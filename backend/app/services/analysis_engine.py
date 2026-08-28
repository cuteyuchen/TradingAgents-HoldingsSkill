"""Portfolio-aware analysis job runner built around the holdings Skill rules."""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..candidates.service import latest_candidate_context, scan_candidates
from ..config import settings
from ..database import SessionLocal
from ..decision_contract import (
    CANDIDATE_MAX_COUNT,
    DEFAULT_PORTFOLIO_ACTION,
    canonicalize_analysis_mode,
    should_normalize_no_action,
)
from ..memory.service import current_memory_features, memory_context_for_analysis
from ..portfolio.decision_gate import apply_portfolio_decision_gate
from ..portfolio.service import portfolio_context_for_analysis
from ..v2_models import AnalysisJob, AnalysisRun, ModelProfile, PortfolioSnapshot
from .market_data import collect_market_snapshot, normalize_code, refresh_snapshot_quotes
from .model_client import call_model, parse_json_result
from .analysis_lease import AnalysisLeaseHeartbeat
from .skill_runtime import runtime_prompt

logger = logging.getLogger(__name__)

CORE_RULES = """
你是 TradingAgents Holdings Advisor 的服务端分析引擎，面向 A 股和 ETF。
必须遵守：
- NO_ACTION 是一等合法的组合结果。先判断是否有必要改变当前组合，再判断改变什么；分析完成不等于必须交易。
- 本次确认的持仓快照是当前持仓的唯一真实来源，历史只能用于一致性检查。
- qty 是总持仓，available_qty 是当前可卖数量；减仓/卖出数量不得超过 available_qty。
- qty-available_qty 可能来自挂单、冻结或 T+1，不能推断为已经卖出。
- 亏损是风险输入，不是自动卖出理由，必须结合技术、资金、事件和组合风险。
- 同日或近期建议发生方向反转，必须指出发生了什么实质变化。
- 缺少关键行情时，不得编造触发价和具体数量。
- 新 Candidate 只表示当前未持有的新机会，允许 0-3 个；当前持仓加仓/条件加仓只能出现在 Holding Action。
- Phase F deterministic Candidate Engine 是新 Candidate 的唯一来源：模型只能解释或否决后端 ACTION，不能发明代码、提升 READY/WATCHLIST、修改分数或绕过 Decision Edge。
- Fast 只读取同一持仓快照下的近期可靠 CandidateRun；Standard/Deep 才能运行本地缓存扫描，候选扫描不得逐票联网。
- 证据充分、所有持仓为 hold/watch 且没有通过门控的新 Candidate 时，组合级结果必须为 no_action；质量门控 blocked 时必须保留 watch_only。
- 事实、推断、风险和失效条件必须区分。
- 这是研究辅助，不承诺收益，不执行交易。
- portfolio_context 是后端提供的确定性组合风险事实和动作上限，不是交易指令；不得覆盖 hard_cap、max_additional_weight 或 max_sellable_qty。
- Historical Memory 仅是可审计的辅助证据。当前 Market、Portfolio、Candidate、Data Quality 和 Decision Gate 永远优先；历史案例不得发明候选、升级候选阶段或覆盖风险门控。
- 不得因为历史案例盈利就复制动作，也不得因为历史案例亏损就机械反向交易。Memory context 不能改变任何因子权重、Hard Cap 或 Risk Gate。
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
    "final_rating": "add/hold/reduce/sell/rotate/no_action/watch_only",
    "cash_target": "建议现金区间",
    "confidence": "high/medium/low",
    "holdings": [
        {
            "code": "证券代码",
            "name": "名称",
            "action": "add/hold/reduce/sell/watch",
            "reason": "证据与原因",
            "trigger": "条件或价格",
            "trigger_plan": {
                "condition": "price_below/price_above/pct_change_below/pct_change_above",
                "threshold": 0.0,
                "priority": "P0/P1/P2/P3",
                "action_context": "触发后复核的动作语义",
            },
            "quantity": "数量或比例；卖出不得超过 available_qty",
            "current_weight": 0.0,
            "target_weight": 0.0,
            "adjustment_weight": 0.0,
            "max_sellable_qty": 0,
            "hard_cap": 0.0,
            "max_additional_weight": 0.0,
            "portfolio_gate": "PASS/ADJUSTED/BLOCKED/REVIEW_ONLY",
            "stop_loss": "止损/失效条件",
            "take_profit": "止盈/观察条件",
            "risk": "主要风险",
        }
    ],
    "candidates": [
        {
            "code": "代码",
            "name": "名称",
            "action": "new_position",
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
    "memory_context": {},
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


def _numeric_score(value: Any) -> float | None:
    """Parse a finite candidate score while keeping malformed scores unranked."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if score == score and score not in {float("inf"), float("-inf")} else None


