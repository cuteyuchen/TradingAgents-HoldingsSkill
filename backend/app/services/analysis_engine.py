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
from .skill_runtime import runtime_prompt

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

# Keep the model-facing schema explicit.  The frontend can render the report even
# when one provider returns a partial JSON object because every phase is
# normalised before it is persisted.
CLAIM_SCHEMA = {
    "claim_id": "INV-1 or RISK-1",
    "speaker": "bull/bear/aggressive/neutral/conservative",
    "stance": "bullish/bearish/risk_accept/risk_balance/risk_avoid",
    "claim": "一句话具体论点",
    "evidence": ["最多三条可核验证据"],
    "confidence": 0.0,
    "status": "open/addressed/resolved/unresolved",
    "target_claim_ids": [],
}

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
    "today_actions": [],
    "investment_debate_state": {},
    "research_manager_verdict": {},
    "trader_proposal": {},
    "risk_revision": {},
    "risk_debate_state": {},
    "portfolio_manager_final": {},
    "buy_candidates": [],
    "hot_sectors": [],
    "rebalance_plan": {},
    "checkpoint_plan": "",
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


def _required_call_json(
    profile: ModelProfile | None,
    system: str,
    payload: dict[str, Any],
    instruction: str,
    phase_name: str,
) -> dict[str, Any]:
    """Run a required Skill phase and reject incomplete provider output."""
    if profile is None:
        raise RuntimeError(f"{phase_name}_model_not_configured")
    result = _call_json(profile, system, payload, instruction)
    if not isinstance(result, dict) or not result:
        raise RuntimeError(f"{phase_name}_empty_result")
    return result


def _quality_rank(grade: Any) -> int:
    return {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}.get(str(grade or "F").upper(), 4)


def _worst_grade(*grades: Any) -> str:
    values = [str(grade or "F").upper() for grade in grades]
    return max(values, key=_quality_rank) if values else "F"


def _quality_gate(snapshot: dict[str, Any], market: dict[str, Any], evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply the runtime's hard checks before allowing action advice."""
    holdings = snapshot.get("holdings") or []
    quotes = market.get("quotes") or {}
    missing: list[str] = []
    coded_holdings = [item for item in holdings if item.get("code")]
    complete_quote_coverage = all((quotes.get(item.get("code"), {}) or {}).get("price") is not None for item in coded_holdings)
    collector_asserts_coverage = str(market.get("quality_grade") or "F").upper() in {"A", "B"} and not any(
        str(error).startswith("quote") for error in market.get("errors") or []
    )
    checks = {
        "confirmed_holdings": bool(holdings),
        "instrument_code": all(bool(normalize_code(item.get("code") or "")) for item in holdings),
        "quote_coverage": bool(coded_holdings) and (complete_quote_coverage or collector_asserts_coverage),
        "available_quantity_semantics": all("available_qty" in item for item in holdings),
    }
    for key, passed in checks.items():
        if not passed:
            missing.append(key)
    market_grade = market.get("quality_grade") or "F"
    evidence_grade = (evidence or {}).get("quality_grade") or (evidence or {}).get("data_quality_grade")
    grade = _worst_grade(market_grade, evidence_grade or market_grade)
    # Missing holdings, codes, or quote coverage is a hard block regardless of
    # a provider's optimistic self-assessment.
    if any(key in missing for key in ("confirmed_holdings", "instrument_code", "quote_coverage")):
        grade = "F" if "quote_coverage" in missing or "confirmed_holdings" in missing else "D"
    return {
        "grade": grade,
        "status": "blocked" if grade in {"D", "F"} else "pass",
        "mandatory_checks": checks,
        "missing_fields": missing,
        "market_grade": market_grade,
        "evidence_grade": evidence_grade,
        "action_bias": "watch_only" if grade in {"C", "D", "F"} else "normal",
    }


def _claim_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("claim") or value.get("text") or value.get("reason") or "").strip()
    return str(value or "").strip()


