"""Portfolio-aware analysis job runner built around the holdings Skill rules."""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..v2_models import AnalysisJob, AnalysisRun, ModelProfile, PortfolioSnapshot
from .market_data import collect_market_snapshot, normalize_code, refresh_snapshot_quotes
from .model_client import call_model, parse_json_result

logger = logging.getLogger(__name__)

CORE_RULES = """
你是 TradingAgents Holdings Advisor 的服务端分析引擎，面向 A 股和 ETF。
必须遵守：
- 本次确认的持仓快照是当前持仓的唯一真实来源，历史只能用于一致性检查。
- qty 是总持仓，available_qty 是当前可卖数量；减仓/卖出数量不得超过 available_qty。
- qty-available_qty 可能来自挂单、冻结或 T+1，不能推断为已经卖出。
- 亏损是风险输入，不是自动卖出理由，必须结合技术、资金、事件和组合风险。
- 同日或近期建议发生方向反转，必须指出发生了什么实质变化。
- 缺少关键行情时，不得编造触发价和具体数量。
- 事实、推断、风险和失效条件必须区分。
- 这是研究辅助，不承诺收益，不执行交易。
""".strip()

FINAL_SCHEMA = {
    "data_quality_grade": "A/B/C/D/F",
    "market_read": "市场概览",
    "portfolio_conclusion": "组合级结论",
    "final_rating": "add/hold/reduce/sell/rotate/watch_only",
    "cash_target": "建议现金区间",
    "confidence": "high/medium/low",
    "holdings": [
        {
            "code": "证券代码",
            "name": "名称",
            "action": "add/hold/reduce/sell/watch",
            "reason": "证据与原因",
            "trigger": "条件或价格",
            "quantity": "数量或比例；卖出不得超过 available_qty",
            "max_sellable_qty": 0,
            "stop_loss": "止损/失效条件",
            "take_profit": "止盈/观察条件",
            "risk": "主要风险",
        }
    ],
    "candidates": [
        {
            "code": "代码",
            "name": "名称",
            "action": "new_position/add_existing/rotation_watch",
            "reason": "原因",
            "trigger": "触发条件",
            "initial_size": "初始仓位",
            "stop_loss": "止损",
        }
    ],
    "history_consistency": "与最近几次建议和持仓变化的关系",
    "bull_case": ["多头证据"],
    "bear_case": ["空头证据"],
    "unresolved_claims": ["未解决问题"],
    "risk_warnings": ["风险"],
    "evidence": ["关键数据证据及来源"],
}


def _job_stage(db: Session, job: AnalysisJob, stage: str, progress: int) -> None:
    db.refresh(job)
    if job.status == "cancelled":
        raise RuntimeError("job_cancelled")
    job.current_stage = stage
    job.progress_percent = progress
    db.commit()


def _profile(db: Session, user_id: int, purpose: str) -> ModelProfile | None:
    return (
        db.query(ModelProfile)
        .filter(
            ModelProfile.user_id == user_id,
            ModelProfile.purpose == purpose,
            ModelProfile.is_default.is_(True),
        )
        .first()
    )


def _holdings(snapshot: PortfolioSnapshot) -> list[dict[str, Any]]:
    return [
        {
            "code": row.code,
            "name": row.name,
            "market": row.market,
            "qty": row.qty,
            "available_qty": row.available_qty,
            "unavailable_qty": row.unavailable_qty,
            "cost": row.cost,
            "screenshot_price": row.screenshot_price,
            "market_value": row.market_value,
            "pnl": row.pnl_ratio,
            "pnl_amount": row.pnl_amount,
            "weight": row.weight,
        }
        for row in snapshot.holdings
    ]


def _history(db: Session, job: AnalysisJob) -> list[dict[str, Any]]:
    rows = (
        db.query(AnalysisRun)
        .join(AnalysisJob, AnalysisRun.job_id == AnalysisJob.id)
        .filter(AnalysisRun.user_id == job.user_id, AnalysisJob.portfolio_id == job.portfolio_id)
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(settings.ANALYSIS_HISTORY_LIMIT)
        .all()
    )
    history: list[dict[str, Any]] = []
    for row in rows:
        result = (row.structured_result_json or {}).get("result", {})
        history.append(
            {
                "run_id": row.id,
                "created_at": row.created_at.isoformat(),
                "summary": row.summary,
                "final_rating": row.final_rating,
                "cash_target": row.cash_target,
                "confidence": row.confidence,
                "holdings": result.get("holdings", []),
                "history_consistency": result.get("history_consistency"),
            }
        )
    return history


def _call_json(profile: ModelProfile, system: str, payload: dict[str, Any], instruction: str) -> dict[str, Any]:
    response = call_model(
        profile,
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": instruction + "\n\n输入数据：\n" + json.dumps(payload, ensure_ascii=False, default=str),
            },
        ],
        json_mode=True,
    )
    return parse_json_result(response)