def _trigger_context(job: AnalysisJob) -> dict[str, Any] | None:
    """Return server-owned Trigger context as analysis context, never an order."""

    context = job.context_json if isinstance(job.context_json, dict) else {}
    event_ids = list(context.get("trigger_event_ids") or [])
    if not event_ids and context.get("trigger_event_id") is not None:
        event_ids = [context["trigger_event_id"]]
    if not event_ids:
        return None
    contexts = [item for item in context.get("trigger_contexts") or [] if isinstance(item, dict)]
    if not contexts:
        contexts = [{
            "trigger_event_id": event_ids[-1],
            "trigger_reason": context.get("trigger_reason"),
            "trigger_evidence": context.get("trigger_evidence") or {},
        }]
    return {
        "trigger_event_ids": event_ids,
        "reason": context.get("trigger_reason"),
        "evidence": context.get("trigger_evidence") or {},
        "events": contexts,
        "interpretation": "这是本次重新分析的原因与已观测证据，不是交易指令；必须独立核验后才能提出任何动作。",
    }


def _candidate_context_for_analysis(
    db: Session,
    *,
    job: AnalysisJob,
    analysis_mode: str,
    quote_rows: Any = None,
    parameter_context: dict[str, Any] | None = None,
    parameter_lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load deterministic candidates without allowing analysis to invent them."""

    try:
        candidate_config = None
        if parameter_context is not None:
            from ..governance.registry import candidate_config_from_snapshot

            candidate_config = candidate_config_from_snapshot(parameter_context["snapshot"])
        if analysis_mode == "fast":
            return latest_candidate_context(
                db,
                user_id=job.user_id,
                portfolio_id=job.portfolio_id,
                snapshot_id=job.snapshot_id,
                max_age_seconds=30 * 60,
                require_reliable=True,
            )
        return scan_candidates(
            db,
            user_id=job.user_id,
            portfolio_id=job.portfolio_id,
            snapshot_id=job.snapshot_id,
            mode=analysis_mode,
            persist=True,
            config=candidate_config,
            # Standard/Deep own one fresh all-market bulk quote snapshot inside
            # Candidate Engine.  The initial market snapshot only covers held
            # positions and must not be reused as candidate provenance.
            quote_rows=None,
            parameter_context=parameter_context,
            parameter_lineage=parameter_lineage,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Candidate Engine unavailable for analysis job %s", job.id)
        return {
            "status": "unavailable",
            "quality_status": "MISSING",
            "confidence": 0.0,
            "run_id": None,
            "watchlist": [],
            "ready": [],
            "action": [],
            "candidates": [],
            "reason": "CANDIDATE_ENGINE_UNAVAILABLE",
            "error": str(exc)[:300],
        }


def _normalize_final(
    result: dict[str, Any],
    holdings: list[dict[str, Any]],
    quality_grade: str,
    workflow: dict[str, Any] | None = None,
    candidate_context: dict[str, Any] | None = None,
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
        "candidate_engine": {},
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
    candidate_context = candidate_context if candidate_context is not None else workflow.get("candidate_context")
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
    if candidate_context is not None:
        result["candidate_engine"] = {
            "run_id": candidate_context.get("run_id") or (candidate_context.get("run") or {}).get("id"),
            "status": candidate_context.get("status", "unavailable"),
            "quality_status": candidate_context.get("quality_status", "MISSING"),
            "confidence": candidate_context.get("confidence", 0.0),
            "market_regime": (candidate_context.get("candidate_engine") or {}).get("market_regime"),
            "watchlist_count": len(candidate_context.get("watchlist") or []),
            "ready_count": len(candidate_context.get("ready") or []),
            "action_count": len(candidate_context.get("action") or []),
            "calculation_version": (candidate_context.get("candidate_engine") or {}).get("calculation_version"),
            "reason": candidate_context.get("reason"),
        }
    phase_errors = workflow.get("phase_errors") or []
    if phase_errors:
        result["phase_errors"] = phase_errors
        result["risk_warnings"].extend(f"分析阶段降级：{item}" for item in phase_errors)

    by_code: dict[str, dict[str, Any]] = {}
    for item in holdings:
        code = normalize_code(str(item.get("code") or ""))
        if not code:
            continue
        source = dict(item)
        source["code"] = code
        by_code[code] = source
    output_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in result.get("holdings") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        code = normalize_code(str(row.get("code") or ""))
        if code not in by_code or code in seen:
            continue
        seen.add(code)
        source = by_code[code]
        row["code"] = code
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

    holding_codes = {row["code"] for row in output_rows}
    filtered_candidates: list[dict[str, Any]] = []
    deterministic_allowed: dict[str, dict[str, Any]] | None = None
    candidate_quality = ""
    candidate_quality_blocked = False
    if candidate_context is not None:
        candidate_quality = str(
            candidate_context.get("quality_status")
            or (candidate_context.get("run") or {}).get("quality_status")
            or ""
        ).upper()
        candidate_quality_blocked = candidate_quality in {"MISSING", "BLOCKED", "BLOCKED_FOR_ACTION"}
        if not candidate_quality_blocked:
            deterministic_allowed = {
                normalize_code(str(item.get("code") or "")): dict(item)
                for item in candidate_context.get("action") or []
                if isinstance(item, dict) and normalize_code(str(item.get("code") or ""))
            }
    # An explicitly empty workflow list is authoritative: the candidate scan
    # may have blocked new opportunities even when a later model response still
    # echoes stale candidates in its final payload.
    if "candidates" in workflow:
        raw_candidates = workflow.get("candidates") or []
    else:
        raw_candidates = result.get("buy_candidates") or result.get("candidates") or []
    final_model_rating = str(
        result.get("final_rating")
        or (result.get("portfolio_manager_final") or {}).get("portfolio_rating")
        or ""
    ).lower()
    if candidate_quality_blocked:
        raw_candidates = []
        result.setdefault(
            "candidate_blocked_reason",
            f"CandidateRun 全局质量门为 {candidate_quality}，新增风险候选已关闭。",
        )
    if candidate_context is not None and final_model_rating in {"no_action", "watch_only"}:
        # The deterministic ACTION set is the only source of candidates, but
        # the final model is still allowed to veto the entire set.
        raw_candidates = []
        result.setdefault("candidate_blocked_reason", "最终组合经理否决新增风险，保持 NO_ACTION。")
    gate = result.get("quality_gate") or workflow.get("quality_gate") or {}
    gate_grade = str(gate.get("grade") or quality_grade).upper()
    gate_status = str(gate.get("status") or "pass").lower()
    if gate_grade in {"D", "F"}:
        gate_status = "blocked"
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        row = dict(candidate)
        code = normalize_code(str(row.get("code") or ""))
        # Candidate means a new, non-held opportunity.  Invalid or held rows are
        # removed before any count cap is applied; holding-level adds remain in
        # the holding action table.
        if len(code) != 6 or not code.isdigit() or code in holding_codes:
            if code in holding_codes:
                result["risk_warnings"].append(f"候选 {code} 已在当前持仓中，已从新增机会列表移除。")
            continue
        if deterministic_allowed is not None:
            deterministic_row = deterministic_allowed.get(code)
            if deterministic_row is None:
                # READY/WATCHLIST rows and model-invented codes can never enter
                # the Phase A new-position contract.
                continue
            if row.get("accepted") is False or row.get("veto") is True or row.get("actionable") is False:
                continue
            model_row = row
            row = {**deterministic_row, **model_row}
            # LLM output may explain or demote, but may not alter the facts that
            # determine stage, score, edge, coverage, or risk.
            for field in (
                "name",
                "security_type",
                "etf_category",
                "stage",
                "candidate_engine_stage",
                "score",
                "opportunity_score",
                "entry_score",
                "portfolio_fit_score",
                "action_score",
                "decision_edge",
                "edge_vs_no_action",
                "edge_vs_current_holdings",
                "risk_reward_ratio",
                "data_coverage",
                "confidence",
                "funding_mode",
                "probe_weight",
                "positive_drivers",
                "negative_drivers",
                "blocking_reasons",
                "risk_flags",
                "components",
                "entry",
                "portfolio_fit",
                "comparison",
            ):
                if field in deterministic_row:
                    row[field] = deterministic_row[field]
        row["code"] = code
        candidate_type = str(row.get("candidate_type") or row.get("type") or row.get("action") or "rotation_watch").lower()
        candidate_type = {
            "new": "new_position",
            "buy": "new_position",
            "新开仓": "new_position",
            "watch": "rotation_watch",
            "watch_only": "rotation_watch",
            "轮动观察": "rotation_watch",
        }.get(candidate_type, candidate_type)
        # ``result.candidates`` is the Action Candidate list, not a watch pool.
        # Phase A has no separate Watch/Ready lifecycle, so rotation watches and
        # sub-threshold rows must stay out of this list entirely.  They must not
        # prevent deterministic NO_ACTION when every holding is hold/watch.
        if candidate_type != "new_position":
            continue
        row["candidate_type"] = candidate_type
        reason_detail = row.get("reason_detail") if isinstance(row.get("reason_detail"), dict) else {}
        row["reason_detail"] = {
            "catalyst": row.get("catalyst") or row.get("news_catalyst") or reason_detail.get("catalyst"),
            "capital_flow": row.get("capital_flow") or reason_detail.get("capital_flow"),
            "sector_position": row.get("sector_position") or reason_detail.get("sector_position"),
        }
        missing_reason_fields = [key for key, value in row["reason_detail"].items() if value is None or not str(value).strip()]
        if missing_reason_fields or gate_grade in {"C", "D", "F"} or gate_status == "blocked":
            continue
        score = _numeric_score(row.get("score"))
        # A candidate only enters the action contract when it clears the
        # minimum score.  Missing or malformed scores are not actionable.
        if score is None or score < 7:
            continue
        row["buyable"] = True
        row["actionable"] = True
        row["gate_status"] = "buyable"
        filtered_candidates.append(row)

    # Keep the contract bounded.  Valid numeric scores are preferred, while
    # preserving stable input order for candidates without a score.
    filtered_candidates.sort(
        key=lambda item: (
            _numeric_score(item.get("score")) is not None,
            _numeric_score(item.get("score")) if _numeric_score(item.get("score")) is not None else float("-inf"),
        ),
        reverse=True,
    )
    filtered_candidates = filtered_candidates[:CANDIDATE_MAX_COUNT]
    result["candidates"] = filtered_candidates
    result["buy_candidates"] = filtered_candidates
    result["today_actions"] = output_rows
    existing_candidate_status = str(result.get("candidate_status") or "").lower()
    if filtered_candidates:
        result["candidate_status"] = existing_candidate_status or "ready"
    elif gate_status == "blocked":
        result["candidate_status"] = "blocked"
    elif existing_candidate_status in {"blocked_missing_evidence", "risk_control", "watch_only"}:
        result["candidate_status"] = existing_candidate_status
    else:
        result["candidate_status"] = "none"
    if not filtered_candidates and not result.get("candidate_blocked_reason"):
        result["candidate_blocked_reason"] = "当前没有同时满足消息面、资金面、板块位置和风险门控的候选。"

    investment = result.get("investment_debate_state") or {}
    if investment:
        result["bull_case"] = investment.get("bull_case") or result.get("bull_case") or []
        result["bear_case"] = investment.get("bear_case") or result.get("bear_case") or []
        result["unresolved_claims"] = investment.get("unresolved_claims") or result.get("unresolved_claims") or []

    normalized_no_action = should_normalize_no_action(
        quality_gate_status=gate_status,
        holdings=output_rows,
        candidates=filtered_candidates,
    )
    if gate_status == "blocked":
        # A blocked quality gate is never silently downgraded to NO_ACTION, even
        # if the model happened to emit a no_action rating in its free-form output.
        result["final_rating"] = "watch_only"
    if normalized_no_action:
        result["final_rating"] = DEFAULT_PORTFOLIO_ACTION
        # Deterministic contract fields outrank any stale model prose/actions.
        result["portfolio_conclusion"] = "当前没有足够证据证明调整组合优于保持现状，维持当前组合。"
        result["today_actions"] = output_rows
        result["rebalance_plan"] = {}
        trader_proposal = result.get("trader_proposal")
        if not isinstance(trader_proposal, dict):
            trader_proposal = {}
        trader_proposal["orders"] = output_rows
        trader_proposal["proposals"] = output_rows
        trader_proposal["decision"] = "hold"
        result["trader_proposal"] = trader_proposal

    portfolio_final = result.get("portfolio_manager_final") or {}
    if not isinstance(portfolio_final, dict):
        portfolio_final = {}
    if gate_status == "blocked":
        portfolio_final["portfolio_rating"] = "watch_only"
    elif normalized_no_action:
        portfolio_final["portfolio_rating"] = DEFAULT_PORTFOLIO_ACTION
        portfolio_final["final_actions"] = output_rows
    elif result.get("final_rating") == DEFAULT_PORTFOLIO_ACTION:
        portfolio_final["portfolio_rating"] = DEFAULT_PORTFOLIO_ACTION
    else:
        portfolio_final.setdefault("portfolio_rating", result.get("final_rating"))
    portfolio_final.setdefault("cash_target", result.get("cash_target"))
    portfolio_final.setdefault("risk_decision", (result.get("risk_revision") or {}).get("decision", "pass"))
    if not normalized_no_action:
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
    decision_gate = result.get("decision_gate") or {}
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
        "## 组合约束门",
        "",
        f"- Gate 状态：`{_md(decision_gate.get('status'))}`；组合动作：`{_md(decision_gate.get('portfolio_action'))}`",
        f"- 阻断原因：{_md(decision_gate.get('blocking_reasons') or '无')}",
        f"- 调整提示：{_md(decision_gate.get('warnings') or '无')}",
        "",
        "| 标的 | Gate | 请求目标/数量 | 允许目标/数量 | Reason Code |",
        "|---|---|---|---|---|",
    ])
    for gate_row in decision_gate.get("action_results") or []:
        lines.append(
            f"| {_md(gate_row.get('code'))} | {_md(gate_row.get('status'))} | "
            f"{_md(gate_row.get('requested_target_weight') if gate_row.get('requested_target_weight') is not None else gate_row.get('requested_qty'))} | "
            f"{_md(gate_row.get('allowed_target_weight') if gate_row.get('allowed_target_weight') is not None else gate_row.get('allowed_qty'))} | "
            f"{_md(gate_row.get('reason_codes'))} |"
        )
    if not decision_gate.get("action_results"):
        lines.append("| - | PASS | - | - | - |")

    lines.extend([
        "",
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


def _fail_closed_portfolio_gate_result(final: dict[str, Any], error: Exception) -> dict[str, Any]:
    """Remove executable portfolio changes when the deterministic Gate fails."""

    final["portfolio_engine"] = {"status": "unavailable", "error": str(error)[:300]}
    final["decision_gate"] = {
        "status": "REVIEW_ONLY",
        "portfolio_action": "WATCH_ONLY",
        "blocking_reasons": ["PORTFOLIO_ENGINE_UNAVAILABLE"],
        "calculation_version": "portfolio-decision-gate-v1",
    }
    safe_holdings: list[dict[str, Any]] = []
    for raw in final.get("holdings") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if str(row.get("action") or "").lower() in {"add", "conditional_add", "reduce", "sell"}:
            row["action"] = "watch"
            row["quantity"] = None
            row["target_weight"] = None
        row["portfolio_gate"] = "BLOCKED"
        row["portfolio_gate_reasons"] = ["PORTFOLIO_ENGINE_UNAVAILABLE"]
        safe_holdings.append(row)
    safe_candidates: list[dict[str, Any]] = []
    for raw in final.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        candidate["buyable"] = False
        candidate["actionable"] = False
        candidate["gate_status"] = "blocked"
        candidate["portfolio_gate"] = "BLOCKED"
        candidate["portfolio_gate_reasons"] = ["PORTFOLIO_ENGINE_UNAVAILABLE"]
        safe_candidates.append(candidate)
    final["holdings"] = safe_holdings
    final["today_actions"] = safe_holdings
    final["candidates"] = safe_candidates
    final["buy_candidates"] = safe_candidates
    final["final_rating"] = "watch_only"
    final["portfolio_conclusion"] = "组合约束引擎暂不可用，本次不输出可执行交易动作。"
    portfolio_final = final.get("portfolio_manager_final") if isinstance(final.get("portfolio_manager_final"), dict) else {}
    portfolio_final["portfolio_rating"] = "watch_only"
    portfolio_final["final_actions"] = safe_holdings
    final["portfolio_manager_final"] = portfolio_final
    return final


def run_analysis_job(job_id: int) -> None:
    db = SessionLocal()
    job: AnalysisJob | None = None
    heartbeat: AnalysisLeaseHeartbeat | None = None
    stop_event = threading.Event()
    from ..system.logging import bind_worker_context
    from ..system.workers import register_worker, unregister_worker

    register_worker("analysis", job_id, stop_event)
    bind_worker_context(analysis_job_id=job_id)
    try:
        job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if job is None or job.status not in {"queued", "retrying"}:
            return
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job.error_code = None
        job.error_message = None
        db.commit()
        heartbeat = AnalysisLeaseHeartbeat.for_job(
            db,
            job_id=job.id,
            external_stop=stop_event,
        )
        if heartbeat is not None:
            heartbeat.start()

        from ..governance.service import lineage_fields, resolve_production_parameters

        parameter_context = resolve_production_parameters(db)
        parameter_lineage = lineage_fields(parameter_context)
        bind_worker_context(
            analysis_job_id=job_id,
            parameter_set_version=parameter_lineage.get("parameter_set_version"),
        )

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
        # A realtime Trigger may have attached context after this job began.
        # Refresh before model prompts so a reused active job sees that reason.
        db.refresh(job)
        phase_errors: list[str] = []
        evidence: dict[str, Any] = {}
        workflow: dict[str, Any] = {"phase_errors": phase_errors}
        trigger_context = _trigger_context(job)
        if trigger_context is not None:
            workflow["trigger_context"] = trigger_context
        final_profile = deep_profile or quick_profile
        system_prompt = CORE_RULES + "\n\n" + runtime_prompt()
        try:
            portfolio_context = portfolio_context_for_analysis(db, snapshot=snapshot_row, market=market)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Portfolio Engine context failed for analysis job %s", job.id)
            portfolio_context = {
                "interpretation": "Portfolio Engine context unavailable; do not produce executable risk-increase actions.",
                "portfolio_quality": "BLOCKED",
                "portfolio_confidence": 0.0,
                "position_constraints": [],
                "market_state_frozen": False,
                "portfolio_engine_error": str(exc)[:300],
            }
            workflow["phase_errors"].append("portfolio_engine_context_unavailable")
        workflow["portfolio_context"] = portfolio_context
        quality_gate = _quality_gate(snapshot, market)
        analysis_mode = canonicalize_analysis_mode(job.mode)
        workflow["analysis_mode"] = analysis_mode
        candidate_context = _candidate_context_for_analysis(
            db,
            job=job,
            analysis_mode=analysis_mode,
            quote_rows=market.get("quotes") if isinstance(market, dict) else None,
            parameter_context=parameter_context,
            parameter_lineage=parameter_lineage,
        )
        workflow["candidate_context"] = candidate_context
        memory_context = memory_context_for_analysis(
            db,
            user_id=job.user_id,
            portfolio_id=job.portfolio_id,
            as_of=job.started_at or datetime.now(UTC),
            current_features=current_memory_features(
                portfolio_context=portfolio_context,
                candidate_context=candidate_context,
            ),
        )
        workflow["memory_context"] = memory_context

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
            manager_profile = (deep_profile or quick_profile) if analysis_mode == "deep" else analyst_profile
            input_payload = {
                "snapshot": snapshot,
                "market": market,
                "recent_history": history,
                "checkpoint": job.checkpoint,
                "analysis_mode": analysis_mode,
                "trigger_context": trigger_context,
                "portfolio_context": portfolio_context,
                "candidate_context": candidate_context,
                "memory_context": memory_context,
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
                deterministic_candidates = [
                    dict(item)
                    for item in candidate_context.get("action") or []
                    if isinstance(item, dict) and str(item.get("stage") or "").upper() == "ACTION"
                ]
                candidate_raw: dict[str, Any] = {
                    "deterministic_candidates": deterministic_candidates,
                    "candidates": deterministic_candidates,
                    "accepted_codes": [item.get("code") for item in deterministic_candidates],
                    "review_status": "not_needed" if not deterministic_candidates else "pending",
                }
                if deterministic_candidates:
                    try:
                        review_raw = _call_json(
                            analyst_profile,
                            system_prompt,
                            {
                                "input": input_payload,
                                "candidate_context": candidate_context,
                                "deterministic_action_candidates": deterministic_candidates,
                                "quality_gate": quality_gate,
                                "trader_proposal": trader,
                                "risk_revision": risk_revision,
                            },
                            "只审查后端 deterministic_action_candidates。你可以解释、补充风险，或明确否决某个候选；"
                            "不得新增代码、不得把 READY/WATCHLIST 提升为 ACTION、不得修改任何分数、coverage、confidence、"
                            "decision_edge、risk_reward_ratio 或 stage。若没有需要否决的候选，原样返回 accepted_codes。"
                            "输出 JSON：{accepted_codes:[], veto_codes:[], explanations:{code:{reason_detail:{},risk:[]}}, "
                            "hot_sectors:[], candidate_blocked_reason:\"\"}。",
                        )
                    except Exception as exc:  # noqa: BLE001
                        review_raw = {
                            "review_status": "unavailable",
                            "review_error": str(exc)[:300],
                        }
                        phase_errors.append("candidate_llm_review_unavailable")
                    candidate_raw.update(review_raw if isinstance(review_raw, dict) else {})
                    all_codes = {
                        normalize_code(str(item.get("code") or ""))
                        for item in deterministic_candidates
                    }
                    if "accepted_codes" in candidate_raw:
                        accepted_codes = {
                            normalize_code(str(code))
                            for code in candidate_raw.get("accepted_codes") or []
                        }
                    elif "candidates" in candidate_raw or "buy_candidates" in candidate_raw:
                        returned = candidate_raw.get("candidates")
                        if returned is None:
                            returned = candidate_raw.get("buy_candidates")
                        accepted_codes = {
                            normalize_code(str(item.get("code") or ""))
                            for item in returned or []
                            if isinstance(item, dict)
                        }
                    else:
                        accepted_codes = all_codes
                    veto_codes = {
                        normalize_code(str(code))
                        for code in candidate_raw.get("veto_codes") or candidate_raw.get("rejected_codes") or []
                    }
                    accepted_codes -= veto_codes
                    explanations = candidate_raw.get("explanations") if isinstance(candidate_raw.get("explanations"), dict) else {}
                    candidates = []
                    for item in deterministic_candidates:
                        code = normalize_code(str(item.get("code") or ""))
                        if code not in accepted_codes or code in veto_codes:
                            continue
                        explanation = explanations.get(code) if isinstance(explanations.get(code), dict) else {}
                        candidates.append({**item, **explanation})
                    candidate_raw["accepted_codes"] = [item.get("code") for item in candidates]
                    candidate_raw["review_status"] = candidate_raw.get("review_status") or "completed"
                else:
                    candidates = []
                diagnostics = candidate_context.get("diagnostics") if isinstance(candidate_context.get("diagnostics"), dict) else {}
                action_zero_reasons = diagnostics.get("action_zero_reasons") or {}
                deterministic_blocked_reason = candidate_context.get("reason")
                if not deterministic_blocked_reason and action_zero_reasons:
                    deterministic_blocked_reason = "确定性候选未通过门控：" + "、".join(
                        f"{key}={value}" for key, value in sorted(action_zero_reasons.items())
                    )
                workflow["candidates"] = candidates
                workflow["candidate_review"] = candidate_raw
                workflow["hot_sectors"] = candidate_raw.get("hot_sectors") or market.get("sector_heat") or []
                workflow["candidate_status"] = (
                    candidate_raw.get("market_buy_mode")
                    or ("ready" if candidates else "llm_veto" if deterministic_candidates else candidate_context.get("status") or "none")
                )
                workflow["candidate_blocked_reason"] = (
                    candidate_raw.get("candidate_blocked_reason")
                    or deterministic_blocked_reason
                    or ("LLM 否决了全部 deterministic ACTION 候选。" if deterministic_candidates and not candidates else None)
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
                    "不得遗漏调仓计划、检查点计划、未解决论点和风险约束。trigger 保留报告用自然语言；"
                    "trigger_plan 仅在存在明确机器可读阈值时输出对象，否则必须为 null。",
                    "portfolio_synthesis",
                )
                if not final:
                    final = {
                        "data_quality_grade": quality_gate["grade"],
                        "market_read": evidence.get("market_read") or "市场证据已采集，最终模型阶段降级。",
                        "portfolio_conclusion": research.get("strategic_action") or "保持观察。",
                        "final_rating": DEFAULT_PORTFOLIO_ACTION,
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
        try:
            # Rebuild from the final quote refresh so the Gate sees the same
            # server-owned price facts as the persisted visible decision.
            portfolio_context = portfolio_context_for_analysis(db, snapshot=snapshot_row, market=market)
            workflow["portfolio_context"] = portfolio_context
            final = apply_portfolio_decision_gate(final, portfolio_context=portfolio_context)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Portfolio Decision Gate failed for analysis job %s", job.id)
            workflow["phase_errors"].append("portfolio_decision_gate_unavailable")
            final = _fail_closed_portfolio_gate_result(final, exc)
        final["portfolio_engine"] = {
            **(final.get("portfolio_engine") if isinstance(final.get("portfolio_engine"), dict) else {}),
            "portfolio_context": workflow.get("portfolio_context"),
            "calculation_version": "portfolio-engine-v1",
        }
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
            "portfolio_context",
            "decision_gate",
            "portfolio_engine",
        ):
            workflow[key] = final.get(key)
        workflow["memory_context"] = memory_context
        final["memory_context"] = memory_context

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
                    "mode": analysis_mode,
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
            parameter_set_version_id=parameter_lineage["parameter_set_version_id"],
            parameter_set_version=parameter_lineage["parameter_set_version"],
            parameter_set_hash=parameter_lineage["parameter_set_hash"],
            governance_lineage_json=parameter_lineage["governance_lineage_json"],
        )
        db.add(run)
        job.status = "succeeded"
        job.current_stage = "completed"
        job.progress_percent = 100
        job.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(run)

        # Memory is a derived maintenance fact. Capture it after the successful
        # AnalysisRun commit so a capture failure cannot roll back the report.
        memory_db = SessionLocal()
        try:
            from ..memory.decision import capture_decision_memory

            persisted_run = memory_db.query(AnalysisRun).filter(AnalysisRun.id == run.id).first()
            captured_memory = (
                capture_decision_memory(
                    memory_db,
                    persisted_run,
                    available_at=datetime.now(UTC),
                    commit=True,
                )
                if persisted_run is not None
                else None
            )
            if captured_memory is not None:
                logger.info(
                    "memory_capture portfolio=%s analysis_run=%s decision_type=%s targets=%s quality=%s",
                    job.portfolio_id,
                    run.id,
                    captured_memory.decision_type,
                    len(captured_memory.holding_decisions_json or []) + len(captured_memory.candidate_decisions_json or []),
                    captured_memory.quality_status,
                )
        except Exception:
            logger.exception("Decision Memory capture failed for analysis run %s", run.id)
        finally:
            memory_db.close()

        # Realtime triggers are resolved only after the authoritative AnalysisRun
        # exists.  Standard/Deep runs may also refresh explicit structured plans;
        # natural-language conditions remain report-only.
        try:
            from ..triggers.resolution import resolve_trigger_event_from_analysis_run
            from ..triggers.plans import refresh_trigger_plans_from_run

            resolve_trigger_event_from_analysis_run(db, run)
            refresh_trigger_plans_from_run(db, run, mode=analysis_mode)
            db.commit()
        except Exception:
            logger.exception("Trigger post-processing failed for analysis run %s", run.id)

        if job.notify:
            try:
                from .notifications import send_run_notifications

                send_run_notifications(db, run)
                db.commit()
            except Exception:
                logger.exception("Notification failed for analysis run %s", run.id)
        try:
            from ..operations.notifications import dispatch_material_events

            dispatch_material_events(
                db,
                user_id=run.user_id,
                portfolio_id=job.portfolio_id,
                as_of=run.created_at,
            )
        except Exception:
            # Operating notifications are advisory side effects and must never
            # change the authoritative AnalysisJob/AnalysisRun result.
            logger.exception("Operating notification dispatch failed for analysis run %s", run.id)
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
        if heartbeat is not None:
            heartbeat.stop()
        unregister_worker("analysis", job_id)
        db.close()