def _normalise_claim(
    raw: Any,
    claim_id: str,
    speaker: str,
    stance: str,
    *,
    target_claim_ids: list[str] | None = None,
    default_status: str = "open",
) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    evidence = source.get("evidence") if isinstance(source.get("evidence"), list) else []
    evidence = [str(item) for item in evidence if str(item).strip()][:3]
    text = _claim_text(raw) or "该阶段未返回具体论点"
    try:
        confidence = float(source.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    status = str(source.get("status") or default_status).lower()
    if status not in {"open", "addressed", "resolved", "unresolved"}:
        status = default_status
    return {
        "claim_id": claim_id,
        "speaker": str(source.get("speaker") or speaker),
        "stance": str(source.get("stance") or stance),
        "claim": text,
        "evidence": evidence,
        "confidence": confidence,
        "status": status,
        "target_claim_ids": list(source.get("target_claim_ids") or target_claim_ids or []),
    }


def _normalise_investment_debate(debate: dict[str, Any], evidence: dict[str, Any], holdings: list[dict[str, Any]]) -> dict[str, Any]:
    state = debate.get("investment_debate_state") if isinstance(debate.get("investment_debate_state"), dict) else debate
    raw_bull = state.get("bull_claims") or debate.get("bull_claims") or debate.get("bull_case") or []
    raw_bear = state.get("bear_claims") or debate.get("bear_claims") or debate.get("bear_case") or []
    if not isinstance(raw_bull, list):
        raw_bull = [raw_bull]
    if not isinstance(raw_bear, list):
        raw_bear = [raw_bear]
    # Always retain at least one claim per side so the transcript does not hide
    # uncertainty when the provider returns a summary-only response.
    if not raw_bull:
        raw_bull = [(evidence.get("market_read") or "未返回多头证据")]
    if not raw_bear:
        raw_bear = [(evidence.get("data_gaps") or ["未返回空头证据"])[0] if isinstance(evidence.get("data_gaps"), list) else "未返回空头证据"]
    while len(raw_bull) < 2:
        raw_bull.append("未获得第二条可核验多头证据，保持低置信度")
    while len(raw_bear) < 2:
        raw_bear.append("未获得第二条可核验空头证据，列为未解决风险")
    bull_claims = [_normalise_claim(item, f"INV-{index * 2 + 1}", "bull", "bullish") for index, item in enumerate(raw_bull[:3])]
    bear_claims = [_normalise_claim(item, f"INV-{index * 2 + 2}", "bear", "bearish") for index, item in enumerate(raw_bear[:3])]
    claims = bull_claims + bear_claims
    unresolved_ids = list(state.get("unresolved_claim_ids") or debate.get("unresolved_claim_ids") or [])
    unresolved_ids.extend(claim["claim_id"] for claim in claims if claim["status"] in {"open", "unresolved"})
    unresolved_ids = list(dict.fromkeys(str(item) for item in unresolved_ids if str(item).startswith("INV-")))
    rounds = state.get("round_summaries") or debate.get("round_summaries") or []
    if not isinstance(rounds, list):
        rounds = []
    if not rounds:
        rounds = [
            {"round": 1, "goal": "建立核心论点", "summary": "双方提交基于当前证据的核心论点。"},
            {"round": 2, "goal": "攻防核心论点", "summary": "未解决论点交由研究总监继续权衡。"},
        ]
    manager_verdict = state.get("judge_decision") or debate.get("manager_verdict") or "证据不足，保持观察。"
    return {
        "bull_claims": bull_claims,
        "bear_claims": bear_claims,
        "unresolved_claim_ids": unresolved_ids,
        "round_summaries": rounds,
        "judge_decision": manager_verdict,
        "claim_schema": CLAIM_SCHEMA,
        "holdings_covered": [item.get("code") for item in holdings],
        "bull_case": [claim["claim"] for claim in bull_claims],
        "bear_case": [claim["claim"] for claim in bear_claims],
        "unresolved_claims": [claim["claim"] for claim in claims if claim["claim_id"] in unresolved_ids],
    }


def _normalise_risk_debate(debate: dict[str, Any], holdings: list[dict[str, Any]], quality_gate: dict[str, Any]) -> dict[str, Any]:
    state = debate.get("risk_debate_state") if isinstance(debate.get("risk_debate_state"), dict) else debate
    raw_claims = state.get("claims") or debate.get("claims") or []
    by_speaker: dict[str, Any] = {}
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if isinstance(item, dict):
                speaker = str(item.get("speaker") or "").lower()
                if speaker:
                    by_speaker[speaker] = item
    # RISK-1/2/3 are intentionally stable for archive consumers.
    defaults = {
        "aggressive": ("风险接受", "risk_accept", "若指数和主力资金确认，允许小仓位执行。"),
        "neutral": ("风险平衡", "risk_balance", "仓位和 T+1 可执行性优先于方向判断。"),
        "conservative": ("风险规避", "risk_avoid", "质量门控未通过时不新增买入，先等待确认。"),
    }
    claims = []
    for index, speaker in enumerate(("aggressive", "neutral", "conservative"), start=1):
        stance, stance_code, fallback = defaults[speaker]
        raw = by_speaker.get(speaker) or fallback
        claim = _normalise_claim(raw, f"RISK-{index}", speaker, stance_code)
        claim["claim"] = claim["claim"] or stance
        if quality_gate.get("grade") in {"C", "D", "F"} and speaker == "conservative":
            claim["claim"] = f"数据质量 {quality_gate.get('grade')}，{claim['claim']}"
        claims.append(claim)
    unresolved = list(state.get("unresolved_claim_ids") or debate.get("unresolved_claim_ids") or [])
    unresolved.extend(claim["claim_id"] for claim in claims if claim["status"] in {"open", "unresolved"})
    unresolved = list(dict.fromkeys(item for item in unresolved if str(item).startswith("RISK-")))
    rounds = state.get("round_summaries") or debate.get("round_summaries") or [
        {"round": 1, "goal": "风险取舍", "summary": "激进、中立、保守三方围绕仓位与执行风险给出判断。"}
    ]
    return {
        "aggressive_claims": [claims[0]],
        "neutral_claims": [claims[1]],
        "conservative_claims": [claims[2]],
        "unresolved_claim_ids": unresolved,
        "round_summaries": rounds,
        "judge_decision": state.get("judge_decision") or debate.get("judge_decision") or "以中立方案为默认。",
        "claim_schema": CLAIM_SCHEMA,
    }


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
    quality_gate = _quality_gate(snapshot, market)
    blocked_reason = "；".join(market.get("errors") or []) or "关键行情数据缺失"
    investment = _normalise_investment_debate({}, {"market_read": "", "data_gaps": [blocked_reason]}, snapshot.get("holdings", []))
    risk = _normalise_risk_debate({}, snapshot.get("holdings", []), quality_gate)
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
        "evidence_pack": {
            "market_read": "关键实时行情缺失",
            "data_gaps": market.get("errors", []),
            "source_chain": market.get("source_chain", []),
        },
        "quality_gate": quality_gate,
        "investment_debate_state": investment,
        "research_manager_verdict": {
            "rating": "watch_only",
            "winner": "none",
            "strategic_action": "等待行情和代码确认后重新分析",
            "confidence": "low",
            "unresolved_claim_treatment": market.get("errors", []),
        },
        "trader_proposal": {
            "orders": [],
            "status": "blocked",
            "reason": "质量门控未通过",
            "cancel_all_buys_when": blocked_reason,
        },
        "risk_revision": {
            "decision": "reject",
            "revision_count": 0,
            "hard_constraints": ["缺少可验证行情时不得执行交易"],
            "soft_constraints": [],
            "de_risk_triggers": [],
        },
        "risk_debate_state": risk,
        "portfolio_manager_final": {
            "portfolio_rating": "watch_only",
            "cash_target": "保持现状",
            "risk_decision": "reject",
            "hard_constraints": ["等待行情恢复"],
            "de_risk_triggers": [],
        },
        "hot_sectors": [],
        "buy_candidates": [],
        "today_actions": [],
        "candidate_status": "blocked",
        "candidate_blocked_reason": blocked_reason,
        "rebalance_plan": {"status": "blocked", "reason": blocked_reason},
        "checkpoint_plan": "行情恢复并完成最终刷新后重新执行质量门控。",
    }


