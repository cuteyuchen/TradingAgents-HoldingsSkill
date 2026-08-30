"""Phase N live decision validation and paper-only shadow execution.

The service intentionally keeps three kinds of facts separate:

* production decision observations are immutable;
* shadow intents/fills/ledger entries are an isolated paper account; and
* outcomes are derived research facts which can exist without a fill.

There is no broker adapter in this module and no model call.  A fill can only
be created from a durable ``LiveQuoteObservation`` whose captured time is
strictly after the decision finalization time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..candidates.models import CandidateRun
from ..config import settings
from ..decision_contract import CONTRACT_VERSION
from ..market.codes import normalize_security_code
from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache
from ..market.models import NormalizedQuote
from ..market_models import SecurityMaster, TradingCalendar
from ..memory.models import DecisionMemory
from ..portfolio.constraints import hard_cap_for_security
from ..portfolio.ledger import transaction_cost_estimate
from ..portfolio_models import TradeLedgerEntry
from ..trigger_models import TriggerEvent
from ..v2_models import AnalysisJob, AnalysisRun, ModelProfile, ModelProvider, Portfolio, PortfolioSnapshot
from .models import (  # type: ignore[attr-defined]
    DecisionActualAlignment,
    LiveDecisionObservation,
    LiveDecisionOutcome,
    LiveQuoteObservation,
    ShadowAccount,
    ShadowDailySnapshot,
    ShadowFill,
    ShadowLedgerEntry,
    ShadowOrderIntent,
    ShadowPosition,
)

logger = logging.getLogger(__name__)

SHADOW_EXECUTION_VERSION = "shadow-execution-v1"
OBSERVATION_VERSION = "live-decision-observation-v1"
OUTCOME_VERSION = "live-shadow-outcome-v1"
OUTCOME_HORIZONS = (1, 5, 10, 20, 60)
ACTIONABLE_ACTIONS = frozenset({"add", "conditional_add", "new_position", "buy", "reduce", "sell", "exit", "rotate"})
BUY_ACTIONS = frozenset({"add", "conditional_add", "new_position", "buy"})
SELL_ACTIONS = frozenset({"reduce", "sell", "exit"})
VALID_QUOTE_QUALITY = frozenset({"VALID", "DEGRADED"})
FINAL_ACTIONS = frozenset({"ACTION", "NO_ACTION", "DECISION_BLOCKED"})
CONDITIONAL_ACTION_EXECUTION_UNSUPPORTED = "CONDITIONAL_ACTION_EXECUTION_UNSUPPORTED"
SHADOW_MARK_DATA_GAP = "SHADOW_MARK_DATA_GAP"


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _china_date(value: datetime | date) -> date:
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return aware.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai")).date()
    return value


def _number(value: Any) -> float | None:
    if value is None or value == "" or value == "-" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive(value: Any) -> float | None:
    parsed = _number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _boolish(value: Any) -> bool:
    """Parse provider flags without treating the string ``"false"`` as true."""

    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc_naive(value).isoformat() if _utc_naive(value) else None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value") and not isinstance(value, (str, bytes, int, float, bool)):
        return _json_safe(value.value)
    return value


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used by observation hashes."""

    return json.dumps(_json_safe(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _result_payload(run: AnalysisRun) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = run.structured_result_json if isinstance(run.structured_result_json, dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
    return (
        result if isinstance(result, dict) else {},
        workflow,
        payload,
    )


def _quality_status(result: Mapping[str, Any]) -> str:
    gate = result.get("quality_gate") if isinstance(result.get("quality_gate"), Mapping) else {}
    grade = str(result.get("data_quality_grade") or gate.get("grade") or "F").upper()
    if str(gate.get("status") or "").lower() == "blocked" or grade in {"D", "F"}:
        return "BLOCKED"
    if grade == "C":
        return "DEGRADED"
    return "VALID"


def _confidence(value: Any) -> float:
    numeric = _number(value)
    if numeric is not None:
        return max(0.0, min(1.0, numeric / 100.0 if numeric > 1 else numeric))
    return {"high": 0.9, "medium": 0.7, "low": 0.4}.get(str(value or "").lower(), 0.0)


def _canonical_action(value: Any) -> str:
    action = str(value or "watch").strip().lower()
    return {
        "conditional_buy": "conditional_add",
        "new": "new_position",
        "trim": "reduce",
        "exit_all": "exit",
        "wait": "hold",
        "observe": "watch_only",
        "watch": "watch_only",
        "hold_only": "hold",
        "加仓": "add",
        "条件加仓": "conditional_add",
        "减仓": "reduce",
        "卖出": "sell",
        "清仓": "exit",
        "买入": "buy",
        "持有": "hold",
        "观察": "watch_only",
    }.get(action, action)


def _final_action(result: Mapping[str, Any], quality_status: str) -> tuple[str, str]:
    gate = result.get("decision_gate") if isinstance(result.get("decision_gate"), Mapping) else {}
    raw = str(
        gate.get("portfolio_action")
        or result.get("final_action")
        or result.get("final_rating")
        or (result.get("portfolio_manager_final") or {}).get("portfolio_rating")
        or "NO_ACTION"
    )
    normalized = raw.upper().replace("-", "_").replace(" ", "_")
    if normalized in {"ACTION", "PORTFOLIO_ACTION", "MIXED_ACTION", "NEW_POSITION_ACTION", "BUY"}:
        return "ACTION", raw
    if normalized in {"BLOCKED", "WATCH_ONLY", "DECISION_BLOCKED", "BLOCKED_FOR_ACTION"} or quality_status == "BLOCKED":
        return "DECISION_BLOCKED", raw
    if normalized in {"NO_ACTION", "HOLD_ONLY", "HOLD", "NONE"}:
        return "NO_ACTION", raw
    # The normalized production result is expected to contain decision_gate.
    # For legacy rows, use the existing contract helper only as a fallback.
    try:
        from ..decision_contract import has_actionable_portfolio_change

        return ("ACTION" if has_actionable_portfolio_change(dict(result)) else "NO_ACTION"), raw
    except Exception:
        return "NO_ACTION", raw


def _market_payload(payload: Mapping[str, Any], workflow: Mapping[str, Any]) -> dict[str, Any]:
    market = payload.get("market_snapshot")
    if not isinstance(market, Mapping):
        market = workflow.get("market_snapshot")
    return dict(market) if isinstance(market, Mapping) else {}


def _market_quote(market: Mapping[str, Any], code: str) -> dict[str, Any]:
    quotes = market.get("quotes")
    if isinstance(quotes, Mapping):
        candidate = quotes.get(code)
        if isinstance(candidate, Mapping):
            return dict(candidate)
    if isinstance(quotes, list):
        for item in quotes:
            if isinstance(item, Mapping) and normalize_security_code(item.get("code")) == code:
                return dict(item)
    return {}


def _reference_price(row: Mapping[str, Any], market: Mapping[str, Any], code: str) -> tuple[float | None, str]:
    quote = _market_quote(market, code)
    price = _positive(quote.get("price") or quote.get("last") or quote.get("close"))
    if price is not None:
        basis = str(
            quote.get("price_basis")
            or quote.get("adjustment")
            or (quote.get("metadata") or {}).get("price_basis")
            or "RAW_QUOTE"
        ).upper().replace("-", "_")
        return price, basis
    # This is only a reference/drift value, never a fill source.  Prefer
    # server-owned lineage fields before tolerating a normalized result field.
    lineage = row.get("lineage") if isinstance(row.get("lineage"), Mapping) else {}
    price = _positive(lineage.get("quote_price")) or _positive(row.get("reference_price"))
    basis = str(lineage.get("quote_price_basis") or row.get("reference_price_basis") or "UNKNOWN").upper()
    return price, basis


def _action_rows(result: Mapping[str, Any], payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    market = _market_payload(payload, {})
    rows: list[dict[str, Any]] = []
    holdings = result.get("today_actions") or result.get("holdings") or []
    for raw in holdings:
        if not isinstance(raw, Mapping):
            continue
        action = _canonical_action(raw.get("action") or raw.get("recommended_action") or raw.get("type"))
        if action not in ACTIONABLE_ACTIONS:
            continue
        code = normalize_security_code(raw.get("code") or raw.get("security_code"))
        if not code:
            continue
        price, basis = _reference_price(raw, market, code)
        rows.append({
            "source_type": "HOLDING",
            "code": code,
            "name": raw.get("name") or raw.get("security_name"),
            "security_type": raw.get("security_type") or raw.get("type"),
            "etf_category": raw.get("etf_category"),
            "action": action,
            "side": "BUY" if action in BUY_ACTIONS else "SELL",
            "target_qty": _positive(raw.get("target_qty") or raw.get("recommended_qty") or raw.get("quantity") or raw.get("qty") or raw.get("proposed_qty")),
            "target_notional": _positive(raw.get("target_notional") or raw.get("notional")),
            "target_weight": _number(raw.get("target_weight") or raw.get("weight")),
            "reference_price": price,
            "reference_price_basis": basis,
            "candidate_id": None,
            "source": dict(raw),
        })
    candidates = result.get("candidates") if "candidates" in result else result.get("buy_candidates")
    for raw in candidates or []:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("buyable") is False or raw.get("actionable") is False:
            continue
        gate = str(raw.get("portfolio_gate") or raw.get("gate_status") or "").upper()
        if gate in {"BLOCKED", "NOT_EVALUATED_V3"}:
            continue
        stage = str(raw.get("stage") or raw.get("candidate_engine_stage") or "").upper()
        action = _canonical_action(raw.get("action") or raw.get("recommended_action") or raw.get("candidate_type") or ("new_position" if stage == "ACTION" else ""))
        if action not in BUY_ACTIONS:
            continue
        code = normalize_security_code(raw.get("code") or raw.get("security_code"))
        if not code:
            continue
        price, basis = _reference_price(raw, market, code)
        rows.append({
            "source_type": "CANDIDATE",
            "code": code,
            "name": raw.get("name") or raw.get("security_name"),
            "security_type": raw.get("security_type") or raw.get("type"),
            "etf_category": raw.get("etf_category"),
            "action": action,
            "side": "BUY",
            "target_qty": _positive(raw.get("target_qty") or raw.get("recommended_qty") or raw.get("quantity") or raw.get("qty") or raw.get("proposed_qty")),
            "target_notional": _positive(raw.get("target_notional") or raw.get("notional")),
            "target_weight": _number(raw.get("target_weight") or raw.get("weight") or raw.get("probe_weight")),
            "reference_price": price,
            "reference_price_basis": basis,
            "candidate_id": raw.get("id") or raw.get("candidate_id") or raw.get("score_id"),
            "buyable": raw.get("buyable"),
            "actionable": raw.get("actionable"),
            "portfolio_gate": raw.get("portfolio_gate") or raw.get("gate_status"),
            "stage": raw.get("stage"),
            "candidate_engine_stage": raw.get("candidate_engine_stage"),
            "source": dict(raw),
        })
    return rows


def _selected_candidate_ids(result: Mapping[str, Any]) -> list[Any]:
    values: list[Any] = []
    candidates = result.get("candidates") if "candidates" in result else result.get("buy_candidates")
    for row in candidates or []:
        if not isinstance(row, Mapping):
            continue
        value = row.get("id") or row.get("candidate_id") or row.get("score_id")
        if value is not None:
            values.append(value)
    return values


def _candidate_run_id(db: Session, result: Mapping[str, Any], workflow: Mapping[str, Any], *, user_id: int, portfolio_id: int) -> int | None:
    context = workflow.get("candidate_context")
    context = context if isinstance(context, Mapping) else {}
    run = context.get("run") if isinstance(context.get("run"), Mapping) else {}
    value = context.get("run_id") or run.get("id")
    try:
        candidate_id = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    row = db.execute(select(CandidateRun).where(
        CandidateRun.id == candidate_id,
        CandidateRun.user_id == user_id,
        CandidateRun.portfolio_id == portfolio_id,
    )).scalar_one_or_none()
    return row.id if row is not None else None


def _trigger_event(db: Session, job: AnalysisJob) -> TriggerEvent | None:
    context = job.context_json if isinstance(job.context_json, Mapping) else {}
    ids = context.get("trigger_event_ids") or []
    if not ids and context.get("trigger_event_id") is not None:
        ids = [context.get("trigger_event_id")]
    for value in reversed(list(ids)):
        try:
            event_id = int(value)
        except (TypeError, ValueError):
            continue
        row = db.execute(select(TriggerEvent).where(
            TriggerEvent.id == event_id,
            TriggerEvent.user_id == job.user_id,
            TriggerEvent.portfolio_id == job.portfolio_id,
        )).scalar_one_or_none()
        if row is not None:
            return row
    return None


def _model_lineage(db: Session, run: AnalysisRun) -> tuple[str | None, str | None]:
    if run.model_profile_id is None:
        return None, None
    profile = db.get(ModelProfile, run.model_profile_id)
    if profile is None:
        return None, None
    provider = db.get(ModelProvider, profile.provider_id)
    return (provider.provider if provider else None), profile.model_name


def _market_lineage(result: Mapping[str, Any], workflow: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    market = _market_payload(payload, workflow)
    context = workflow.get("candidate_context") if isinstance(workflow.get("candidate_context"), Mapping) else {}
    run = context.get("run") if isinstance(context.get("run"), Mapping) else {}
    portfolio_engine = result.get("portfolio_engine") if isinstance(result.get("portfolio_engine"), Mapping) else {}
    portfolio_context = portfolio_engine.get("portfolio_context") if isinstance(portfolio_engine.get("portfolio_context"), Mapping) else {}
    values = {
        "market_snapshot_id": market.get("snapshot_id") or market.get("market_snapshot_id") or context.get("market_snapshot_id") or run.get("market_snapshot_id"),
        "market_score_snapshot_id": market.get("market_score_snapshot_id") or context.get("market_score_snapshot_id") or run.get("market_score_snapshot_id"),
        "market_metric_snapshot_id": market.get("market_metric_snapshot_id") or context.get("market_metric_snapshot_id") or run.get("market_metric_snapshot_id"),
        "market_regime": result.get("market_regime") or portfolio_context.get("market_regime") or context.get("market_regime") or market.get("regime"),
        "market_score": _number(result.get("market_score") or portfolio_context.get("market_score") or market.get("display_score") or market.get("market_score")),
        "market_quality": str(result.get("market_quality") or market.get("quality_grade") or market.get("quality_status") or "MISSING").upper(),
    }
    return {key: value for key, value in values.items() if value is not None}


def _candidate_vetoes(
    result: Mapping[str, Any],
    workflow: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Keep deterministic Candidate ACTIONs that never became final actions."""

    context = workflow.get("candidate_context") if isinstance(workflow.get("candidate_context"), Mapping) else {}
    deterministic = context.get("action") if isinstance(context, Mapping) else []
    if not isinstance(deterministic, list):
        deterministic = []
    final_candidates = result.get("candidates") if "candidates" in result else result.get("buy_candidates")
    accepted_codes = {
        normalize_security_code(row.get("code"))
        for row in final_candidates or []
        if isinstance(row, Mapping) and normalize_security_code(row.get("code"))
    }
    vetoes: list[dict[str, Any]] = []
    for raw in deterministic:
        if not isinstance(raw, Mapping):
            continue
        code = normalize_security_code(raw.get("code"))
        stage = str(raw.get("stage") or raw.get("candidate_engine_stage") or "").upper()
        if not code or stage != "ACTION" or code in accepted_codes:
            continue
        vetoes.append({
            **dict(raw),
            "code": code,
            "veto_reason": "FINAL_PORTFOLIO_DECISION_DID_NOT_SELECT_CANDIDATE",
        })
    return vetoes


def _data_coverage(result: Mapping[str, Any], workflow: Mapping[str, Any]) -> float | None:
    values = [
        workflow.get("data_coverage"),
        (workflow.get("candidate_context") or {}).get("quote_coverage") if isinstance(workflow.get("candidate_context"), Mapping) else None,
        (workflow.get("candidate_context") or {}).get("coverage") if isinstance(workflow.get("candidate_context"), Mapping) else None,
        (result.get("candidate_engine") or {}).get("quote_coverage") if isinstance(result.get("candidate_engine"), Mapping) else None,
    ]
    for value in values:
        parsed = _number(value)
        if parsed is not None:
            return parsed / 100.0 if parsed > 1 else parsed
    return None


def _observation_values(
    db: Session,
    run: AnalysisRun,
    *,
    captured_at: datetime | None = None,
    decision_kind: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    job = run.job or db.get(AnalysisJob, run.job_id)
    if job is None or str(job.status).lower() != "succeeded":
        return None
    snapshot = db.get(PortfolioSnapshot, run.portfolio_snapshot_id)
    portfolio = db.get(Portfolio, job.portfolio_id)
    if snapshot is None or portfolio is None:
        return None
    if snapshot.user_id != run.user_id or snapshot.portfolio_id != job.portfolio_id or portfolio.user_id != run.user_id:
        return None
    result, workflow, payload = _result_payload(run)
    if not result:
        return None
    quality = _quality_status(result)
    final_action, raw_final_action = _final_action(result, quality)
    finalized_at = _utc_naive(job.finished_at) or _utc_naive(run.created_at) or _now()
    observed_at = max(_utc_naive(captured_at) or _now(), finalized_at)
    event = _trigger_event(db, job)
    kind = decision_kind or (
        "TRIGGER" if event is not None or str(job.trigger_type or "").lower() in {"trigger", "realtime_trigger"}
        else "CHECKPOINT" if job.checkpoint else "MANUAL"
    )
    if kind not in {"CHECKPOINT", "TRIGGER", "MANUAL"}:
        kind = "MANUAL"
    market = _market_lineage(result, workflow, payload)
    actions = _action_rows(result, payload)
    candidate_vetoes = _candidate_vetoes(result, workflow)
    conditional_actions = [row for row in actions if row.get("action") == "conditional_add"]
    candidate_run_id = _candidate_run_id(db, result, workflow, user_id=run.user_id, portfolio_id=job.portfolio_id)
    memory = db.execute(select(DecisionMemory).where(DecisionMemory.analysis_run_id == run.id)).scalar_one_or_none()
    provider, model_name = _model_lineage(db, run)
    runtime = payload.get("skill_runtime") if isinstance(payload.get("skill_runtime"), Mapping) else {}
    decision_contract = runtime.get("decision_contract") if isinstance(runtime.get("decision_contract"), Mapping) else {}
    gate = result.get("decision_gate") if isinstance(result.get("decision_gate"), Mapping) else {}
    gate_reasons = gate.get("blocking_reasons") or gate.get("warnings") or []
    reason_codes = list(dict.fromkeys([
        *(str(item) for item in (result.get("reason_codes") or []) if item),
        *(str(item) for item in (gate_reasons or []) if item),
        *( ["QUALITY_BLOCKED"] if quality == "BLOCKED" else [] ),
    ]))
    if conditional_actions:
        reason_codes.append(CONDITIONAL_ACTION_EXECUTION_UNSUPPORTED)
    checkpoint = str(job.checkpoint).strip() if job.checkpoint else None
    trigger_context = job.context_json if isinstance(job.context_json, Mapping) else {}
    calculation_key = ":".join([
        "live",
        str(run.user_id),
        str(job.portfolio_id),
        kind,
        checkpoint or (event.trigger_type if event else "manual"),
        str(job.id),
        str(run.id),
        str(decision_contract.get("version") or CONTRACT_VERSION),
    ])
    source_lineage = {
        "analysis_job_id": job.id,
        "analysis_run_id": run.id,
        "portfolio_id": job.portfolio_id,
        "portfolio_snapshot_id": run.portfolio_snapshot_id,
        "decision_memory_id": memory.id if memory else None,
        "candidate_run_id": candidate_run_id,
        "market": market,
        "trigger": {
            "event_id": event.id if event else None,
            "trigger_type": event.trigger_type if event else None,
            "priority": event.priority if event else None,
            "reason": event.evidence_json if event else trigger_context.get("trigger_reason"),
            "context": dict(trigger_context),
        },
        "analysis_mode": job.mode,
        "candidate_vetoes": candidate_vetoes,
    }
    if conditional_actions:
        source_lineage["shadow_execution"] = {
            "conditional_action_execution": "UNSUPPORTED_V1",
            "conditional_action_codes": [row.get("code") for row in conditional_actions],
        }
    source_lineage = _json_safe(source_lineage)
    facts = {
        "user_id": run.user_id,
        "portfolio_id": job.portfolio_id,
        "trade_date": _china_date(finalized_at),
        "decision_kind": kind,
        "decision_checkpoint": checkpoint,
        "trigger_event_id": event.id if event else None,
        "trigger_type": event.trigger_type if event else None,
        "trigger_priority": event.priority if event else None,
        "source_analysis_job_id": job.id,
        "source_analysis_run_id": run.id,
        "portfolio_snapshot_id": run.portfolio_snapshot_id,
        "candidate_run_id": candidate_run_id,
        "parameter_set_version_id": run.parameter_set_version_id,
        "parameter_set_version": run.parameter_set_version,
        "parameter_set_hash": run.parameter_set_hash,
        "runtime_contract_version": str(decision_contract.get("version") or CONTRACT_VERSION),
        "decision_contract_version": str(decision_contract.get("version") or CONTRACT_VERSION),
        "runtime_prompt_version": runtime.get("prompt_version"),
        "runtime_prompt_sha256": runtime.get("prompt_sha256") or runtime.get("runtime_prompt_sha256"),
        "skill_version": runtime.get("version"),
        "skill_sha256": runtime.get("runtime_sha256"),
        "model_provider": provider,
        "model_name": model_name,
        "final_action": final_action,
        "raw_final_action": raw_final_action,
        "final_reason_codes": reason_codes,
        "selected_actions": actions,
        "selected_candidate_ids": _selected_candidate_ids(result),
        "market": market,
        "portfolio_quality": str(
            (gate.get("risk_context") or {}).get("portfolio_quality")
            if isinstance(gate.get("risk_context"), Mapping)
            else ""
        ).upper() or None,
        "confidence": _confidence(result.get("confidence")),
        "data_coverage": _data_coverage(result, workflow),
        "decision_started_at": _utc_naive(job.started_at),
        "decision_finalized_at": finalized_at,
        "source_lineage": source_lineage,
        "deterministic_core_hash": (result.get("deterministic_core_hash") or workflow.get("deterministic_core_hash")),
    }
    hash_payload = {
        "calculation_key": calculation_key,
        "decision_inputs_lineage": source_lineage,
        "final_action": final_action,
        "selected_actions": actions,
        "selected_candidate_ids": facts["selected_candidate_ids"],
        "parameter_set_version": run.parameter_set_version,
        "parameter_set_hash": run.parameter_set_hash,
        "runtime_contract_version": facts["runtime_contract_version"],
        "decision_contract_version": facts["decision_contract_version"],
        "market_regime": market.get("market_regime"),
        "market_score": market.get("market_score"),
        "portfolio_snapshot_id": run.portfolio_snapshot_id,
        "decision_finalized_at": finalized_at,
    }
    values = {
        "user_id": run.user_id,
        "portfolio_id": job.portfolio_id,
        "trade_date": facts["trade_date"],
        "decision_kind": kind,
        "decision_checkpoint": checkpoint,
        "trigger_type": event.trigger_type if event else None,
        "trigger_event_id": event.id if event else None,
        "trigger_priority": event.priority if event else None,
        "trigger_reason": str(event.evidence_json or "")[:4000] if event else trigger_context.get("trigger_reason"),
        "source_analysis_job_id": job.id,
        "source_analysis_run_id": run.id,
        "decision_memory_id": memory.id if memory else None,
        "candidate_run_id": candidate_run_id,
        "portfolio_snapshot_id": run.portfolio_snapshot_id,
        "market_snapshot_id": market.get("market_snapshot_id"),
        "market_score_snapshot_id": market.get("market_score_snapshot_id"),
        "market_metric_snapshot_id": market.get("market_metric_snapshot_id"),
        "parameter_set_version_id": run.parameter_set_version_id,
        "parameter_set_version": run.parameter_set_version or "LEGACY_PRE_GOVERNANCE",
        "parameter_set_hash": run.parameter_set_hash,
        "runtime_contract_version": facts["runtime_contract_version"],
        "decision_contract_version": facts["decision_contract_version"],
        "runtime_prompt_version": runtime.get("prompt_version"),
        "runtime_prompt_sha256": runtime.get("runtime_sha256"),
        "skill_version": runtime.get("version"),
        "skill_sha256": runtime.get("runtime_sha256"),
        "market_engine_version": (result.get("market_engine") or {}).get("calculation_version") if isinstance(result.get("market_engine"), Mapping) else None,
        "candidate_engine_version": (result.get("candidate_engine") or {}).get("calculation_version") if isinstance(result.get("candidate_engine"), Mapping) else None,
        "model_provider": provider,
        "model_name": model_name,
        "final_action": final_action,
        "raw_final_action": raw_final_action,
        "final_reason_codes_json": reason_codes,
        "selected_actions_json": actions,
        "selected_candidate_ids_json": facts["selected_candidate_ids"],
        "market_regime": market.get("market_regime"),
        "market_score": market.get("market_score"),
        "market_quality": market.get("market_quality"),
        "portfolio_quality": facts["portfolio_quality"],
        "confidence": facts["confidence"],
        "data_coverage": facts["data_coverage"],
        "decision_started_at": facts["decision_started_at"],
        "decision_finalized_at": finalized_at,
        "captured_at": observed_at,
        "source_lineage_json": source_lineage,
        "deterministic_core_hash": facts["deterministic_core_hash"],
        "observation_hash": sha256_json(hash_payload),
        "calculation_key": calculation_key,
        "quality_status": quality,
        "live_evidence_eligibility": "DIAGNOSTIC_ONLY",
        "calculation_version": OBSERVATION_VERSION,
    }
    return values, {"run": run, "job": job, "result": result, "workflow": workflow, "payload": payload, "event": event}


def capture_live_decision_observation(
    db: Session,
    analysis_run: AnalysisRun | int,
    *,
    captured_at: datetime | None = None,
    decision_kind: str | None = None,
    create_shadow_intents: bool = True,
    create_outcomes: bool = True,
) -> LiveDecisionObservation | None:
    """Capture one successful final production decision idempotently."""

    run = analysis_run if isinstance(analysis_run, AnalysisRun) else db.get(AnalysisRun, analysis_run)
    if run is None:
        return None
    built = _observation_values(db, run, captured_at=captured_at, decision_kind=decision_kind)
    if built is None:
        return None
    values, context = built
    existing = db.execute(select(LiveDecisionObservation).where(
        LiveDecisionObservation.calculation_key == values["calculation_key"]
    )).scalar_one_or_none()
    if existing is None:
        observation = LiveDecisionObservation(**values)
        try:
            # A nested transaction keeps a concurrent duplicate from rolling
            # back unrelated work in the caller's session.
            with db.begin_nested():
                db.add(observation)
                db.flush()
        except IntegrityError:
            observation = db.execute(select(LiveDecisionObservation).where(
                LiveDecisionObservation.calculation_key == values["calculation_key"]
            )).scalar_one_or_none()
            if observation is None:
                raise
    else:
        observation = existing
    if create_outcomes:
        ensure_live_outcomes(db, observation)
    if create_shadow_intents and observation.final_action == "ACTION":
        account = db.execute(select(ShadowAccount).where(
            ShadowAccount.user_id == observation.user_id,
            ShadowAccount.source_portfolio_id == observation.portfolio_id,
            ShadowAccount.status == "ACTIVE",
        )).scalar_one_or_none()
        if account is not None:
            ensure_shadow_order_intents(db, observation, account=account)
    return observation


capture_production_decision = capture_live_decision_observation


def _snapshot_for_account(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    snapshot_id: int | None,
) -> PortfolioSnapshot:
    query = select(PortfolioSnapshot).where(
        PortfolioSnapshot.user_id == user_id,
        PortfolioSnapshot.portfolio_id == portfolio_id,
        PortfolioSnapshot.status == "confirmed",
    )
    if snapshot_id is not None:
        query = query.where(PortfolioSnapshot.id == snapshot_id)
    row = db.execute(query.order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc())).scalars().first()
    if row is None:
        raise ValueError("confirmed_snapshot_not_found")
    return row


def _security_metadata(db: Session, code: str) -> SecurityMaster | None:
    return db.execute(select(SecurityMaster).where(
        SecurityMaster.market == "CN",
        SecurityMaster.code == code,
    )).scalars().first()


def _initial_equity(snapshot: PortfolioSnapshot) -> float:
    cash = _number(snapshot.broker_available_cash) or 0.0
    market_value = _number(snapshot.total_market_value)
    if market_value is None:
        market_value = sum(
            (_number(row.market_value) or ((_number(row.qty) or 0.0) * (_number(row.screenshot_price) or 0.0)))
            for row in snapshot.holdings
            if row.code
        )
    return max(0.0, cash + market_value)


def _append_ledger(
    db: Session,
    *,
    entry_key: str,
    account: ShadowAccount,
    entry_type: str,
    occurred_at: datetime,
    code: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    gross_amount: float | None = None,
    commission: float | None = None,
    tax: float | None = None,
    cash_delta: float | None = None,
    sellable_at: datetime | None = None,
    decision_observation_id: int | None = None,
    order_intent_id: int | None = None,
    fill_id: int | None = None,
    payload: Mapping[str, Any] | None = None,
) -> ShadowLedgerEntry:
    existing = db.execute(select(ShadowLedgerEntry).where(ShadowLedgerEntry.entry_key == entry_key)).scalar_one_or_none()
    if existing is not None:
        return existing
    row = ShadowLedgerEntry(
        entry_key=entry_key,
        shadow_account_id=account.id,
        shadow_generation=account.shadow_generation,
        entry_type=entry_type,
        code=code,
        quantity=quantity,
        price=price,
        gross_amount=gross_amount,
        commission=commission,
        tax=tax,
        cash_delta=cash_delta,
        sellable_at=_utc_naive(sellable_at),
        decision_observation_id=decision_observation_id,
        order_intent_id=order_intent_id,
        fill_id=fill_id,
        occurred_at=_utc_naive(occurred_at) or _now(),
        payload_json=dict(payload or {}),
    )
    db.add(row)
    db.flush()
    return row


def _seed_shadow_generation(db: Session, account: ShadowAccount, snapshot: PortfolioSnapshot, *, now: datetime) -> None:
    account.current_cash = _number(snapshot.broker_available_cash) or 0.0
    account.starting_cash = account.current_cash
    config = dict(account.config_json or {})
    generations = dict(config.get("generations") or {})
    generations[str(account.shadow_generation)] = {
        "starting_cash": account.current_cash,
        "starting_equity": _initial_equity(snapshot),
        "initialized_from_snapshot_id": snapshot.id,
        "initialized_at": _json_safe(now),
    }
    config.update({
        "paper_only": True,
        "starting_equity": _initial_equity(snapshot),
        "initialized_from_snapshot_id": snapshot.id,
        "execution_policy": "NEXT_EXECUTABLE_QUOTE",
        "slippage_not_modeled": True,
        "stock_settlement": "T_PLUS_1",
        "unknown_etf_settlement": "T_PLUS_1_CONSERVATIVE",
        "hard_caps": {"stock": 0.20, "sector_theme_etf": 0.30},
        "generations": generations,
    })
    account.config_json = config
    _append_ledger(
        db,
        entry_key=f"shadow:{account.id}:generation:{account.shadow_generation}:initial-cash",
        account=account,
        entry_type="INITIAL_CASH",
        occurred_at=now,
        cash_delta=account.current_cash,
        payload={"source_snapshot_id": snapshot.id, "paper_only": True},
    )
    for holding in snapshot.holdings:
        code = normalize_security_code(holding.code)
        qty = _positive(holding.qty) or 0.0
        if not code or qty <= 0:
            continue
        security = _security_metadata(db, code)
        security_type = str(
            holding.extra_json.get("security_type") if isinstance(holding.extra_json, Mapping) else ""
        ).upper() or (str(security.security_type).upper() if security else None)
        etf_category = security.etf_category if security else None
        sellable = min(qty, _number(holding.available_qty) if holding.available_qty is not None else qty)
        sellable = max(0.0, sellable)
        cost = _number(holding.cost) or 0.0
        _append_ledger(
            db,
            entry_key=f"shadow:{account.id}:generation:{account.shadow_generation}:initial-position:{code}",
            account=account,
            entry_type="INITIAL_POSITION",
            occurred_at=now,
            code=code,
            quantity=qty,
            price=cost,
            gross_amount=_number(holding.market_value),
            sellable_at=now,
            payload={
                "name": holding.name,
                "security_type": security_type,
                "etf_category": etf_category,
                "sellable_quantity": sellable,
                "source_snapshot_id": snapshot.id,
            },
        )


def create_shadow_account(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int | None = None,
    source_portfolio_id: int | None = None,
    snapshot_id: int | None = None,
    name: str = "影子验证",
    now: datetime | None = None,
) -> ShadowAccount:
    """Create an explicitly requested paper-only account from a confirmed snapshot."""

    owner_portfolio_id = portfolio_id if portfolio_id is not None else source_portfolio_id
    if owner_portfolio_id is None:
        raise ValueError("portfolio_id_required")
    portfolio = db.execute(select(Portfolio).where(
        Portfolio.id == owner_portfolio_id,
        Portfolio.user_id == user_id,
    )).scalar_one_or_none()
    if portfolio is None:
        raise ValueError("portfolio_not_found")
    active = db.execute(select(ShadowAccount).where(
        ShadowAccount.user_id == user_id,
        ShadowAccount.source_portfolio_id == owner_portfolio_id,
        ShadowAccount.status == "ACTIVE",
    )).scalar_one_or_none()
    if active is not None:
        raise ValueError("active_shadow_account_exists")
    snapshot = _snapshot_for_account(db, user_id=user_id, portfolio_id=owner_portfolio_id, snapshot_id=snapshot_id)
    moment = _utc_naive(now) or _now()
    account = ShadowAccount(
        user_id=user_id,
        source_portfolio_id=owner_portfolio_id,
        name=(name or "影子验证").strip()[:128],
        status="ACTIVE",
        mode="FOLLOW_FINAL_ACTIONS",
        base_currency=portfolio.currency or "CNY",
        paper_only=True,
        initialized_from_snapshot_id=snapshot.id,
        initialized_at=moment,
        starting_cash=_number(snapshot.broker_available_cash) or 0.0,
        current_cash=_number(snapshot.broker_available_cash) or 0.0,
        reserved_cash=0.0,
        shadow_generation=1,
        execution_contract_version=SHADOW_EXECUTION_VERSION,
        expires_policy="NEXT_TRADING_DAY_CLOSE",
        config_json={"paper_only": True},
    )
    db.add(account)
    db.flush()
    _seed_shadow_generation(db, account, snapshot, now=moment)
    refresh_shadow_materialized_state(db, account, as_of=moment)
    return account


def pause_shadow_account(db: Session, account: ShadowAccount, *, now: datetime | None = None) -> ShadowAccount:
    if account.status == "CLOSED":
        raise ValueError("shadow_account_closed")
    account.status = "PAUSED"
    account.paused_at = _utc_naive(now) or _now()
    account.version = int(account.version or 0) + 1
    db.flush()
    return account


def resume_shadow_account(db: Session, account: ShadowAccount) -> ShadowAccount:
    if account.status == "CLOSED":
        raise ValueError("shadow_account_closed")
    account.status = "ACTIVE"
    account.paused_at = None
    account.version = int(account.version or 0) + 1
    db.flush()
    return account


def rebase_shadow_account(
    db: Session,
    account: ShadowAccount,
    *,
    snapshot_id: int | None = None,
    now: datetime | None = None,
) -> ShadowAccount:
    """Start a new generation while preserving all old facts."""

    if account.status == "CLOSED":
        raise ValueError("shadow_account_closed")
    snapshot = _snapshot_for_account(
        db,
        user_id=account.user_id,
        portfolio_id=account.source_portfolio_id,
        snapshot_id=snapshot_id,
    )
    moment = _utc_naive(now) or _now()
    old_generation = account.shadow_generation
    account.shadow_generation = old_generation + 1
    account.initialized_from_snapshot_id = snapshot.id
    account.initialized_at = moment
    account.status = "ACTIVE"
    account.paused_at = None
    account.version = int(account.version or 0) + 1
    _seed_shadow_generation(db, account, snapshot, now=moment)
    refresh_shadow_materialized_state(db, account, as_of=moment)
    logger.info(
        "shadow_rebase account=%s old_generation=%s new_generation=%s snapshot=%s",
        account.id,
        old_generation,
        account.shadow_generation,
        snapshot.id,
    )
    return account


def _trading_days_after(db: Session, day: date, horizon: int) -> list[date]:
    return list(db.execute(select(TradingCalendar.trade_date).where(
        TradingCalendar.market == "CN",
        TradingCalendar.trade_date > day,
        TradingCalendar.is_open.is_(True),
    ).order_by(TradingCalendar.trade_date.asc()).limit(horizon)).scalars())


def _next_trading_open(db: Session, day: date) -> datetime:
    dates = _trading_days_after(db, day, 1)
    target = dates[0] if dates else day + timedelta(days=1)
    # 09:30 Shanghai is 01:30 UTC; internal datetimes are UTC-naive.
    return datetime.combine(target, time(1, 30))


def _next_trading_close(db: Session, day: date) -> datetime:
    dates = _trading_days_after(db, day, 1)
    target = dates[0] if dates else day + timedelta(days=1)
    # DateTime columns in this project are UTC-naive.  15:00 Shanghai is 07:00 UTC.
    return datetime.combine(target, time(7, 0))


def _earliest_executable_at(db: Session, finalized_at: datetime, trade_date: date) -> datetime:
    """Move an EOD decision to the next session; intraday stays immediately eligible."""

    local = (_utc_naive(finalized_at) or _now()).replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Shanghai"))
    if local.time() >= time(15, 0):
        return _next_trading_open(db, trade_date)
    return _utc_naive(finalized_at) or _now()


def ensure_shadow_order_intents(
    db: Session,
    observation: LiveDecisionObservation,
    *,
    account: ShadowAccount | None = None,
) -> list[ShadowOrderIntent]:
    """Create intents only for final Portfolio ACTION rows."""

    if observation.final_action != "ACTION":
        return []
    account = account or db.execute(select(ShadowAccount).where(
        ShadowAccount.user_id == observation.user_id,
        ShadowAccount.source_portfolio_id == observation.portfolio_id,
        ShadowAccount.status == "ACTIVE",
    )).scalar_one_or_none()
    if account is None:
        return []
    finalized = _utc_naive(observation.decision_finalized_at) or _now()
    earliest_executable_at = _earliest_executable_at(db, finalized, observation.trade_date)
    expires_at = _next_trading_close(db, observation.trade_date)
    rows: list[ShadowOrderIntent] = []
    for index, raw in enumerate(observation.selected_actions_json or []):
        if not isinstance(raw, Mapping):
            continue
        if _canonical_action(raw.get("action")) == "conditional_add":
            continue
        code = normalize_security_code(raw.get("code"))
        side = str(raw.get("side") or "").upper()
        if not code or side not in {"BUY", "SELL"}:
            continue
        if raw.get("source_type") == "CANDIDATE" and (
            raw.get("buyable") is False or raw.get("actionable") is False or str(raw.get("portfolio_gate") or "").upper() == "BLOCKED"
        ):
            continue
        key = f"shadow:{account.id}:generation:{account.shadow_generation}:observation:{observation.id}:action:{index}:{code}:{side}"
        existing = db.execute(select(ShadowOrderIntent).where(ShadowOrderIntent.idempotency_key == key)).scalar_one_or_none()
        if existing is not None:
            rows.append(existing)
            continue
        row = ShadowOrderIntent(
            shadow_account_id=account.id,
            shadow_generation=account.shadow_generation,
            decision_observation_id=observation.id,
            action_index=index,
            code=code,
            security_type=raw.get("security_type"),
            side=side,
            target_qty=_positive(raw.get("target_qty")),
            target_notional=_positive(raw.get("target_notional")),
            target_weight=_number(raw.get("target_weight")),
            decision_reference_price=_positive(raw.get("reference_price")),
            decision_reference_basis=str(raw.get("reference_price_basis") or "UNKNOWN"),
            decision_finalized_at=finalized,
            earliest_executable_at=earliest_executable_at,
            status="PENDING",
            reason_codes_json=["PAPER_ONLY", "WAIT_FOR_FUTURE_QUOTE"],
            expires_at=expires_at,
            idempotency_key=key,
        )
        db.add(row)
        db.flush()
        rows.append(row)
    return rows


def persist_live_quote_observation(
    db: Session,
    quote: NormalizedQuote | Mapping[str, Any],
    *,
    captured_at: datetime | None = None,
    captured_at_precision: str | None = None,
    quote_key: str | None = None,
) -> tuple[LiveQuoteObservation, bool]:
    """Persist one quote with application capture time, never provider time as proof."""

    if isinstance(quote, NormalizedQuote):
        data = quote.to_dict()
        actual_capture = _utc_naive(captured_at) or _utc_naive(quote.fetched_at) or _now()
    else:
        data = dict(quote)
        actual_capture = _utc_naive(captured_at)
        if actual_capture is None:
            value = data.get("captured_at") or data.get("fetched_at")
            if isinstance(value, datetime):
                actual_capture = _utc_naive(value)
            else:
                # source_timestamp is deliberately not used here: it is a
                # provider fact, not proof that this process captured the quote.
                actual_capture = _now()
    code = normalize_security_code(data.get("code") or data.get("security_code"))
    if not code:
        raise ValueError("quote_code_required")
    price = _number(data.get("price") or data.get("last") or data.get("close"))
    provider = str(data.get("provider") or "").lower()
    source_ref = data.get("source_ref") or data.get("raw_reference") or data.get("source")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else data.get("metadata_json")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    basis = str(data.get("price_basis") or data.get("adjustment") or metadata.get("price_basis") or "RAW_QUOTE").upper()
    precision = str(captured_at_precision or data.get("captured_at_precision") or "EXACT").upper()
    key = quote_key or data.get("quote_key") or sha256_json({
        "market": str(data.get("market") or "CN").upper(),
        "code": code,
        "captured_at": actual_capture,
        "provider": provider,
        "source_ref": source_ref,
        "price": price,
    })
    existing = db.execute(select(LiveQuoteObservation).where(LiveQuoteObservation.quote_key == str(key))).scalar_one_or_none()
    if existing is not None:
        return existing, False
    trade_day = data.get("trade_date")
    if isinstance(trade_day, str):
        try:
            trade_day = date.fromisoformat(trade_day[:10])
        except ValueError:
            trade_day = None
    if not isinstance(trade_day, date):
        trade_day = _china_date(actual_capture)
    quality = data.get("quality_status")
    quality = quality.value if hasattr(quality, "value") else str(quality or "VALID").upper()
    row = LiveQuoteObservation(
        quote_key=str(key),
        market=str(data.get("market") or "CN").upper(),
        exchange=data.get("exchange"),
        code=code,
        security_type=data.get("security_type"),
        trade_date=trade_day,
        captured_at=actual_capture,
        captured_at_precision=precision,
        price=price,
        prev_close=_number(data.get("prev_close")),
        bid=_number(data.get("bid")),
        ask=_number(data.get("ask")),
        volume=_number(data.get("volume")),
        amount=_number(data.get("amount") or data.get("turnover")),
        limit_up=_boolish(data.get("limit_up") or data.get("locked_limit_up") or metadata.get("limit_up")),
        limit_down=_boolish(data.get("limit_down") or data.get("locked_limit_down") or metadata.get("limit_down")),
        suspended=_boolish(data.get("suspended") or data.get("is_suspended") or metadata.get("is_suspended")),
        instrument_active=_boolish(data.get("instrument_active", True)),
        quality_status=quality,
        provider=provider,
        source_ref=str(source_ref)[:255] if source_ref is not None else None,
        price_basis=basis,
        source_snapshot_id=data.get("source_snapshot_id") or metadata.get("snapshot_id"),
        metadata_json={
            **metadata,
            "provider_source_timestamp": _json_safe(data.get("source_timestamp") or data.get("quote_time")),
            "fetched_at": _json_safe(data.get("fetched_at")),
        },
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = db.execute(select(LiveQuoteObservation).where(LiveQuoteObservation.quote_key == str(key))).scalar_one_or_none()
        if existing is None:
            raise
        return existing, False
    return row, True


def _security_for_intent(db: Session, intent: ShadowOrderIntent) -> tuple[str, str | None, SecurityMaster | None]:
    row = _security_metadata(db, intent.code)
    security_type = str(intent.security_type or (row.security_type if row else "UNKNOWN")).upper()
    category = row.etf_category if row else None
    return security_type, category, row


def _settlement_policy(security: SecurityMaster | None, security_type: str) -> str:
    """Resolve settlement conservatively when metadata does not say T+0."""

    kind = str(security_type or "UNKNOWN").upper()
    if kind == "STOCK":
        return "T_PLUS_1"
    if kind != "ETF":
        return "T_PLUS_1_CONSERVATIVE"
    metadata = security.raw_metadata_json if security and isinstance(security.raw_metadata_json, Mapping) else {}
    for key in ("settlement", "settlement_policy", "trading_rule", "etf_settlement"):
        value = metadata.get(key)
        normalized = str(value or "").upper().replace("+", "_PLUS_").replace("-", "_")
        if normalized in {"T_0", "T_PLUS_0", "T0", "TPLUS0"}:
            return "T_PLUS_0"
        if normalized in {"T_1", "T_PLUS_1", "T1", "TPLUS1"}:
            return "T_PLUS_1"
    # SecurityMaster has no authoritative settlement column today, so an ETF
    # without an explicit rule must use the required conservative default.
    return "T_PLUS_1_CONSERVATIVE"


def _state_from_entries(db: Session, account: ShadowAccount, *, generation: int, as_of: datetime | None = None) -> dict[str, Any]:
    cutoff = _utc_naive(as_of) or _now()
    state: dict[str, Any] = {"cash": 0.0, "positions": {}, "ledger_entry_count": 0, "as_of": cutoff}
    entries = db.execute(select(ShadowLedgerEntry).where(
        ShadowLedgerEntry.shadow_account_id == account.id,
        ShadowLedgerEntry.shadow_generation == generation,
        ShadowLedgerEntry.occurred_at <= cutoff,
    ).order_by(ShadowLedgerEntry.occurred_at.asc(), ShadowLedgerEntry.id.asc())).scalars().all()
    for entry in entries:
        state["ledger_entry_count"] += 1
        state["cash"] += _number(entry.cash_delta) or 0.0
        code = normalize_security_code(entry.code)
        if not code:
            continue
        position = state["positions"].setdefault(code, {
            "code": code,
            "name": None,
            "security_type": None,
            "etf_category": None,
            "quantity": 0.0,
            "sellable_quantity": 0.0,
            "average_cost": 0.0,
            "acquired_decision_ids": [],
        })
        quantity = _number(entry.quantity) or 0.0
        price = _number(entry.price) or 0.0
        if entry.entry_type == "INITIAL_POSITION":
            payload = entry.payload_json if isinstance(entry.payload_json, Mapping) else {}
            position["name"] = payload.get("name")
            position["security_type"] = payload.get("security_type")
            position["etf_category"] = payload.get("etf_category")
            position["quantity"] += quantity
            position["sellable_quantity"] += min(quantity, _number(payload.get("sellable_quantity")) if payload.get("sellable_quantity") is not None else quantity)
            position["average_cost"] = price
        elif entry.entry_type == "BUY_FILL":
            before = position["quantity"]
            position["quantity"] += quantity
            position["average_cost"] = ((before * position["average_cost"]) + (quantity * price)) / position["quantity"] if position["quantity"] > 0 else 0.0
            sellable_at = _utc_naive(entry.sellable_at)
            if sellable_at is not None and sellable_at <= cutoff:
                position["sellable_quantity"] += quantity
            if entry.decision_observation_id is not None:
                position["acquired_decision_ids"].append(entry.decision_observation_id)
        elif entry.entry_type == "SELL_FILL":
            position["quantity"] = max(0.0, position["quantity"] - quantity)
            position["sellable_quantity"] = max(0.0, position["sellable_quantity"] - quantity)
    state["cash"] = round(state["cash"], 10)
    state["positions"] = {
        code: row for code, row in state["positions"].items()
        if row["quantity"] > 1e-9
    }
    return state


def rebuild_shadow_state(
    db: Session,
    account: ShadowAccount | int,
    *,
    generation: int | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    row = account if isinstance(account, ShadowAccount) else db.get(ShadowAccount, account)
    if row is None:
        raise ValueError("shadow_account_not_found")
    return _state_from_entries(db, row, generation=generation or row.shadow_generation, as_of=as_of)


def _latest_mark(db: Session, code: str, *, as_of: datetime) -> tuple[float | None, str | None, int | None]:
    quote = db.execute(select(LiveQuoteObservation).where(
        LiveQuoteObservation.code == code,
        LiveQuoteObservation.captured_at <= as_of,
        LiveQuoteObservation.price > 0,
        LiveQuoteObservation.quality_status.in_(tuple(VALID_QUOTE_QUALITY)),
    ).order_by(LiveQuoteObservation.captured_at.desc(), LiveQuoteObservation.id.desc()).limit(1)).scalar_one_or_none()
    if quote is None:
        return None, None, None
    return quote.price, quote.price_basis, quote.id


def refresh_shadow_materialized_state(
    db: Session,
    account: ShadowAccount,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    cutoff = _utc_naive(as_of) or _now()
    # Materialized state is the account's current state.  A historical mark or
    # a rebuild at an earlier cutoff must never move current cash/positions
    # backwards after a later fill already exists.
    state = rebuild_shadow_state(db, account)
    existing = {
        row.code: row for row in db.execute(select(ShadowPosition).where(
            ShadowPosition.shadow_account_id == account.id,
            ShadowPosition.shadow_generation == account.shadow_generation,
        )).scalars().all()
    }
    for code, item in state["positions"].items():
        row = existing.pop(code, None)
        if row is None:
            row = ShadowPosition(
                shadow_account_id=account.id,
                shadow_generation=account.shadow_generation,
                code=code,
                name=item.get("name"),
                security_type=item.get("security_type"),
                etf_category=item.get("etf_category"),
            )
            db.add(row)
        mark, _, _ = _latest_mark(db, code, as_of=cutoff)
        row.quantity = item["quantity"]
        row.sellable_quantity = item["sellable_quantity"]
        row.average_cost = item["average_cost"]
        row.current_mark = mark if mark is not None else row.current_mark
        row.market_value = row.current_mark * row.quantity if row.current_mark is not None else None
        row.unrealized_pnl = (row.current_mark - row.average_cost) * row.quantity if row.current_mark is not None else None
        row.acquired_decision_ids_json = list(dict.fromkeys(item.get("acquired_decision_ids") or []))
        row.updated_at = _now()
    for row in existing.values():
        db.delete(row)
    account.current_cash = state["cash"]
    account.reserved_cash = 0.0
    db.flush()
    return state


def _lot_size(security: SecurityMaster | None, security_type: str) -> int:
    value = int(security.lot_size or 100) if security and security.lot_size else 100
    return max(1, value if security_type in {"STOCK", "ETF"} else 1)


def _floor_lot(quantity: float, lot_size: int) -> float:
    return math.floor(max(0.0, quantity) / lot_size) * lot_size


def _cost_breakdown(side: str, gross: float) -> tuple[float, float, float, bool]:
    commission_bps = settings.PORTFOLIO_BROKER_COMMISSION_BPS
    minimum_commission = settings.PORTFOLIO_MINIMUM_COMMISSION
    sell_tax_bps = settings.PORTFOLIO_SELL_TAX_BPS
    total = transaction_cost_estimate(
        side=side,
        gross_amount=gross,
        commission_bps=commission_bps,
        minimum_commission=minimum_commission,
        sell_tax_bps=sell_tax_bps,
    )
    if commission_bps is None or minimum_commission is None:
        return 0.0, 0.0, 0.0, False
    commission = max(gross * commission_bps / 10_000.0, minimum_commission)
    tax = gross * sell_tax_bps / 10_000.0 if side == "SELL" and sell_tax_bps is not None else 0.0
    return commission, tax, float(total or 0.0), True


def _intent_target_quantity(intent: ShadowOrderIntent, *, price: float, equity: float, current_value: float) -> float | None:
    if intent.target_qty is not None and intent.target_qty > 0:
        return float(intent.target_qty)
    if intent.target_notional is not None and intent.target_notional > 0:
        return float(intent.target_notional) / price
    if intent.target_weight is not None and intent.target_weight >= 0:
        if intent.side == "BUY":
            return max(0.0, equity * float(intent.target_weight) - current_value) / price
        return max(0.0, current_value - equity * float(intent.target_weight)) / price
    return None


def _set_intent_reason(intent: ShadowOrderIntent, reason: str) -> None:
    current = list(intent.reason_codes_json or [])
    if reason not in current:
        current.append(reason)
    intent.reason_codes_json = current


def _eligible_quotes(db: Session, intent: ShadowOrderIntent, *, now: datetime) -> list[LiveQuoteObservation]:
    return db.execute(select(LiveQuoteObservation).where(
        LiveQuoteObservation.code == intent.code,
        LiveQuoteObservation.captured_at > intent.decision_finalized_at,
        LiveQuoteObservation.captured_at >= intent.earliest_executable_at,
        LiveQuoteObservation.captured_at <= intent.expires_at,
        LiveQuoteObservation.captured_at <= now,
    ).order_by(LiveQuoteObservation.captured_at.asc(), LiveQuoteObservation.id.asc())).scalars().all()


def process_pending_shadow_intents(
    db: Session,
    *,
    now: datetime | None = None,
    account_id: int | None = None,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """Consume only the first eligible future persisted quote for each intent."""

    moment = _utc_naive(now) or _now()
    query = select(ShadowOrderIntent).where(ShadowOrderIntent.status == "PENDING")
    if user_id is not None or portfolio_id is not None:
        query = query.join(ShadowAccount, ShadowOrderIntent.shadow_account_id == ShadowAccount.id)
        if user_id is not None:
            query = query.where(ShadowAccount.user_id == user_id)
        if portfolio_id is not None:
            query = query.where(ShadowAccount.source_portfolio_id == portfolio_id)
    if account_id is not None:
        query = query.where(ShadowOrderIntent.shadow_account_id == account_id)
    if code:
        normalized = normalize_security_code(code)
        query = query.where(ShadowOrderIntent.code == normalized)
    intents = db.execute(query.order_by(ShadowOrderIntent.created_at.asc(), ShadowOrderIntent.id.asc())).scalars().all()
    summary = {"checked": 0, "filled": 0, "partial": 0, "blocked": 0, "expired": 0, "pending": 0, "fills": []}
    for intent in intents:
        summary["checked"] += 1
        account = db.get(ShadowAccount, intent.shadow_account_id)
        if account is None or account.status != "ACTIVE":
            summary["pending"] += 1
            continue
        if intent.shadow_generation != account.shadow_generation:
            intent.status = "SUPERSEDED"
            _set_intent_reason(intent, "SHADOW_GENERATION_INACTIVE")
            summary["blocked"] += 1
            continue
        if moment > _utc_naive(intent.expires_at):
            intent.status = "EXPIRED"
            _set_intent_reason(intent, "ORDER_EXPIRED")
            summary["expired"] += 1
            continue
        quotes = _eligible_quotes(db, intent, now=moment)
        quote: LiveQuoteObservation | None = None
        blocked_reason: str | None = None
        for candidate in quotes:
            quality = str(candidate.quality_status or "").upper()
            if candidate.captured_at_precision != "EXACT":
                blocked_reason = "QUOTE_TIME_NOT_EXACT"
                continue
            if quality not in VALID_QUOTE_QUALITY:
                blocked_reason = f"QUOTE_{quality or 'INVALID'}"
                continue
            if candidate.price is None or candidate.price <= 0:
                blocked_reason = "QUOTE_PRICE_INVALID"
                continue
            if not candidate.instrument_active:
                blocked_reason = "INSTRUMENT_INACTIVE"
                continue
            if candidate.suspended:
                # A suspension is a temporary no-fill state. Keep waiting for
                # a later valid observation until the intent expires.
                blocked_reason = "SUSPENDED"
                continue
            if intent.side == "BUY" and candidate.limit_up:
                blocked_reason = "BLOCKED_BY_LIMIT_UP"
                break
            if intent.side == "SELL" and candidate.limit_down:
                blocked_reason = "BLOCKED_BY_LIMIT_DOWN"
                break
            quote = candidate
            break
        if blocked_reason and quote is None:
            _set_intent_reason(intent, blocked_reason)
            if blocked_reason.startswith("BLOCKED_BY_LIMIT"):
                intent.status = "BLOCKED"
                summary["blocked"] += 1
            else:
                summary["pending"] += 1
            continue
        if quote is None:
            _set_intent_reason(intent, "WAITING_FOR_FUTURE_QUOTE")
            summary["pending"] += 1
            continue
        state = rebuild_shadow_state(db, account, as_of=quote.captured_at)
        position = state["positions"].get(intent.code) or {
            "quantity": 0.0,
            "sellable_quantity": 0.0,
            "average_cost": 0.0,
            "security_type": intent.security_type,
            "etf_category": None,
        }
        security_type, etf_category, security = _security_for_intent(db, intent)
        quantity = _intent_target_quantity(
            intent,
            price=float(quote.price),
            equity=float(state["cash"] + sum(
                (_number(item.get("current_mark")) or 0.0) * float(item.get("quantity") or 0.0)
                for item in state["positions"].values()
            )),
            current_value=float(position.get("quantity") or 0.0) * float(quote.price),
        )
        if quantity is None or quantity <= 0:
            intent.status = "BLOCKED"
            _set_intent_reason(intent, "TARGET_SIZE_MISSING")
            summary["blocked"] += 1
            continue
        if intent.side == "BUY":
            quantity = _floor_lot(quantity, _lot_size(security, security_type))
            if quantity <= 0:
                intent.status = "BLOCKED"
                _set_intent_reason(intent, "LOT_SIZE_BLOCKED")
                summary["blocked"] += 1
                continue
        else:
            sellable = float(position.get("sellable_quantity") or 0.0)
            if sellable <= 0:
                intent.status = "BLOCKED"
                _set_intent_reason(intent, "BLOCKED_BY_SHADOW_SELLABLE_QTY")
                summary["blocked"] += 1
                continue
            if quantity > sellable:
                intent.status = "BLOCKED"
                _set_intent_reason(intent, "BLOCKED_BY_SHADOW_SELLABLE_QTY")
                summary["blocked"] += 1
                continue
        if quantity <= 0:
            intent.status = "BLOCKED"
            _set_intent_reason(intent, "QUANTITY_INVALID")
            summary["blocked"] += 1
            continue
        gross = float(quantity) * float(quote.price)
        commission, tax, total_cost, cost_configured = _cost_breakdown(intent.side, gross)
        if intent.side == "BUY":
            cash_required = gross + total_cost
            if cash_required > float(state["cash"]) + 1e-8:
                intent.status = "BLOCKED"
                _set_intent_reason(intent, "SHADOW_CASH_BLOCKED")
                summary["blocked"] += 1
                continue
            cap, cap_flags = hard_cap_for_security(security_type, etf_category)
            if cap is not None:
                market_value = 0.0
                for held_code, item in state["positions"].items():
                    if held_code == intent.code:
                        mark = float(quote.price)
                    else:
                        materialized = db.execute(select(ShadowPosition).where(
                            ShadowPosition.shadow_account_id == account.id,
                            ShadowPosition.shadow_generation == account.shadow_generation,
                            ShadowPosition.code == held_code,
                        )).scalar_one_or_none()
                        mark = _number(materialized.current_mark) if materialized else None
                        if mark is None:
                            mark, _, _ = _latest_mark(db, held_code, as_of=quote.captured_at)
                        mark = float(mark or 0.0)
                    market_value += float(item.get("quantity") or 0.0) * mark
                equity = float(state["cash"]) + market_value
                current_value = float(position.get("quantity") or 0.0) * float(quote.price)
                if equity <= 0 or (current_value + gross) / equity > cap + 1e-9:
                    intent.status = "BLOCKED"
                    _set_intent_reason(intent, "BLOCKED_BY_SHADOW_CONSTRAINT")
                    summary["blocked"] += 1
                    continue
            cash_delta = -cash_required
        else:
            cash_delta = gross - total_cost
        execution_key = f"shadow-fill:{intent.id}:quote:{quote.id}"
        existing_fill = db.execute(select(ShadowFill).where(ShadowFill.execution_key == execution_key)).scalar_one_or_none()
        if existing_fill is not None:
            intent.status = "FILLED"
            summary["pending"] += 1
            continue
        fill = ShadowFill(
            order_intent_id=intent.id,
            shadow_account_id=account.id,
            shadow_generation=account.shadow_generation,
            code=intent.code,
            side=intent.side,
            quantity=quantity,
            price=float(quote.price),
            gross_amount=gross,
            commission=commission,
            tax=tax,
            total_cost=total_cost,
            price_basis=quote.price_basis,
            quote_observation_id=quote.id,
            quote_source_ref=quote.source_ref,
            quote_captured_at=quote.captured_at,
            fill_at=quote.captured_at,
            fill_quality=quote.quality_status,
            execution_key=execution_key,
            slippage_not_modeled=True,
            execution_delay_seconds=max(0.0, (quote.captured_at - intent.decision_finalized_at).total_seconds()),
            execution_delay_price_drift=(float(quote.price) / float(intent.decision_reference_price) - 1.0) if intent.decision_reference_price and intent.decision_reference_price > 0 else None,
        )
        db.add(fill)
        db.flush()
        settlement_policy = _settlement_policy(security, security_type)
        sellable_at = (
            _next_trading_open(db, _china_date(quote.captured_at))
            if intent.side == "BUY" and settlement_policy != "T_PLUS_0"
            else quote.captured_at
        )
        _append_ledger(
            db,
            entry_key=f"shadow-ledger:{fill.id}",
            account=account,
            entry_type="BUY_FILL" if intent.side == "BUY" else "SELL_FILL",
            occurred_at=quote.captured_at,
            code=intent.code,
            quantity=quantity,
            price=float(quote.price),
            gross_amount=gross,
            commission=commission,
            tax=tax,
            cash_delta=cash_delta,
            sellable_at=sellable_at,
            decision_observation_id=intent.decision_observation_id,
            order_intent_id=intent.id,
            fill_id=fill.id,
            payload={
                "paper_only": True,
                "cost_configured": cost_configured,
                "settlement_policy": settlement_policy,
            },
        )
        intent.status = "FILLED"
        _set_intent_reason(intent, "PAPER_FILLED_FROM_FUTURE_QUOTE")
        refresh_shadow_materialized_state(db, account, as_of=quote.captured_at)
        summary["fills"].append(fill.id)
        summary["filled"] += 1
    db.flush()
    return summary


def ensure_live_outcomes(db: Session, observation: LiveDecisionObservation) -> list[LiveDecisionOutcome]:
    """Create outcome rows for all horizons; rows remain pending until facts exist."""

    rows: list[LiveDecisionOutcome] = []
    account = db.execute(select(ShadowAccount).where(
        ShadowAccount.user_id == observation.user_id,
        ShadowAccount.source_portfolio_id == observation.portfolio_id,
        ShadowAccount.status.in_(("ACTIVE", "PAUSED")),
    ).order_by(ShadowAccount.id.desc())).scalars().first()
    account_id = account.id if account else None
    generation = account.shadow_generation if account else None
    actions = [row for row in observation.selected_actions_json or [] if isinstance(row, Mapping)]
    targets: list[tuple[str, str, str, Mapping[str, Any] | None]] = [("PORTFOLIO", "PORTFOLIO", observation.final_action, None)]
    for row in actions:
        code = normalize_security_code(row.get("code"))
        if code:
            targets.append(("SECURITY", code, str(row.get("action") or observation.final_action), row))
    if observation.final_action == "NO_ACTION":
        # Keep vetoed Candidate ACTIONs as explicit opportunity-cost samples.
        for row in observation.source_lineage_json.get("candidate_vetoes", []) if isinstance(observation.source_lineage_json, Mapping) else []:
            code = normalize_security_code(row.get("code")) if isinstance(row, Mapping) else ""
            if code:
                targets.append(("CANDIDATE_VETO", code, "VETOED_ACTION", row))
    for target_type, target_key, action, raw in targets:
        for horizon in OUTCOME_HORIZONS:
            existing = db.execute(select(LiveDecisionOutcome).where(
                LiveDecisionOutcome.decision_observation_id == observation.id,
                LiveDecisionOutcome.target_type == target_type,
                LiveDecisionOutcome.target_key == target_key,
                LiveDecisionOutcome.horizon_trading_days == horizon,
                LiveDecisionOutcome.calculation_version == OUTCOME_VERSION,
            )).scalar_one_or_none()
            if existing is not None:
                rows.append(existing)
                continue
            reference_price = _positive(raw.get("reference_price")) if isinstance(raw, Mapping) else None
            reference_basis = str(raw.get("reference_price_basis") or "UNKNOWN") if isinstance(raw, Mapping) else None
            target_dates = _trading_days_after(db, observation.trade_date, horizon)
            target_date = target_dates[-1] if len(target_dates) >= horizon else None
            row = LiveDecisionOutcome(
                decision_observation_id=observation.id,
                shadow_account_id=account_id,
                shadow_generation=generation,
                target_type=target_type,
                target_key=target_key,
                recommended_action=action,
                horizon_trading_days=horizon,
                reference_trade_date=observation.trade_date,
                reference_at=observation.decision_finalized_at,
                reference_price=reference_price,
                reference_price_basis=reference_basis,
                target_trade_date=target_date,
                target_price=None,
                status="PENDING",
                quality_status="PENDING",
                live_evidence_eligibility="INSUFFICIENT_SAMPLE",
                next_due_date=target_date,
                source_refs_json={"observation_id": observation.id, "benchmark": "ALL_A_MEDIAN"},
                calculation_version=OUTCOME_VERSION,
            )
            db.add(row)
            db.flush()
            rows.append(row)
    return rows


def _bar(db: Session, code: str, day: date, *, as_of: datetime | None = None) -> DailyBarCache | None:
    query = select(DailyBarCache).where(
        DailyBarCache.market == "CN",
        DailyBarCache.code == code,
        DailyBarCache.trade_date == day,
        DailyBarCache.quality_status.in_(tuple(VALID_QUOTE_QUALITY)),
    )
    if as_of is not None:
        cutoff = _utc_naive(as_of) or _now()
        query = query.where(
            DailyBarCache.available_at.is_not(None),
            DailyBarCache.available_at <= cutoff,
        )
    # QFQ is the production daily-bar basis.  If only another basis exists,
    # the caller can explicitly reject the mix instead of silently combining it.
    return db.execute(query.order_by(
        (DailyBarCache.adjustment == "QFQ").desc(),
        DailyBarCache.id.desc(),
    ).limit(1)).scalar_one_or_none()


def _basis_compatible(reference_basis: str | None, target_basis: str | None) -> bool:
    reference = str(reference_basis or "").upper().replace("-", "_")
    target = str(target_basis or "").upper().replace("-", "_")
    aliases = {
        "RAW": "RAW_QUOTE",
        "RAWQUOTE": "RAW_QUOTE",
        "UNADJUSTED": "RAW_QUOTE",
        "QFQ": "QFQ",
        "ADJUSTED": "QFQ",
    }
    reference = aliases.get(reference, reference)
    target = aliases.get(target, target)
    return bool(reference and target and reference not in {"UNKNOWN", "NONE"} and target not in {"UNKNOWN", "NONE"} and reference == target)


def _benchmark_return(
    db: Session,
    start: date,
    end: date,
    *,
    as_of: datetime | None = None,
) -> float | None:
    filters = [
        AllAMedianIndexDaily.market == "CN",
        AllAMedianIndexDaily.quality_status.in_(tuple(VALID_QUOTE_QUALITY)),
    ]
    if as_of is not None:
        cutoff = _utc_naive(as_of) or _now()
        filters.extend([
            AllAMedianIndexDaily.available_at.is_not(None),
            AllAMedianIndexDaily.available_at <= cutoff,
        ])
    first = db.execute(select(AllAMedianIndexDaily).where(
        *filters,
        AllAMedianIndexDaily.trade_date <= start,
    ).order_by(AllAMedianIndexDaily.trade_date.desc(), AllAMedianIndexDaily.id.desc()).limit(1)).scalar_one_or_none()
    last = db.execute(select(AllAMedianIndexDaily).where(
        *filters,
        AllAMedianIndexDaily.trade_date <= end,
    ).order_by(AllAMedianIndexDaily.trade_date.desc(), AllAMedianIndexDaily.id.desc()).limit(1)).scalar_one_or_none()
    if first is None or last is None or not first.index_value or not last.index_value:
        return None
    return float(last.index_value) / float(first.index_value) - 1.0


def _security_forward_metrics(
    db: Session,
    code: str,
    start: date,
    end: date,
    reference_price: float,
    reference_basis: str | None = None,
) -> tuple[float | None, float | None, float | None, float | None]:
    target = _bar(db, code, end)
    if target is None or target.close is None or reference_price <= 0:
        return None, None, None, None
    if not _basis_compatible(reference_basis, target.adjustment):
        return None, None, None, None
    bars = db.execute(select(DailyBarCache).where(
        DailyBarCache.market == "CN",
        DailyBarCache.code == code,
        DailyBarCache.trade_date > start,
        DailyBarCache.trade_date <= end,
        DailyBarCache.close.is_not(None),
        DailyBarCache.adjustment == target.adjustment,
        DailyBarCache.quality_status.in_(tuple(VALID_QUOTE_QUALITY)),
    ).order_by(DailyBarCache.trade_date.asc(), DailyBarCache.id.asc())).scalars().all()
    closes = [float(item.close) for item in bars if item.close is not None]
    if not closes:
        closes = [float(target.close)]
    forward = float(target.close) / reference_price - 1.0
    return forward, max(closes) / reference_price - 1.0, min(closes) / reference_price - 1.0, float(target.close)


def _shadow_execution_evidence(
    db: Session,
    outcome: LiveDecisionOutcome,
    *,
    now: datetime,
) -> tuple[bool, bool, ShadowFill | None, dict[str, Any]]:
    """Derive execution eligibility from the isolated intent/fill facts."""

    if outcome.target_type not in {"PORTFOLIO", "SECURITY"}:
        return False, False, None, {"status": "NON_EXECUTABLE_TARGET"}
    if outcome.shadow_account_id is None or outcome.shadow_generation is None:
        return False, False, None, {"status": "SHADOW_ACCOUNT_DATA_GAP"}
    query = select(ShadowOrderIntent).where(
        ShadowOrderIntent.shadow_account_id == outcome.shadow_account_id,
        ShadowOrderIntent.shadow_generation == outcome.shadow_generation,
        ShadowOrderIntent.decision_observation_id == outcome.decision_observation_id,
    )
    if outcome.target_type == "SECURITY":
        query = query.where(ShadowOrderIntent.code == outcome.target_key)
    intents = db.execute(query.order_by(ShadowOrderIntent.id.asc())).scalars().all()
    if not intents:
        status = (
            CONDITIONAL_ACTION_EXECUTION_UNSUPPORTED
            if outcome.recommended_action == "conditional_add"
            else "NO_SHADOW_INTENT"
        )
        return False, False, None, {"status": status, "intent_ids": [], "fill_ids": []}

    intent_by_id = {intent.id: intent for intent in intents}
    fills = db.execute(select(ShadowFill).where(
        ShadowFill.order_intent_id.in_(tuple(intent_by_id)),
        ShadowFill.shadow_account_id == outcome.shadow_account_id,
        ShadowFill.shadow_generation == outcome.shadow_generation,
    ).order_by(ShadowFill.fill_at.asc(), ShadowFill.id.asc())).scalars().all()
    fill = next(
        (item for item in fills if intent_by_id.get(item.order_intent_id, None) is not None
         and intent_by_id[item.order_intent_id].status == "FILLED"),
        None,
    )
    if fill is not None:
        return True, True, fill, {
            "status": "FILLED",
            "intent_ids": list(intent_by_id),
            "fill_ids": [item.id for item in fills],
        }

    future_quote_count = sum(
        len(_eligible_quotes(db, intent, now=now))
        for intent in intents
        if intent.status == "PENDING"
    )
    statuses = {str(intent.status or "").upper() for intent in intents}
    if "PENDING" in statuses:
        status = "FUTURE_QUOTE_AVAILABLE_UNFILLED" if future_quote_count else "WAITING_FOR_FUTURE_QUOTE"
    elif "BLOCKED" in statuses:
        status = "BLOCKED"
    elif "EXPIRED" in statuses:
        status = "EXPIRED"
    elif "SUPERSEDED" in statuses:
        status = "SUPERSEDED"
    else:
        status = "NO_SHADOW_FILL"
    return False, False, None, {
        "status": status,
        "intent_ids": list(intent_by_id),
        "intent_statuses": sorted(statuses),
        "future_quote_count": future_quote_count,
        "fill_ids": [item.id for item in fills],
    }


def _shadow_daily_snapshot_for_generation(
    db: Session,
    *,
    account_id: int,
    generation: int,
    trade_date: date,
) -> ShadowDailySnapshot | None:
    return db.execute(select(ShadowDailySnapshot).where(
        ShadowDailySnapshot.shadow_account_id == account_id,
        ShadowDailySnapshot.shadow_generation == generation,
        ShadowDailySnapshot.trade_date == trade_date,
    )).scalar_one_or_none()


def _shadow_equity_at(
    db: Session,
    account: ShadowAccount,
    *,
    generation: int,
    as_of: datetime,
) -> tuple[float | None, dict[str, Any]]:
    """Mark one generation at a reference time without using future facts."""

    cutoff = _utc_naive(as_of) or _now()
    state = rebuild_shadow_state(db, account, generation=generation, as_of=cutoff)
    market_value = 0.0
    refs: dict[str, Any] = {}
    missing_codes: list[str] = []
    for code, item in state["positions"].items():
        price, basis, quote_id = _latest_mark(db, code, as_of=cutoff)
        if price is not None:
            refs[code] = {"quote_observation_id": quote_id, "basis": basis}
        else:
            bar = _bar(db, code, _china_date(cutoff), as_of=cutoff)
            price = _positive(bar.close) if bar else None
            basis = str(bar.adjustment or "QFQ") if bar and price is not None else None
            refs[code] = {"daily_bar_id": bar.id if bar else None, "basis": basis}
        if price is None:
            missing_codes.append(code)
            continue
        market_value += float(item["quantity"]) * float(price)
    if missing_codes:
        return None, {
            "status": SHADOW_MARK_DATA_GAP,
            "missing_codes": missing_codes,
            "marks": refs,
        }
    return float(state["cash"]) + market_value, {
        "status": "VALID",
        "marks": refs,
    }


def evaluate_live_outcomes(
    db: Session,
    *,
    as_of: datetime | None = None,
    observation_id: int | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    moment = _utc_naive(as_of) or _now()
    query = select(LiveDecisionOutcome).where(LiveDecisionOutcome.status == "PENDING")
    if observation_id is not None:
        query = query.where(LiveDecisionOutcome.decision_observation_id == observation_id)
    else:
        query = query.where((LiveDecisionOutcome.next_due_date.is_(None)) | (LiveDecisionOutcome.next_due_date <= _china_date(moment)))
    rows = db.execute(query.order_by(LiveDecisionOutcome.id.asc()).limit(max(1, min(limit, 5000)))).scalars().all()
    completed = 0
    pending = 0
    for outcome in rows:
        observation = db.get(LiveDecisionObservation, outcome.decision_observation_id)
        if observation is None:
            pending += 1
            continue
        if outcome.target_trade_date is None:
            target_dates = _trading_days_after(db, observation.trade_date, outcome.horizon_trading_days)
            if len(target_dates) >= outcome.horizon_trading_days:
                outcome.target_trade_date = target_dates[-1]
                outcome.next_due_date = outcome.target_trade_date
        if outcome.target_trade_date is None or outcome.target_trade_date > _china_date(moment):
            pending += 1
            continue
        benchmark = _benchmark_return(
            db,
            observation.trade_date,
            outcome.target_trade_date,
            as_of=moment,
        )
        forward = mfe = mae = target_price = None
        portfolio_evidence: dict[str, Any] | None = None
        if outcome.target_type == "PORTFOLIO":
            account = db.get(ShadowAccount, outcome.shadow_account_id) if outcome.shadow_account_id is not None else None
            if account is not None and outcome.shadow_generation is not None:
                reference_equity, reference_evidence = _shadow_equity_at(
                    db,
                    account,
                    generation=outcome.shadow_generation,
                    as_of=observation.decision_finalized_at,
                )
                target_snapshot = _shadow_daily_snapshot_for_generation(
                    db,
                    account_id=account.id,
                    generation=outcome.shadow_generation,
                    trade_date=outcome.target_trade_date,
                )
                target_equity = _number(target_snapshot.total_equity) if target_snapshot is not None else None
                portfolio_evidence = {
                    "account_id": account.id,
                    "shadow_generation": outcome.shadow_generation,
                    "reference_equity": reference_equity,
                    "reference": reference_evidence,
                    "target_snapshot_id": target_snapshot.id if target_snapshot is not None else None,
                    "target_equity": target_equity,
                }
                if reference_equity is not None and reference_equity > 0 and target_equity is not None:
                    forward = target_equity / reference_equity - 1.0
        else:
            reference = outcome.reference_price
            if reference is not None:
                forward, mfe, mae, target_price = _security_forward_metrics(
                    db,
                    outcome.target_key,
                    observation.trade_date,
                    outcome.target_trade_date,
                    float(reference),
                    outcome.reference_price_basis,
                )
        execution_eligible, shadow_filled, fill, execution_evidence = _shadow_execution_evidence(
            db,
            outcome,
            now=moment,
        )
        outcome.execution_eligible = execution_eligible
        outcome.shadow_filled = shadow_filled
        outcome.fill_delay_seconds = fill.execution_delay_seconds if fill else None
        outcome.fill_drift = fill.execution_delay_price_drift if fill else None
        source_refs = dict(outcome.source_refs_json or {})
        source_refs["execution"] = execution_evidence
        if portfolio_evidence is not None:
            source_refs["portfolio_equity"] = portfolio_evidence
        outcome.source_refs_json = source_refs
        if outcome.target_type == "PORTFOLIO":
            incomplete = forward is None or benchmark is None
        else:
            incomplete = forward is None
        if incomplete:
            outcome.status = "PENDING"
            outcome.quality_status = "DATA_GAP"
            outcome.next_due_date = outcome.target_trade_date
            pending += 1
            continue
        outcome.target_price = target_price
        outcome.forward_return = forward
        outcome.benchmark_return = benchmark
        outcome.excess_return = forward - benchmark if forward is not None and benchmark is not None else None
        outcome.mfe = mfe
        outcome.mae = mae
        outcome.direction = "UP" if forward > 0 else "DOWN" if forward < 0 else "FLAT"
        outcome.drawdown = mae
        if observation.final_action == "NO_ACTION":
            outcome.drawdown_avoided = max(0.0, -benchmark) if benchmark is not None else None
            outcome.risk_off_correct = bool(
                str(observation.market_regime or "").upper() in {"RISK_OFF", "DEFENSIVE", "BEAR"}
                and (benchmark is not None and benchmark < 0)
            )
        if outcome.target_type == "CANDIDATE_VETO":
            outcome.candidate_opportunity_cost = forward
        outcome.status = "COMPLETED"
        outcome.quality_status = "VALID" if target_price is not None or benchmark is not None else "DEGRADED"
        outcome.computed_at = moment
        outcome.next_due_date = None
        completed += 1
    db.flush()
    return {"checked": len(rows), "completed": completed, "pending": pending}


def create_shadow_daily_snapshot(
    db: Session,
    account: ShadowAccount,
    *,
    trade_date: date,
    as_of: datetime | None = None,
) -> ShadowDailySnapshot:
    """Materialize one close mark; it never changes production holdings."""

    close_at = _utc_naive(as_of) or datetime.combine(trade_date, time(7, 5))
    state = rebuild_shadow_state(db, account, as_of=close_at)
    market_value = 0.0
    refs: dict[str, Any] = {}
    bases: set[str] = set()
    missing_codes: list[str] = []
    for code, item in state["positions"].items():
        bar = _bar(db, code, trade_date, as_of=close_at)
        price = _positive(bar.close) if bar else None
        basis = str(bar.adjustment or "QFQ") if bar else None
        if price is None:
            price, basis, quote_id = _latest_mark(db, code, as_of=close_at)
            refs[code] = {"quote_observation_id": quote_id, "basis": basis}
        else:
            refs[code] = {"daily_bar_id": bar.id if bar else None, "basis": basis}
        if price is None:
            missing_codes.append(code)
            continue
        market_value += float(item["quantity"]) * price
        bases.add(str(basis or "UNKNOWN"))
    if missing_codes:
        raise RuntimeError(f"{SHADOW_MARK_DATA_GAP}:{','.join(sorted(missing_codes))}")
    equity = float(state["cash"]) + market_value
    previous = db.execute(select(ShadowDailySnapshot).where(
        ShadowDailySnapshot.shadow_account_id == account.id,
        ShadowDailySnapshot.shadow_generation == account.shadow_generation,
        ShadowDailySnapshot.trade_date < trade_date,
    ).order_by(ShadowDailySnapshot.trade_date.desc(), ShadowDailySnapshot.id.desc()).limit(1)).scalar_one_or_none()
    generation_config = (account.config_json or {}).get("generations") if isinstance(account.config_json, Mapping) else {}
    generation_config = generation_config.get(str(account.shadow_generation), {}) if isinstance(generation_config, Mapping) else {}
    starting_equity = (
        _number(generation_config.get("starting_equity"))
        or _number((account.config_json or {}).get("starting_equity"))
        or account.starting_cash
        or 0.0
    )
    peak = max([float(row.total_equity) for row in db.execute(select(ShadowDailySnapshot).where(
        ShadowDailySnapshot.shadow_account_id == account.id,
        ShadowDailySnapshot.shadow_generation == account.shadow_generation,
        ShadowDailySnapshot.trade_date <= trade_date,
    )).scalars().all()] + [equity])
    benchmark = (
        _benchmark_return(
            db,
            previous.trade_date if previous else trade_date,
            trade_date,
            as_of=close_at,
        )
        if previous
        else None
    )
    turnover = sum(
        float(fill.gross_amount or 0.0)
        for fill in db.execute(select(ShadowFill).where(
            ShadowFill.shadow_account_id == account.id,
            ShadowFill.shadow_generation == account.shadow_generation,
            ShadowFill.fill_at >= datetime.combine(trade_date, time(0, 0)),
            ShadowFill.fill_at < datetime.combine(trade_date + timedelta(days=1), time(0, 0)),
        )).scalars().all()
    )
    observation_query = select(LiveDecisionObservation).where(
        LiveDecisionObservation.portfolio_id == account.source_portfolio_id,
        LiveDecisionObservation.trade_date == trade_date,
    )
    observations = db.execute(observation_query).scalars().all()
    snapshot = db.execute(select(ShadowDailySnapshot).where(
        ShadowDailySnapshot.shadow_account_id == account.id,
        ShadowDailySnapshot.shadow_generation == account.shadow_generation,
        ShadowDailySnapshot.trade_date == trade_date,
    )).scalar_one_or_none()
    if snapshot is None:
        snapshot = ShadowDailySnapshot(
            shadow_account_id=account.id,
            shadow_generation=account.shadow_generation,
            trade_date=trade_date,
        )
        db.add(snapshot)
    snapshot.cash = float(state["cash"])
    snapshot.market_value = market_value
    snapshot.total_equity = equity
    snapshot.daily_return = equity / previous.total_equity - 1.0 if previous and previous.total_equity else None
    snapshot.cumulative_return = equity / starting_equity - 1.0 if starting_equity > 0 else None
    snapshot.drawdown = equity / peak - 1.0 if peak > 0 else None
    snapshot.turnover = turnover
    snapshot.position_count = len(state["positions"])
    snapshot.action_count = sum(1 for item in observations if item.final_action == "ACTION")
    snapshot.no_action_count = sum(1 for item in observations if item.final_action == "NO_ACTION")
    snapshot.benchmark_return = benchmark
    snapshot.excess_return = snapshot.daily_return - benchmark if snapshot.daily_return is not None and benchmark is not None else None
    snapshot.market_regime = next((item.market_regime for item in reversed(observations) if item.market_regime), None)
    snapshot.price_basis = next(iter(bases), None) if len(bases) == 1 else "MIXED" if bases else None
    snapshot.price_basis_compatible = len(bases) <= 1
    snapshot.source_refs_json = refs
    db.flush()
    return snapshot


def match_actual_trade_alignment(db: Session, observation: LiveDecisionObservation) -> list[DecisionActualAlignment]:
    """Match only explicit confirmed TradeLedger facts; never infer from holdings diffs."""

    if observation.final_action != "ACTION":
        return []
    end = _next_trading_close(db, observation.trade_date)
    rows: list[DecisionActualAlignment] = []
    for action in observation.selected_actions_json or []:
        if not isinstance(action, Mapping):
            continue
        code = normalize_security_code(action.get("code"))
        side = str(action.get("side") or "").upper()
        if not code or side not in {"BUY", "SELL"}:
            continue
        existing = db.execute(select(DecisionActualAlignment).where(
            DecisionActualAlignment.decision_observation_id == observation.id,
            DecisionActualAlignment.code == code,
            DecisionActualAlignment.side == side,
        )).scalar_one_or_none()
        if existing is not None:
            rows.append(existing)
            continue
        trades = db.execute(select(TradeLedgerEntry).where(
            TradeLedgerEntry.user_id == observation.user_id,
            TradeLedgerEntry.portfolio_id == observation.portfolio_id,
            TradeLedgerEntry.status == "CONFIRMED",
            TradeLedgerEntry.security_code == code,
            TradeLedgerEntry.side == side,
            TradeLedgerEntry.executed_at >= observation.decision_finalized_at,
            TradeLedgerEntry.executed_at <= end,
        ).order_by(TradeLedgerEntry.executed_at.asc(), TradeLedgerEntry.id.asc())).scalars().all()
        trade = trades[0] if trades else None
        requested = _positive(action.get("target_qty"))
        row = DecisionActualAlignment(
            decision_observation_id=observation.id,
            user_id=observation.user_id,
            portfolio_id=observation.portfolio_id,
            code=code,
            side=side,
            status="ALIGNED" if trade else "NO_MATCH",
            actual_trade_ledger_id=trade.id if trade else None,
            window_start=observation.decision_finalized_at,
            window_end=end,
            matched_at=trade.executed_at if trade else None,
            time_delta_seconds=(trade.executed_at - observation.decision_finalized_at).total_seconds() if trade else None,
            quantity_ratio=(float(trade.quantity) / requested if trade and requested and trade.quantity is not None else None),
            source_refs_json={"trade_ledger_id": trade.id if trade else None, "matching": "same_code_side_next_trading_day"},
        )
        db.add(row)
        db.flush()
        rows.append(row)
    return rows


def shadow_account_performance(db: Session, account: ShadowAccount, *, generation: int | None = None) -> dict[str, Any]:
    generation = generation or account.shadow_generation
    state = rebuild_shadow_state(db, account, generation=generation)
    snapshots = db.execute(select(ShadowDailySnapshot).where(
        ShadowDailySnapshot.shadow_account_id == account.id,
        ShadowDailySnapshot.shadow_generation == generation,
    ).order_by(ShadowDailySnapshot.trade_date.asc(), ShadowDailySnapshot.id.asc())).scalars().all()
    fills = db.execute(select(ShadowFill).where(
        ShadowFill.shadow_account_id == account.id,
        ShadowFill.shadow_generation == generation,
    )).scalars().all()
    decision_ids = set(db.scalars(select(ShadowOrderIntent.decision_observation_id).where(
        ShadowOrderIntent.shadow_account_id == account.id,
        ShadowOrderIntent.shadow_generation == generation,
    )).all())
    decision_ids.update(db.scalars(select(LiveDecisionOutcome.decision_observation_id).where(
        LiveDecisionOutcome.shadow_account_id == account.id,
        LiveDecisionOutcome.shadow_generation == generation,
    )).all())
    decisions = db.execute(select(LiveDecisionObservation).where(
        LiveDecisionObservation.id.in_(decision_ids) if decision_ids else LiveDecisionObservation.id == -1,
    ).order_by(LiveDecisionObservation.decision_finalized_at.asc(), LiveDecisionObservation.id.asc())).scalars().all()
    last = snapshots[-1] if snapshots else None
    cumulative = last.cumulative_return if last else None
    benchmark = None
    performance_quality = "PENDING"
    if snapshots:
        benchmark_returns = [item.benchmark_return for item in snapshots]
        # The first snapshot is the generation baseline and has no prior day;
        # every later missing benchmark is a data gap, never a zero return.
        if benchmark_returns and benchmark_returns[0] is None:
            benchmark_returns = benchmark_returns[1:]
        if benchmark_returns and all(value is not None for value in benchmark_returns):
            benchmark = 1.0
            for value in benchmark_returns:
                benchmark *= 1.0 + float(value)
            benchmark -= 1.0
            performance_quality = "VALID"
        elif benchmark_returns:
            performance_quality = "DATA_GAP"
    return {
        "account_id": account.id,
        "shadow_generation": generation,
        "status": account.status,
        "paper_only": True,
        "execution_contract_version": account.execution_contract_version,
        "current_cash": state["cash"],
        "reserved_cash": 0.0,
        "current_equity": last.total_equity if last else None,
        "cumulative_return": cumulative,
        "benchmark_return": benchmark,
        "excess_return": cumulative - benchmark if cumulative is not None and benchmark is not None else None,
        "performance_quality": performance_quality,
        "max_drawdown": min((float(item.drawdown) for item in snapshots if item.drawdown is not None), default=None),
        "turnover": sum(float(item.turnover or 0.0) for item in snapshots),
        "transaction_cost": sum(float(item.total_cost or 0.0) for item in fills),
        "position_count": len(state["positions"]),
        "action_count": sum(1 for item in decisions if item.final_action == "ACTION"),
        "no_action_count": sum(1 for item in decisions if item.final_action == "NO_ACTION"),
        "decision_count": len(decisions),
        "fill_count": len(fills),
        "sample_days": len({item.trade_date for item in snapshots}),
        "snapshots": snapshots,
    }


def validation_summary(db: Session, *, user_id: int, portfolio_id: int | None = None) -> dict[str, Any]:
    query = select(LiveDecisionObservation).where(LiveDecisionObservation.user_id == user_id)
    if portfolio_id is not None:
        query = query.where(LiveDecisionObservation.portfolio_id == portfolio_id)
    observations = db.execute(query.order_by(LiveDecisionObservation.decision_finalized_at.asc(), LiveDecisionObservation.id.asc())).scalars().all()
    cohorts: dict[str, dict[str, Any]] = {}
    for observation in observations:
        generation = db.execute(select(ShadowOrderIntent.shadow_generation).where(
            ShadowOrderIntent.decision_observation_id == observation.id,
        ).order_by(ShadowOrderIntent.id.desc()).limit(1)).scalar_one_or_none()
        if generation is None:
            generation = db.execute(select(LiveDecisionOutcome.shadow_generation).where(
                LiveDecisionOutcome.decision_observation_id == observation.id,
                LiveDecisionOutcome.shadow_generation.is_not(None),
            ).order_by(LiveDecisionOutcome.id.desc()).limit(1)).scalar_one_or_none()
        key = "|".join([
            str(observation.parameter_set_hash or "UNKNOWN"),
            str(observation.decision_contract_version or CONTRACT_VERSION),
            str(observation.runtime_contract_version or CONTRACT_VERSION),
            str(generation or "UNASSIGNED"),
        ])
        item = cohorts.setdefault(key, {
            "cohort": {
                "parameter_set_hash": observation.parameter_set_hash,
                "decision_contract_version": observation.decision_contract_version,
                "runtime_contract_version": observation.runtime_contract_version,
                "shadow_generation": generation,
            },
            "decision_count": 0,
            "action_count": 0,
            "no_action_count": 0,
            "blocked_count": 0,
            "sample_days": set(),
            "outcome_buckets": {},
        })
        item["decision_count"] += 1
        item["action_count"] += observation.final_action == "ACTION"
        item["no_action_count"] += observation.final_action == "NO_ACTION"
        item["blocked_count"] += observation.final_action == "DECISION_BLOCKED"
        item["sample_days"].add(observation.trade_date.isoformat())
        for outcome in db.execute(select(LiveDecisionOutcome).where(LiveDecisionOutcome.decision_observation_id == observation.id, LiveDecisionOutcome.status == "COMPLETED")).scalars().all():
            bucket_key = (outcome.target_type, outcome.target_key, outcome.horizon_trading_days)
            bucket = item["outcome_buckets"].setdefault(bucket_key, {
                "target_type": outcome.target_type,
                "target_key": outcome.target_key,
                "horizon_trading_days": outcome.horizon_trading_days,
                "completed_outcome_count": 0,
                "excess_returns": [],
            })
            bucket["completed_outcome_count"] += 1
            if outcome.excess_return is not None:
                bucket["excess_returns"].append(float(outcome.excess_return))
    result: list[dict[str, Any]] = []
    for item in cohorts.values():
        sample_days = len(item.pop("sample_days"))
        buckets = item.pop("outcome_buckets")
        item["sample_days"] = sample_days
        item["outcomes_by_target_horizon"] = [
            {
                "target_type": bucket["target_type"],
                "target_key": bucket["target_key"],
                "horizon_trading_days": bucket["horizon_trading_days"],
                "completed_outcome_count": bucket["completed_outcome_count"],
                "mean_excess_return": (
                    sum(bucket["excess_returns"]) / len(bucket["excess_returns"])
                    if bucket["excess_returns"] else None
                ),
            }
            for bucket in sorted(
                buckets.values(),
                key=lambda value: (
                    str(value["target_type"]),
                    str(value["target_key"]),
                    int(value["horizon_trading_days"]),
                ),
            )
        ]
        item["completed_outcome_count"] = sum(
            bucket["completed_outcome_count"] for bucket in buckets.values()
        )
        item["evidence_status"] = "INSUFFICIENT_LIVE_EVIDENCE" if sample_days < 20 or item["action_count"] < 30 else "OBSERVE"
        result.append(item)
    return {
        "user_id": user_id,
        "portfolio_id": portfolio_id,
        "live_sample_days": len({item.trade_date for item in observations}),
        "decision_count": len(observations),
        "cohorts": result,
        "historical_backtest_included": False,
        "limitations": [
            "Paper fill uses only persisted future quotes.",
            "Slippage and order-book impact are not modeled.",
            "Live evidence is independent from Historical Research/Backtest.",
            "Outcome means are separated by target and trading horizon.",
        ],
    }


def maintain_shadow(
    db: Session,
    *,
    as_of: datetime | None = None,
    trade_date: date | None = None,
) -> dict[str, Any]:
    """Single-scheduler maintenance hook for fills, outcomes and close marks."""

    moment = _utc_naive(as_of) or _now()
    fills = process_pending_shadow_intents(db, now=moment)
    outcomes = evaluate_live_outcomes(db, as_of=moment)
    day = trade_date or _china_date(moment)
    snapshots: list[int] = []
    snapshot_errors: list[dict[str, Any]] = []
    local_time = moment.replace(tzinfo=UTC).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai")).time()
    if trade_date is not None or local_time >= time(15, 30):
        for account in db.execute(select(ShadowAccount).where(ShadowAccount.status.in_(("ACTIVE", "PAUSED")))).scalars().all():
            try:
                snapshots.append(create_shadow_daily_snapshot(db, account, trade_date=day, as_of=moment).id)
            except Exception as exc:
                snapshot_errors.append({"account_id": account.id, "reason": str(exc)[:300]})
                logger.exception("Shadow daily snapshot failed account=%s", account.id)
    db.flush()
    return {
        "fills": fills,
        "outcomes": outcomes,
        "snapshots": snapshots,
        "degraded": bool(snapshot_errors),
        "snapshot_errors": snapshot_errors,
    }


__all__ = [
    "ACTIONABLE_ACTIONS",
    "CONDITIONAL_ACTION_EXECUTION_UNSUPPORTED",
    "OUTCOME_HORIZONS",
    "SHADOW_MARK_DATA_GAP",
    "SHADOW_EXECUTION_VERSION",
    "canonical_json",
    "capture_live_decision_observation",
    "capture_production_decision",
    "create_shadow_account",
    "create_shadow_daily_snapshot",
    "ensure_live_outcomes",
    "ensure_shadow_order_intents",
    "evaluate_live_outcomes",
    "maintain_shadow",
    "match_actual_trade_alignment",
    "pause_shadow_account",
    "persist_live_quote_observation",
    "process_pending_shadow_intents",
    "rebase_shadow_account",
    "rebuild_shadow_state",
    "refresh_shadow_materialized_state",
    "resume_shadow_account",
    "sha256_json",
    "shadow_account_performance",
    "validation_summary",
]