def _resolve_missing_codes(profile: ModelProfile, holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing = [
        {"index": index, "name": item.get("name"), "market": item.get("market")}
        for index, item in enumerate(holdings)
        if not normalize_code(item.get("code") or "")
    ]
    if not missing:
        return holdings

    result = _call_json(
        profile,
        "你负责根据证券名称匹配 A 股、场内 ETF 或基金的证券代码。无法唯一确定时必须返回 null，不得猜测。",
        {"holdings": missing},
        "为每个输入项匹配六位证券代码。名称可能是券商显示的简称。"
        "输出 JSON：{\"matches\":[{\"index\":0,\"code\":\"六位代码或null\","
        "\"confidence\":\"high/medium/low\",\"reason\":\"匹配依据\"}]}。"
        "只有能够唯一确定时才返回代码。",
    )
    matches = result.get("matches") if isinstance(result, dict) else None
    if not isinstance(matches, list):
        return holdings

    for match in matches:
        if not isinstance(match, dict):
            continue
        try:
            index = int(match.get("index"))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(holdings) or holdings[index].get("code"):
            continue
        code = normalize_code(str(match.get("code") or ""))
        if len(code) != 6 or not code.isdigit():
            continue
        holdings[index]["code"] = code
        holdings[index]["code_source"] = "model_match"
        holdings[index]["code_match_confidence"] = match.get("confidence")
    return holdings


def _blocked_result(snapshot: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_quality_grade": "F",
        "market_read": "关键实时行情缺失，质量门控未通过。",
        "portfolio_conclusion": "仅保留观察，不给出具体买卖指令。",
        "final_rating": "watch_only",
        "cash_target": "保持现状",
        "confidence": "low",
        "holdings": [
            {
                "code": item["code"],
                "name": item.get("name"),
                "action": "watch",
                "reason": "缺少可验证的实时行情，暂不生成交易动作。",
                "trigger": None,
                "quantity": None,
                "max_sellable_qty": item.get("available_qty"),
                "stop_loss": None,
                "take_profit": None,
                "risk": "数据不足",
            }
            for item in snapshot["holdings"]
        ],
        "candidates": [],
        "history_consistency": "未改变历史方向，等待数据恢复后重新分析。",
        "bull_case": [],
        "bear_case": ["关键行情数据缺失"],
        "unresolved_claims": market.get("errors", []),
        "risk_warnings": ["质量门控阻断具体交易建议"],
        "evidence": market.get("source_chain", []),
    }


def _numeric_quantity(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or "%" in value:
        return None
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:股|份)?\s*", value)
    return float(match.group(1)) if match else None


def _normalize_final(result: dict[str, Any], holdings: list[dict[str, Any]], quality_grade: str) -> dict[str, Any]:
    defaults = {
        "data_quality_grade": quality_grade,
        "market_read": "",
        "portfolio_conclusion": "",
        "final_rating": "watch_only",
        "cash_target": "未给出",
        "confidence": "low",
        "holdings": [],
        "candidates": [],
        "history_consistency": "",
        "bull_case": [],
        "bear_case": [],
        "unresolved_claims": [],
        "risk_warnings": [],
        "evidence": [],
    }
    for key, value in defaults.items():
        result.setdefault(key, value)

    by_code = {item["code"]: item for item in holdings}
    output_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in result.get("holdings") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        code = str(row.get("code") or "").strip()
        if code not in by_code or code in seen:
            continue
        seen.add(code)
        source = by_code[code]
        available = source.get("available_qty")
        row["name"] = row.get("name") or source.get("name")
        row["max_sellable_qty"] = available
        action = str(row.get("action") or "watch").lower()
        if action in {"reduce", "sell"}:
            if available in (None, 0):
                row["action"] = "watch"
                row["quantity"] = None
                row["reason"] = (str(row.get("reason") or "") + " 当前无可卖数量，动作降级为观察。").strip()
            else:
                numeric = _numeric_quantity(row.get("quantity"))
                if numeric is not None and numeric > float(available):
                    row["quantity"] = str(available)
                    row["reason"] = (str(row.get("reason") or "") + " 卖出数量已按当前可用数量上限修正。").strip()
        output_rows.append(row)

    for code, source in by_code.items():
        if code not in seen:
            output_rows.append(
                {
                    "code": code,
                    "name": source.get("name"),
                    "action": "watch",
                    "reason": "模型未返回该持仓的明确结论。",
                    "trigger": None,
                    "quantity": None,
                    "max_sellable_qty": source.get("available_qty"),
                    "stop_loss": None,
                    "take_profit": None,
                    "risk": "结论缺失",
                }
            )
    result["holdings"] = output_rows

    holding_actions = {row["code"]: str(row.get("action") or "watch").lower() for row in output_rows}
    filtered_candidates: list[dict[str, Any]] = []
    for candidate in result.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        code = str(candidate.get("code") or "").strip()
        if code in holding_actions and holding_actions[code] not in {"add"}:
            result["risk_warnings"].append(
                f"候选 {code} 与当前持仓动作冲突，已从买入候选中移除。"
            )
            continue
        filtered_candidates.append(candidate)
    result["candidates"] = filtered_candidates
    return result


def render_markdown(result: dict[str, Any], market: dict[str, Any], snapshot: dict[str, Any], job: AnalysisJob) -> str:
    lines = [
        f"# {job.checkpoint or '即时'} 持仓分析",
        "",
        f"> 数据质量：**{result.get('data_quality_grade', '-')}** · 置信度：**{result.get('confidence', '-')}** · 任务 #{job.id}",
        "",
        "## 组合结论",
        "",
        result.get("portfolio_conclusion") or "暂无",
        "",
        f"- 组合方向：`{result.get('final_rating', '-')}`",
        f"- 现金目标：{result.get('cash_target', '-')}",
        "",
        "## 市场概览",
        "",
        result.get("market_read") or "暂无",
        "",
        "## 今日持仓操作",
        "",
        "| 标的 | 操作 | 条件/触发 | 数量 | 最大可卖 | 关键原因 | 风险/失效 |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for row in result.get("holdings", []):
        name = f"{row.get('name') or ''}（{row.get('code') or ''}）"
        risk = row.get("risk") or row.get("stop_loss") or "-"
        lines.append(
            f"| {name} | {row.get('action') or '-'} | {row.get('trigger') or '-'} | {row.get('quantity') or '-'} | "
            f"{row.get('max_sellable_qty') if row.get('max_sellable_qty') is not None else '-'} | "
            f"{str(row.get('reason') or '-').replace('|', '｜')} | {str(risk).replace('|', '｜')} |"
        )

    lines.extend(["", "## 买入与轮动候选", ""])
    candidates = result.get("candidates") or []
    if candidates:
        for item in candidates:
            lines.append(
                f"- **{item.get('name') or ''}（{item.get('code') or ''}）**：{item.get('action') or '观察'}；"
                f"触发：{item.get('trigger') or '-'}；仓位：{item.get('initial_size') or '-'}；原因：{item.get('reason') or '-'}"
            )
    else:
        lines.append("- 当前没有通过风险门控的新增候选。")

    lines.extend(["", "## 历史一致性", "", result.get("history_consistency") or "暂无历史上下文。"])
    lines.extend(["", "## 多空证据", "", "### 多头", ""])
    lines.extend([f"- {item}" for item in result.get("bull_case", [])] or ["- 暂无"])
    lines.extend(["", "### 空头", ""])
    lines.extend([f"- {item}" for item in result.get("bear_case", [])] or ["- 暂无"])
    lines.extend(["", "## 风险与未解决问题", ""])
    warnings = list(result.get("risk_warnings", [])) + list(result.get("unresolved_claims", []))
    lines.extend([f"- {item}" for item in warnings] or ["- 暂无"])
    lines.extend(["", "## 数据证据", ""])
    lines.extend(
        [f"- {item}" for item in result.get("evidence", [])]
        or [f"- {item}" for item in market.get("source_chain", [])]
    )
    lines.extend(
        [
            "",
            f"- 最终行情刷新：{market.get('final_quote_refresh_status', '未执行')} / {market.get('final_quote_refresh_at', '-')}",
            "",
            "## 持仓资金摘要",
            "",
            f"- 总资产：{snapshot.get('total_assets')}",
            f"- 持仓市值：{snapshot.get('total_market_value')}",
            f"- 修正后未使用资金：{snapshot.get('corrected_unused_funds')}",
            "",
            "> 本报告仅用于研究辅助，不构成投资建议。交易前请核对实时价格、可用数量和个人风险承受能力。",
        ]
    )
    return "\n".join(lines)


def run_analysis_job(job_id: int) -> None:
    db = SessionLocal()
    job: AnalysisJob | None = None
    try:
        job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if job is None or job.status not in {"queued", "retrying"}:
            return
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job.error_code = None
        job.error_message = None
        db.commit()

        snapshot_row = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.id == job.snapshot_id).first()
        if snapshot_row is None or snapshot_row.status != "confirmed":
            raise RuntimeError("confirmed_snapshot_not_found")
        snapshot = {
            "id": snapshot_row.id,
            "snapshot_time": snapshot_row.snapshot_time.isoformat(),
            "total_assets": snapshot_row.total_assets,
            "total_market_value": snapshot_row.total_market_value,
            "broker_available_cash": snapshot_row.broker_available_cash,
            "corrected_unused_funds": snapshot_row.corrected_unused_funds,
            "repo_or_standard_bond_value": snapshot_row.repo_or_standard_bond_value,
            "holdings": _holdings(snapshot_row),
        }

        _job_stage(db, job, "context_loading", 10)
        history = _history(db, job)
        quick_profile = _profile(db, job.user_id, "analysis")
        deep_profile = _profile(db, job.user_id, "deep_analysis") or quick_profile
        if any(not normalize_code(item.get("code") or "") for item in snapshot["holdings"]):
            resolution_profile = quick_profile or deep_profile
            if resolution_profile is None:
                raise RuntimeError("default_analysis_model_not_configured")
            _job_stage(db, job, "symbol_resolving", 18)
            snapshot["holdings"] = _resolve_missing_codes(resolution_profile, snapshot["holdings"])
        codes = [item["code"] for item in snapshot["holdings"] if item.get("code")]
        _job_stage(db, job, "market_collecting", 22)
        market = collect_market_snapshot(codes)

        if market.get("quality_grade") == "F":
            final = _blocked_result(snapshot, market)
            final_profile = None
            market = refresh_snapshot_quotes(market, codes)
        else:
            if quick_profile is None and deep_profile is None:
                raise RuntimeError("default_analysis_model_not_configured")

            input_payload = {
                "snapshot": snapshot,
                "market": market,
                "recent_history": history,
                "checkpoint": job.checkpoint,
            }
            evidence: dict[str, Any] = {}
            debate: dict[str, Any] = {}
            if job.mode == "deep":
                _job_stage(db, job, "analysts_running", 38)
                evidence = _call_json(
                    quick_profile or deep_profile,
                    CORE_RULES,
                    input_payload,
                    "从行情、技术、主力资金、近期公告、资金可用性、组合集中度和历史一致性形成证据包。"
                    "输出 JSON："
                    '{"market_read":"", "holding_evidence":[], "portfolio_risks":[], "data_gaps":[], "quality_grade":"A-F"}',
                )
                _job_stage(db, job, "investment_debate", 55)
                debate = _call_json(
                    quick_profile or deep_profile,
                    CORE_RULES,
                    {"input": input_payload, "evidence": evidence},
                    "进行 Claim 驱动的多空辩论。输出 JSON："
                    '{"bull_case":[], "bear_case":[], "resolved_claims":[], "unresolved_claims":[], "manager_verdict":""}',
                )

            _job_stage(db, job, "final_quote_refresh", 68)
            market = refresh_snapshot_quotes(market, codes)
            input_payload["market"] = market
            _job_stage(db, job, "portfolio_synthesis", 76)
            final_profile = deep_profile or quick_profile
            final = _call_json(
                final_profile,
                CORE_RULES,
                {
                    "input": input_payload,
                    "evidence_pack": evidence,
                    "debate": debate,
                    "required_schema": FINAL_SCHEMA,
                },
                "基于最终刷新后的行情生成风险受控的组合结论。严格按 required_schema 返回 JSON；"
                "每个当前持仓都必须出现。",
            )
            if debate:
                final.setdefault("bull_case", debate.get("bull_case", []))
                final.setdefault("bear_case", debate.get("bear_case", []))
                final.setdefault("unresolved_claims", debate.get("unresolved_claims", []))
            final = _normalize_final(final, snapshot["holdings"], market.get("quality_grade", "C"))

        _job_stage(db, job, "report_rendering", 90)
        markdown = render_markdown(final, market, snapshot, job)
        run = AnalysisRun(
            job_id=job.id,
            user_id=job.user_id,
            portfolio_snapshot_id=job.snapshot_id,
            model_profile_id=final_profile.id if final_profile else None,
            data_quality_grade=final.get("data_quality_grade"),
            summary=final.get("portfolio_conclusion"),
            final_rating=final.get("final_rating"),
            cash_target=final.get("cash_target"),
            confidence=final.get("confidence"),
            structured_result_json={
                "result": final,
                "market_snapshot": market,
                "input_snapshot": snapshot,
                "history_used": history,
            },
            markdown_text=markdown,
        )
        db.add(run)
        job.status = "succeeded"
        job.current_stage = "completed"
        job.progress_percent = 100
        job.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(run)

        if job.notify:
            try:
                from .notifications import send_run_notifications

                send_run_notifications(db, run)
                db.commit()
            except Exception:
                logger.exception("Notification failed for analysis run %s", run.id)
    except Exception as exc:
        logger.exception("Analysis job %s failed", job_id)
        if job is not None:
            db.rollback()
            job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
            if job is not None:
                if str(exc) == "job_cancelled":
                    job.status = "cancelled"
                    job.current_stage = "cancelled"
                else:
                    job.status = "failed"
                    job.current_stage = "failed"
                    job.error_code = type(exc).__name__
                    job.error_message = str(exc)[:3000]
                job.finished_at = datetime.now(UTC)
                db.commit()
    finally:
        db.close()