def _numeric_quantity(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or "%" in value:
        return None
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:股|份)?\s*", value)
    return float(match.group(1)) if match else None


def _normalize_final(
    result: dict[str, Any],
    holdings: list[dict[str, Any]],
    quality_grade: str,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "hot_sectors": [],
        "rebalance_plan": {},
        "checkpoint_plan": "",
    }
    for key, value in defaults.items():
        result.setdefault(key, value)
    result["data_quality_grade"] = _worst_grade(result.get("data_quality_grade"), quality_grade)
    for key in ("risk_warnings", "unresolved_claims", "bull_case", "bear_case", "evidence", "candidates", "hot_sectors"):
        if not isinstance(result.get(key), list):
            result[key] = [result[key]] if result.get(key) not in (None, "") else []

    workflow = workflow or {}
    for key in (
        "evidence_pack",
        "quality_gate",
        "investment_debate_state",
        "research_manager_verdict",
        "trader_proposal",
        "risk_revision",
        "risk_debate_state",
        "portfolio_manager_final",
    ):
        if workflow.get(key) is not None:
            result[key] = workflow[key]
    if workflow.get("hot_sectors"):
        result["hot_sectors"] = workflow["hot_sectors"]
    if workflow.get("candidate_status"):
        result["candidate_status"] = workflow["candidate_status"]
    if workflow.get("candidate_blocked_reason"):
        result["candidate_blocked_reason"] = workflow["candidate_blocked_reason"]
    phase_errors = workflow.get("phase_errors") or []
    if phase_errors:
        result["phase_errors"] = phase_errors
        result["risk_warnings"].extend(f"分析阶段降级：{item}" for item in phase_errors)

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
        action_aliases = {
            "buy": "add",
            "conditional_buy": "conditional_add",
            "trim": "reduce",
            "wait": "watch",
            "加仓": "add",
            "条件加仓": "conditional_add",
            "持有": "hold",
            "减仓": "reduce",
            "卖出": "sell",
            "观察": "watch",
        }
        action = action_aliases.get(action, action)
        if action not in {"add", "conditional_add", "hold", "reduce", "sell", "watch"}:
            action = "watch"
        row["action"] = action
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
    raw_candidates = workflow.get("candidates") or result.get("buy_candidates") or result.get("candidates") or []
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        row = dict(candidate)
        code = normalize_code(str(row.get("code") or ""))
        row["code"] = code
        candidate_type = str(row.get("candidate_type") or row.get("type") or row.get("action") or "rotation_watch").lower()
        type_aliases = {
            "new": "new_position",
            "buy": "new_position",
            "新开仓": "new_position",
            "add": "add_existing",
            "加仓现有持仓": "add_existing",
            "条件加仓": "conditional_add",
            "watch": "rotation_watch",
            "watch_only": "rotation_watch",
            "轮动观察": "rotation_watch",
        }
        candidate_type = type_aliases.get(candidate_type, candidate_type)
        row["candidate_type"] = candidate_type
        if code in holding_actions and holding_actions[code] not in {"add", "conditional_add"}:
            result["risk_warnings"].append(
                f"候选 {code} 与当前持仓动作冲突，已从买入候选中移除。"
            )
            continue
        if code in holding_actions and candidate_type not in {"add_existing", "conditional_add"}:
            candidate_type = "conditional_add" if holding_actions[code] == "conditional_add" else "add_existing"
            row["candidate_type"] = candidate_type
        reason_detail = row.get("reason_detail") if isinstance(row.get("reason_detail"), dict) else {}
        catalyst = row.get("catalyst") or row.get("news_catalyst") or reason_detail.get("catalyst")
        capital_flow = row.get("capital_flow") or reason_detail.get("capital_flow")
        sector_position = row.get("sector_position") or reason_detail.get("sector_position")
        row["reason_detail"] = {
            "catalyst": catalyst,
            "capital_flow": capital_flow,
            "sector_position": sector_position,
        }
        missing_reason_fields = [
            key
            for key, value in row["reason_detail"].items()
            if value is None or not str(value).strip()
        ]
        if missing_reason_fields:
            row["buyable"] = False
            row["candidate_type"] = "rotation_watch"
            row["gate_status"] = "blocked_missing_evidence"
            row["blocked_reason"] = "缺少候选依据：" + "、".join(missing_reason_fields)
        else:
            try:
                score = float(row.get("score"))
            except (TypeError, ValueError):
                score = None
            row["buyable"] = bool(score is not None and score >= 7 and candidate_type != "rotation_watch")
            row["gate_status"] = "buyable" if row["buyable"] else "watch_only"
        gate_grade = str((result.get("quality_gate") or {}).get("grade") or quality_grade).upper()
        if gate_grade in {"C", "D", "F"}:
            row["buyable"] = False
            row["candidate_type"] = "rotation_watch"
            row["gate_status"] = "watch_only_quality_gate"
            row["blocked_reason"] = f"数据质量 {gate_grade}，新买入被门控阻断"
        filtered_candidates.append(row)
    result["candidates"] = filtered_candidates
    result["buy_candidates"] = filtered_candidates
    result["today_actions"] = output_rows
    result.setdefault("candidate_status", "ready" if filtered_candidates else "none")
    result.setdefault(
        "candidate_blocked_reason",
        None if filtered_candidates else "当前没有同时满足消息面、资金面、板块位置和风险门控的候选。",
    )

    investment = result.get("investment_debate_state") or {}
    if investment:
        result["bull_case"] = investment.get("bull_case") or result.get("bull_case") or []
        result["bear_case"] = investment.get("bear_case") or result.get("bear_case") or []
        result["unresolved_claims"] = investment.get("unresolved_claims") or result.get("unresolved_claims") or []

    portfolio_final = result.get("portfolio_manager_final") or {}
    portfolio_final.setdefault("portfolio_rating", result.get("final_rating"))
    portfolio_final.setdefault("cash_target", result.get("cash_target"))
    portfolio_final.setdefault("risk_decision", (result.get("risk_revision") or {}).get("decision", "pass"))
    portfolio_final.setdefault("final_actions", output_rows)
    result["portfolio_manager_final"] = portfolio_final
    return result


