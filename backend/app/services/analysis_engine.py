"""Portfolio-aware analysis job runner built around the repository's holdings Skill rules."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..v2_models import AnalysisJob, AnalysisRun, HoldingItem, ModelProfile, PortfolioSnapshot
from .market_data import collect_market_snapshot
from .model_client import ModelCallError, call_model, parse_json_result

logger = logging.getLogger(__name__)

CORE_RULES = """
你是 TradingAgents Holdings Advisor 的服务端分析引擎，面向 A 股和 ETF。
必须遵守：
- 本次确认的持仓快照是当前持仓的唯一真实来源，历史只能用于一致性检查。
- qty 是总持仓，available_qty 是当前可卖数量；减仓/卖出数量不得超过 available_qty。
- qty-available_qty 可能来自挂单、冻结或 T+1，不能推断为已经卖出。
- 亏损是风险输入，不是自动卖出理由。必须结合技术、资金、事件和组合风险。
- 同日或近期建议发生方向反转，必须指出发生了什么实质变化。
- 缺少关键行情时，不得编造触发价和具体数量。
- 输出必须区分事实、推断和风险，并包含失效条件。
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
        .filter(ModelProfile.user_id == user_id, ModelProfile.purpose == purpose, ModelProfile.is_default.is_(True))
        .first()
    )


def _holdings(snapshot: PortfolioSnapshot) -> list[dict[str, Any]]:
    return [
        {
            "code": row.code,
            "name": row.name,
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
    return [
        {
            "run_id": row.id,
            "created_at": row.created_at.isoformat(),
            "summary": row.summary,
            "final_rating": row.final_rating,
            "cash_target": row.cash_target,
            "confidence": row.confidence,
            "holdings": (row.structured_result_json or {}).get("holdings", []),
            "history_consistency": (row.structured_result_json or {}).get("history_consistency"),
        }
        for row in rows
    ]


def _call_json(profile: ModelProfile, system: str, payload: dict[str, Any], instruction: str) -> dict[str, Any]:
    result = call_model(
        profile,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": instruction + "\n\n输入数据：\n" + json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        json_mode=True,
    )
    return parse_json_result(result)


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


def _normalize_final(result: dict[str, Any], holdings: list[dict[str, Any]], quality_grade: str) -> dict[str, Any]:
    result.setdefault("data_quality_grade", quality_grade)
    result.setdefault("market_read", "")
    result.setdefault("portfolio_conclusion", "")
    result.setdefault("final_rating", "watch_only")
    result.setdefault("cash_target", "未给出")
    result.setdefault("confidence", "low")
    result.setdefault("holdings", [])
    result.setdefault("candidates", [])
    result.setdefault("history_consistency", "")
    result.setdefault("bull_case", [])
    result.setdefault("bear_case", [])
    result.setdefault("unresolved_claims", [])
    result.setdefault("risk_warnings", [])
    result.setdefault("evidence", [])
    by_code = {item["code"]: item for item in holdings}
    output_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in result.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if code not in by_code or code in seen:
            continue
        seen.add(code)
        source = by_code[code]
        row["name"] = row.get("name") or source.get("name")
        row["max_sellable_qty"] = source.get("available_qty")
        if str(row.get("action", "")).lower() in {"reduce", "sell"} and source.get("available_qty") in (None, 0):
            row["action"] = "watch"
            row["quantity"] = None
            row["reason"] = (str(row.get("reason") or "") + " 当前无可卖数量，动作降级为观察。").strip()
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
        "| 标的 | 操作 | 条件/触发 | 数量 | 关键原因 | 风险/失效 |",
        "|---|---|---|---:|---|---|",
    ]
    for row in result.get("holdings", []):
        name = f"{row.get('name') or ''}（{row.get('code') or ''}）"
        risk = row.get("risk") or row.get("stop_loss") or "-"
        lines.append(
            f"| {name} | {row.get('action') or '-'} | {row.get('trigger') or '-'} | {row.get('quantity') or '-'} | "
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
    lines.extend([f"- {item}" for item in result.get("evidence", [])] or [f"- {item}" for item in market.get("source_chain", [])])
    lines.extend(
        [
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
        codes = [item["code"] for item in snapshot["holdings"]]

        _job_stage(db, job, "context_loading", 10)
        history = _history(db, job)
        _job_stage(db, job, "market_collecting", 22)
        market = collect_market_snapshot(codes)

        if market.get("quality_grade") == "F":
            final = _blocked_result(snapshot, market)
            final_profile = None
        else:
            quick_profile = _profile(db, job.user_id, "analysis")
            deep_profile = _profile(db, job.user_id, "deep_analysis") or quick_profile
            if quick_profile is None and deep_profile is None:
                raise RuntimeError("default_analysis_model_not_configured")
            input_payload = {"snapshot": snapshot, "market": market, "recent_history": history, "checkpoint": job.checkpoint}
            evidence: dict[str, Any] = {}
            debate: dict[str, Any] = {}
            if job.mode == "deep":
                _job_stage(db, job, "analysts_running", 38)
                evidence = _call_json(
                    quick_profile or deep_profile,
                    CORE_RULES,
                    input_payload,
                    "从市场、技术、资金可用性、组合集中度和历史一致性五个角度形成证据包。输出 JSON："
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
            _job_stage(db, job, "portfolio_synthesis", 72)
            final_profile = deep_profile or quick_profile
            final = _call_json(
                final_profile,
                CORE_RULES,
                {"input": input_payload, "evidence_pack": evidence, "debate": debate, "required_schema": FINAL_SCHEMA},
                "生成可执行但风险受控的最终组合结论。严格按 required_schema 返回 JSON；每个当前持仓都必须出现。",
            )
            if debate:
                final.setdefault("bull_case", debate.get("bull_case", []))
                final.setdefault("bear_case", debate.get("bear_case", []))
                final.setdefault("unresolved_claims", debate.get("unresolved_claims", []))
            final = _normalize_final(final, snapshot["holdings"], market.get("quality_grade", "C"))

        _job_stage(db, job, "report_rendering", 88)
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
            structured_result_json={"result": final, "market_snapshot": market, "input_snapshot": snapshot, "history_used": history},
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