def _md(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return str(value if value not in (None, "") else "-").replace("|", "｜").replace("\n", " ")


def _as_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [] if value in (None, "") else [value]


def _append_claim_table(lines: list[str], claims: list[dict[str, Any]]) -> None:
    lines.extend([
        "| Claim ID | 方 | 论点 | 证据 | 置信度 | 状态 | 目标 Claim |",
        "|---|---|---|---|---:|---|---|",
    ])
    if not claims:
        lines.append("| - | - | 暂无 | - | - | - | - |")
        return
    speaker_names = {"bull": "多头", "bear": "空头", "aggressive": "激进", "neutral": "中立", "conservative": "保守"}
    for claim in claims:
        evidence = "；".join(str(item) for item in _as_items(claim.get("evidence"))) or "-"
        targets = "、".join(str(item) for item in _as_items(claim.get("target_claim_ids"))) or "-"
        lines.append(
            f"| {_md(claim.get('claim_id'))} | {_md(speaker_names.get(claim.get('speaker'), claim.get('speaker')))} | "
            f"{_md(claim.get('claim'))} | {_md(evidence)} | {_md(claim.get('confidence'))} | "
            f"{_md(claim.get('status'))} | {_md(targets)} |"
        )


def render_markdown(result: dict[str, Any], market: dict[str, Any], snapshot: dict[str, Any], job: AnalysisJob) -> str:
    evidence_pack = result.get("evidence_pack") or {}
    quality_gate = result.get("quality_gate") or {}
    investment = result.get("investment_debate_state") or {}
    research = result.get("research_manager_verdict") or {}
    trader = result.get("trader_proposal") or {}
    revision = result.get("risk_revision") or {}
    risk_debate = result.get("risk_debate_state") or {}
    portfolio_final = result.get("portfolio_manager_final") or {}
    lines = [
        f"# {job.checkpoint or '即时'} 持仓分析",
        "",
        f"> 数据质量：**{result.get('data_quality_grade', '-')}** · 置信度：**{result.get('confidence', '-')}** · 任务 #{job.id}",
        "",
        "## 市场概览",
        "",
        str(result.get("market_read") or "暂无"),
        "",
        "## 组合结论",
        "",
        str(result.get("portfolio_conclusion") or "暂无"),
        "",
        f"- 组合方向：`{result.get('final_rating', '-')}`",
        f"- 现金目标：{result.get('cash_target', '-')}",
        "",
        "## 证据包",
        "",
        f"- 持仓来源：确认后的当前持仓快照 #{snapshot.get('id', '-')}（历史仅用于一致性检查）",
        f"- 当前检查点：{job.checkpoint or '即时'}",
        f"- 快照时间：{snapshot.get('snapshot_time', '-')}；行情采集：{market.get('captured_at', '-')} ",
        f"- 数据质量：{quality_gate.get('grade') or result.get('data_quality_grade', '-')}；缺失项：{_md(quality_gate.get('missing_fields') or evidence_pack.get('data_gaps') or '无')}",
        f"- 行情来源链：{_md(market.get('source_chain') or result.get('evidence') or '-')}",
        f"- 历史一致性：{_md(result.get('history_consistency') or '首次分析或无可用历史')}",
        "",
        "## 质量门控",
        "",
        f"- 综合评级：**{quality_gate.get('grade') or result.get('data_quality_grade', '-')}**；状态：`{quality_gate.get('status', '-')}`；动作偏置：`{quality_gate.get('action_bias', '-')}`",
        "",
        "| 硬检查 | 结果 |",
        "|---|---|",
    ]
    checks = quality_gate.get("mandatory_checks") or {}
    if checks:
        lines.extend(f"| {_md(name)} | {'通过' if passed else '不通过'} |" for name, passed in checks.items())
    else:
        lines.append("| 未提供 | 不通过 |")

    lines.extend(["", "## 多空辩论", ""])
    for round_item in _as_items(investment.get("round_summaries")):
        if isinstance(round_item, dict):
            lines.append(f"### Round {round_item.get('round', '-')} · {_md(round_item.get('goal'))}")
            lines.append("")
            lines.append(str(round_item.get("summary") or ""))
            lines.append("")
    _append_claim_table(lines, _as_items(investment.get("bull_claims")) + _as_items(investment.get("bear_claims")))
    lines.extend(["", "**未解决投资论点**", ""])
    unresolved_ids = _as_items(investment.get("unresolved_claim_ids"))
    lines.extend([f"- {_md(item)}" for item in unresolved_ids] or ["- 无"])

    lines.extend([
        "",
        "## 研究总监裁决",
        "",
        f"- 评级：{_md(research.get('rating') or research.get('final_rating') or investment.get('judge_decision'))}",
        f"- 胜出方：{_md(research.get('winner'))}",
        f"- 未解决论点处理：{_md(research.get('unresolved_claim_treatment'))}",
        f"- 战略行动：{_md(research.get('strategic_action') or research.get('action'))}",
        f"- 置信度：{_md(research.get('confidence'))}",
        "",
        "## 交易员方案",
        "",
        "| 标的 | 动作 | 触发 | 数量/比例 | 止盈 | 止损 | 失效条件 | 检查点规则 |",
        "|---|---|---|---:|---|---|---|---|",
    ])
    orders = _as_items(trader.get("orders") or trader.get("proposals"))
    if not orders:
        # The normalised final rows are the authoritative fallback when a model
        # returned no separate trader payload.
        orders = result.get("holdings") or []
    for row in orders:
        if not isinstance(row, dict):
            continue
        instrument = f"{row.get('name') or ''}（{row.get('code') or ''}）"
        lines.append(
            f"| {_md(instrument)} | {_md(row.get('action'))} | {_md(row.get('trigger') or row.get('entry_trigger'))} | "
            f"{_md(row.get('quantity') or row.get('size'))} | {_md(row.get('take_profit'))} | {_md(row.get('stop_loss'))} | "
            f"{_md(row.get('invalidation') or row.get('invalidating_condition'))} | {_md(row.get('checkpoint_rule') or trader.get('checkpoint_rule'))} |"
        )

    lines.extend([
        "",
        "## 风控修正循环",
        "",
        f"- 裁决：`{revision.get('decision', 'pass')}`；修正次数：{revision.get('revision_count', 0)}",
        f"- 修正原因：{_md(revision.get('reason') or revision.get('reasons'))}",
        f"- 硬性约束：{_md(revision.get('hard_constraints'))}",
        f"- 建议约束：{_md(revision.get('soft_constraints'))}",
        f"- 去风险触发器：{_md(revision.get('de_risk_triggers'))}",
        "",
        "## 三方风控辩论",
        "",
    ])
    risk_claims = (
        _as_items(risk_debate.get("aggressive_claims"))
        + _as_items(risk_debate.get("neutral_claims"))
        + _as_items(risk_debate.get("conservative_claims"))
    )
    _append_claim_table(lines, risk_claims)
    lines.extend(["", "**未解决风控论点**", ""])
    lines.extend([f"- {_md(item)}" for item in _as_items(risk_debate.get("unresolved_claim_ids"))] or ["- 无"])

    lines.extend([
        "",
        "## 组合经理最终决策",
        "",
        f"- 组合评级：{_md(portfolio_final.get('portfolio_rating') or result.get('final_rating'))}",
        f"- 现金目标：{_md(portfolio_final.get('cash_target') or result.get('cash_target'))}",
        f"- 风控裁决：`{portfolio_final.get('risk_decision', revision.get('decision', 'pass'))}`",
        f"- 硬性约束：{_md(portfolio_final.get('hard_constraints') or revision.get('hard_constraints'))}",
        f"- 去风险触发器：{_md(portfolio_final.get('de_risk_triggers') or revision.get('de_risk_triggers'))}",
        "",
        "## 今日持仓操作",
        "",
        "| 标的 | 操作 | 条件/触发 | 数量 | 最大可卖 | 关键原因 | 风险/失效 |",
        "|---|---|---|---:|---:|---|---|",
    ])
    for row in result.get("holdings", []):
        name = f"{row.get('name') or ''}（{row.get('code') or ''}）"
        risk = row.get("risk") or row.get("stop_loss") or "-"
        lines.append(
            f"| {_md(name)} | {_md(row.get('action'))} | {_md(row.get('trigger'))} | {_md(row.get('quantity'))} | "
            f"{_md(row.get('max_sellable_qty'))} | {_md(row.get('reason'))} | {_md(risk)} |"
        )

    lines.extend([
        "",
        "## 今日买入/轮动候选",
        "",
        "| 候选 | 类型 | 消息面/催化 | 资金面 | 板块位置 | 入场条件 | 仓位 | 止盈1 | 止盈2 | 止损 | 取消条件 | 评分 | 门控 |",
        "|---|---|---|---|---|---|---:|---|---|---|---|---:|---|",
    ])
    candidates = result.get("buy_candidates") or result.get("candidates") or []
    if candidates:
        for item in candidates:
            reason = item.get("reason_detail") or {}
            take_profit = item.get("take_profit")
            if isinstance(take_profit, list):
                tp1 = take_profit[0] if take_profit else None
                tp2 = take_profit[1] if len(take_profit) > 1 else None
            else:
                tp1 = item.get("take_profit_1") or take_profit
                tp2 = item.get("take_profit_2")
            instrument = f"{item.get('name') or ''}（{item.get('code') or ''}）"
            lines.append(
                f"| {_md(instrument)} | {_md(item.get('candidate_type'))} | "
                f"{_md(reason.get('catalyst'))} | {_md(reason.get('capital_flow'))} | {_md(reason.get('sector_position'))} | "
                f"{_md(item.get('trigger') or item.get('entry_trigger'))} | {_md(item.get('initial_size'))} | {_md(tp1)} | {_md(tp2)} | "
                f"{_md(item.get('stop_loss'))} | {_md(item.get('invalidating_condition') or item.get('cancel_condition'))} | "
                f"{_md(item.get('score'))} | {_md(item.get('gate_status'))} |"
            )
    else:
        lines.append(f"| - | 暂不建议买入 | - | - | - | - | - | - | - | - | {_md(result.get('candidate_blocked_reason'))} | - | blocked |")

    lines.extend([
        "",
        "## 调仓计划",
        "",
        f"- {_md(result.get('rebalance_plan') or '保持现有仓位，等待触发条件。')}",
        "",
        "## 当前检查点计划",
        "",
        str(result.get("checkpoint_plan") or trader.get("checkpoint_rule") or "执行前复核指数、板块、资金流和可用数量。"),
        "",
        "## 历史一致性",
        "",
        str(result.get("history_consistency") or "暂无历史上下文。"),
        "",
        "## 风险与未解决问题",
        "",
    ])
    warnings = list(result.get("risk_warnings", [])) + list(result.get("unresolved_claims", []))
    lines.extend([f"- {_md(item)}" for item in warnings] or ["- 暂无"])
    lines.extend([
        "",
        "## 数据证据",
        "",
    ])
    lines.extend([f"- {_md(item)}" for item in result.get("evidence", [])] or [f"- {_md(item)}" for item in market.get("source_chain", [])])
    lines.extend([
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
    ])
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

        _job_stage(db, job, "context_loading", 8)
        history = _history(db, job)
        quick_profile = _profile(db, job.user_id, "analysis")
        deep_profile = _profile(db, job.user_id, "deep_analysis") or quick_profile
        if any(not normalize_code(item.get("code") or "") for item in snapshot["holdings"]):
            resolution_profile = quick_profile or deep_profile
            if resolution_profile is None:
                raise RuntimeError("default_analysis_model_not_configured")
            _job_stage(db, job, "symbol_resolving", 14)
            snapshot["holdings"] = _resolve_missing_codes(resolution_profile, snapshot["holdings"])
        codes = [item["code"] for item in snapshot["holdings"] if item.get("code")]
        _job_stage(db, job, "market_collecting", 20)
        market = collect_market_snapshot(codes)
        phase_errors: list[str] = []
        evidence: dict[str, Any] = {}
        workflow: dict[str, Any] = {"phase_errors": phase_errors}
        final_profile = deep_profile or quick_profile
        system_prompt = CORE_RULES + "\n\n" + runtime_prompt()
        quality_gate = _quality_gate(snapshot, market)

        if quality_gate["status"] == "blocked":
            final = _blocked_result(snapshot, market)
            workflow.update({key: final.get(key) for key in (
                "evidence_pack",
                "quality_gate",
                "investment_debate_state",
                "research_manager_verdict",
                "trader_proposal",
                "risk_revision",
                "risk_debate_state",
                "portfolio_manager_final",
                "hot_sectors",
                "buy_candidates",
                "candidate_status",
                "candidate_blocked_reason",
            )})
            market = refresh_snapshot_quotes(market, codes)
            final_profile = None
        else:
            if quick_profile is None and deep_profile is None:
                raise RuntimeError("default_analysis_model_not_configured")
            analyst_profile = quick_profile or deep_profile
            manager_profile = (deep_profile or quick_profile) if job.mode == "deep" else analyst_profile
            input_payload = {
                "snapshot": snapshot,
                "market": market,
                "recent_history": history,
                "checkpoint": job.checkpoint,
                "analysis_mode": job.mode,
            }

            _job_stage(db, job, "analysts_running", 30)
            evidence = _required_call_json(
                analyst_profile,
                system_prompt,
                input_payload,
                "Phase 1 分析师团队：从行情、技术、VPA、主力资金、近期公告、市场情绪、板块热度、"
                "资金可用性、组合集中度和历史一致性形成证据包。输出 JSON："
                '{"market_read":"", "intent":{}, "analyst_reports":[], "holding_evidence":[], '
                '"portfolio_risks":[], "data_gaps":[], "quality_grade":"A-F"}。证据必须引用输入来源。',
                "analyst_evidence",
            )
            _job_stage(db, job, "quality_gate", 38)
            quality_gate = _quality_gate(snapshot, market, evidence)
            workflow["evidence_pack"] = evidence
            workflow["quality_gate"] = quality_gate

            if quality_gate["status"] == "blocked":
                final = _blocked_result(snapshot, market)
                final["evidence_pack"] = evidence
                final["quality_gate"] = quality_gate
                workflow.update({key: final.get(key) for key in (
                    "investment_debate_state",
                    "research_manager_verdict",
                    "trader_proposal",
                    "risk_revision",
                    "risk_debate_state",
                    "portfolio_manager_final",
                    "hot_sectors",
                    "buy_candidates",
                    "candidate_status",
                    "candidate_blocked_reason",
                )})
                market = refresh_snapshot_quotes(market, codes)
                final_profile = None
            else:
                _job_stage(db, job, "investment_debate", 47)
                debate_raw = _required_call_json(
                    analyst_profile,
                    system_prompt,
                    {"input": input_payload, "evidence_pack": evidence, "quality_gate": quality_gate, "claim_schema": CLAIM_SCHEMA},
                    "Phase 3 进行两轮 Claim 驱动的多空辩论。投资论点必须使用 INV- Claim ID，"
                    "包含 speaker、stance、claim、最多三条 evidence、confidence、status、target_claim_ids。"
                    "输出 investment_debate_state，其中包含 bull_claims、bear_claims、unresolved_claim_ids、"
                    "round_summaries、judge_decision；同时输出 bull_case、bear_case、unresolved_claims。",
                    "investment_debate",
                )
                investment = _normalise_investment_debate(debate_raw, evidence, snapshot["holdings"])
                workflow["investment_debate_state"] = investment

                _job_stage(db, job, "research_verdict", 55)
                research = _required_call_json(
                    manager_profile,
                    system_prompt,
                    {"input": input_payload, "evidence_pack": evidence, "quality_gate": quality_gate, "investment_debate_state": investment},
                    "Phase 4 研究总监裁决：逐项处理 unresolved_claim_ids，输出 JSON："
                    '{"rating":"Buy/Overweight/Hold/Underweight/Sell", "winner":"bull/bear/balanced", '
                    '"unresolved_claim_treatment":[], "strategic_action":"", "confidence":"high/medium/low", "reasoning":""}。',
                    "research_verdict",
                )
                research.setdefault("rating", "Hold")
                research.setdefault("winner", "balanced")
                research.setdefault("unresolved_claim_treatment", investment.get("unresolved_claim_ids", []))
                research.setdefault("strategic_action", investment.get("judge_decision") or "保持观察")
                research.setdefault("confidence", "low" if quality_gate["grade"] == "C" else "medium")
                workflow["research_manager_verdict"] = research

                _job_stage(db, job, "trader_proposal", 62)
                trader_raw = _required_call_json(
                    analyst_profile,
                    system_prompt,
                    {"input": input_payload, "research_manager_verdict": research, "quality_gate": quality_gate},
                    "Phase 4 交易员方案：把研究裁决转为每个持仓可执行的今日动作。严格遵守 available_qty、T+1、"
                    "100 股/份整手和当前检查点。输出 JSON："
                    '{"orders":[{"code":"", "name":"", "action":"add/conditional_add/hold/reduce/sell/watch", '
                    '"trigger":"", "quantity":"", "take_profit":"", "stop_loss":"", "invalidating_condition":"", '
                    '"checkpoint_rule":""}], "checkpoint_rule":"", "cancel_all_buys_when":""}。',
                    "trader_proposal",
                )
                trader = {
                    "orders": trader_raw.get("orders") or trader_raw.get("proposals") or trader_raw.get("holdings") or [],
                    "checkpoint_rule": trader_raw.get("checkpoint_rule") or "执行前复核最终行情与可用数量。",
                    "cancel_all_buys_when": trader_raw.get("cancel_all_buys_when") or "指数、板块或主力资金转弱。",
                    "original_proposal": trader_raw,
                }
                workflow["trader_proposal"] = trader

                _job_stage(db, job, "risk_revision", 69)
                risk_review_raw = _required_call_json(
                    manager_profile,
                    system_prompt,
                    {"input": input_payload, "quality_gate": quality_gate, "trader_proposal": trader, "investment_debate_state": investment},
                    "Phase 4 风控经理审查交易员方案。输出 JSON："
                    '{"decision":"pass/revise/reject", "reason":"", "hard_constraints":[], "soft_constraints":[], '
                    '"de_risk_triggers":[], "execution_prerequisites":[]}。若违反 available_qty、T+1、集中度或数据门控必须 revise/reject。',
                    "risk_revision",
                )
                decision = str(risk_review_raw.get("decision") or risk_review_raw.get("risk_decision") or "pass").lower()
                if decision not in {"pass", "revise", "reject"}:
                    decision = "pass"
                risk_revision = {
                    "decision": decision,
                    "reason": risk_review_raw.get("reason") or risk_review_raw.get("reasons"),
                    "hard_constraints": risk_review_raw.get("hard_constraints") or [],
                    "soft_constraints": risk_review_raw.get("soft_constraints") or [],
                    "de_risk_triggers": risk_review_raw.get("de_risk_triggers") or [],
                    "execution_prerequisites": risk_review_raw.get("execution_prerequisites") or [],
                    "revision_count": 0,
                    "original_proposal": trader.get("orders", []),
                }
                if decision == "revise":
                    revised_raw = _required_call_json(
                        analyst_profile,
                        system_prompt,
                        {"input": input_payload, "trader_proposal": trader, "risk_revision": risk_revision},
                        "Phase 4 交易员按风控硬性约束进行第 1 次且唯一一次修正。输出与 trader_proposal 相同的 orders JSON，"
                        "并说明每项变化；不得突破 available_qty。",
                        "trader_revision",
                    )
                    revised_orders = revised_raw.get("orders") or revised_raw.get("proposals") or revised_raw.get("holdings") or []
                    if revised_orders:
                        trader["orders"] = revised_orders
                        trader["revised_proposal"] = revised_raw
                        risk_revision["revision_count"] = 1
                        risk_revision["revised_proposal"] = revised_orders
                    else:
                        risk_revision["decision"] = "reject"
                        risk_revision["reason"] = "修正后仍未返回可验证交易方案"
                workflow["trader_proposal"] = trader
                workflow["risk_revision"] = risk_revision

                _job_stage(db, job, "risk_debate", 76)
                risk_debate_raw = _required_call_json(
                    manager_profile,
                    system_prompt,
                    {"input": input_payload, "trader_proposal": trader, "risk_revision": risk_revision, "claim_schema": CLAIM_SCHEMA},
                    "Phase 5 三方风控辩论：激进、中立、保守各给出一个核心 Claim。必须输出 claims，"
                    "Claim ID 分别为 RISK-1/RISK-2/RISK-3，speaker 分别为 aggressive/neutral/conservative，"
                    "并输出 unresolved_claim_ids、round_summaries、judge_decision。",
                    "risk_debate",
                )
                risk_debate = _normalise_risk_debate(risk_debate_raw, snapshot["holdings"], quality_gate)
                workflow["risk_debate_state"] = risk_debate

                _job_stage(db, job, "final_quote_refresh", 82)
                market = refresh_snapshot_quotes(market, codes)
                input_payload["market"] = market

                _job_stage(db, job, "candidate_screening", 87)
                candidate_raw = _required_call_json(
                    analyst_profile,
                    system_prompt,
                    {
                        "input": input_payload,
                        "quality_gate": quality_gate,
                        "trader_proposal": trader,
                        "risk_revision": risk_revision,
                    },
                    "执行今日买入候选三层扫描：大盘环境、热门板块、候选盘口。输出 1-2 个候选或明确阻断。"
                    "每个候选必须包含 code、name、candidate_type(new_position/add_existing/conditional_add/rotation_watch)、"
                    "reason_detail(catalyst/capital_flow/sector_position)、entry_trigger、initial_size、take_profit_1、"
                    "take_profit_2、stop_loss、invalidating_condition、score(0-10)、score_breakdown。"
                    "同时输出 hot_sectors、market_buy_mode、cancel_all_buys_when、candidate_blocked_reason。",
                    "candidate_screening",
                )
                candidates = candidate_raw.get("candidates") or candidate_raw.get("buy_candidates") or []
                candidate_evidence_gaps: list[str] = []
                if not market.get("sector_heat"):
                    candidate_evidence_gaps.append("market.sector_heat")
                if not ((market.get("candidate_pool") or {}).get("etf_leaders")):
                    candidate_evidence_gaps.append("market.candidate_pool.etf_leaders")
                if not market.get("news"):
                    candidate_evidence_gaps.append("market.news")
                if candidate_evidence_gaps:
                    candidates = []
                workflow["candidates"] = candidates
                workflow["hot_sectors"] = candidate_raw.get("hot_sectors") or market.get("sector_heat") or []
                workflow["candidate_status"] = (
                    "blocked_missing_evidence"
                    if candidate_evidence_gaps
                    else candidate_raw.get("market_buy_mode") or ("ready" if candidates else "none")
                )
                workflow["candidate_blocked_reason"] = (
                    "候选数据不完整，暂不给出可执行买入：" + "、".join(candidate_evidence_gaps)
                    if candidate_evidence_gaps
                    else candidate_raw.get("candidate_blocked_reason")
                )

                _job_stage(db, job, "portfolio_synthesis", 92)
                final = _required_call_json(
                    manager_profile,
                    system_prompt,
                    {
                        "input": input_payload,
                        "evidence_pack": evidence,
                        "quality_gate": quality_gate,
                        "investment_debate_state": investment,
                        "research_manager_verdict": research,
                        "trader_proposal": trader,
                        "risk_revision": risk_revision,
                        "risk_debate_state": risk_debate,
                        "buy_candidate_plan": candidate_raw,
                        "required_schema": FINAL_SCHEMA,
                    },
                    "Phase 5 组合经理最终决策：基于最终刷新行情综合全部阶段，严格按 required_schema 返回 JSON。"
                    "每个当前持仓都必须出现，today_actions 与 holdings 一致，buy_candidates 与 candidates 一致，"
                    "不得遗漏调仓计划、检查点计划、未解决论点和风险约束。",
                    "portfolio_synthesis",
                )
                if not final:
                    final = {
                        "data_quality_grade": quality_gate["grade"],
                        "market_read": evidence.get("market_read") or "市场证据已采集，最终模型阶段降级。",
                        "portfolio_conclusion": research.get("strategic_action") or "保持观察。",
                        "final_rating": "hold",
                        "cash_target": "保持现状",
                        "confidence": "low",
                        "holdings": trader.get("orders", []),
                        "candidates": candidates,
                        "history_consistency": "沿用本次研究总监和风控结论。",
                    }

        workflow["phase_errors"] = phase_errors
        if final_profile is not None:
            final = _normalize_final(final, snapshot["holdings"], quality_gate.get("grade", market.get("quality_grade", "C")), workflow)
        else:
            final = _normalize_final(final, snapshot["holdings"], final.get("data_quality_grade", "F"), workflow)
        for key in (
            "evidence_pack",
            "quality_gate",
            "investment_debate_state",
            "research_manager_verdict",
            "trader_proposal",
            "risk_revision",
            "risk_debate_state",
            "portfolio_manager_final",
            "today_actions",
            "buy_candidates",
            "hot_sectors",
            "rebalance_plan",
            "checkpoint_plan",
        ):
            workflow[key] = final.get(key)

        _job_stage(db, job, "report_rendering", 96)
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
                "workflow": workflow,
                "skill_execution": {
                    "mode": job.mode,
                    "phases_completed": (
                        [
                            "intent_and_history_context",
                            "verified_market_snapshot",
                            "quality_gate",
                            "analyst_evidence",
                            "bull_bear_debate",
                            "research_verdict",
                            "trader_proposal",
                            "risk_revision",
                            "three_way_risk_debate",
                            "final_quote_refresh",
                            "buy_candidate_selection",
                            "portfolio_manager_final",
                        ]
                        if final_profile is not None
                        else [
                            "intent_and_history_context",
                            "verified_market_snapshot",
                            "quality_gate",
                            *( ["analyst_evidence"] if evidence else [] ),
                            "final_quote_refresh",
                            "portfolio_manager_final",
                        ]
                    ),
                    "phase_errors": phase_errors,
                },
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
