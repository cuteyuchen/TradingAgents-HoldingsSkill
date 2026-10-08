"""Daily advice and remaining-plan projections over TriggerPlan and the real ledger.

Plans never own fills or cash. A fill link points at a confirmed ledger fact;
revisions and voids therefore affect the projection without copying trade facts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, time
import hashlib
import json
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..analysis_workflow.constants import RunStatus
from ..clock import utc_now
from ..config import settings
from ..decision_contract import holding_decision_status, normalize_action_sizing
from ..governance.service import resolve_production_parameters
from ..market.codes import normalize_security_code
from ..market.models import _coerce_datetime
from ..market_models import SecurityMaster
from ..portfolio.account import build_account_state
from ..portfolio.constraints import build_portfolio_constraints
from ..portfolio.decision_gate import apply_portfolio_decision_gate
from ..portfolio.risk import latest_confirmed_snapshot
from ..portfolio.service import portfolio_context_for_analysis
from ..portfolio_models import TradeLedgerEntry
from ..trigger_models import TriggerPlan
from ..v2_models import AnalysisJob, AnalysisRun
from .engine import compare_values

VERSION = "daily-action-plan-v1"
SOURCE = "DAILY_ANALYSIS"
STATES = ("ACTION", "NO_ACTION", "WAITING", "DATA_GAP", "INCOMPLETE", "EXPIRED")
_BUY = {"add", "buy", "new_position", "conditional_add", "conditional_buy"}
_SELL = {"sell", "reduce"}
_CONDITIONS = {
    "price_below": ("price", "LT"), "price_above": ("price", "GT"),
    "pct_change_below": ("pct_change", "LT"), "pct_change_above": ("pct_change", "GT"),
}


def _moment(value: datetime | None = None) -> datetime:
    value = value or utc_now()
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _payload(run: Any) -> tuple[dict, dict]:
    payload = getattr(run, "structured_result_json", None) or {}
    payload = payload if isinstance(payload, dict) else {}
    result = payload.get("result", payload)
    return payload, result if isinstance(result, dict) else {}


def evidence_version(run: Any) -> str:
    payload, result = _payload(run)
    explicit = result.get("evidence_version") or payload.get("evidence_version") or (payload.get("market_snapshot") or {}).get("evidence_version")
    if explicit:
        return str(explicit)
    evidence = {key: payload.get(key) for key in ("market_snapshot", "evidence", "evidence_catalog")}
    evidence["result_evidence"] = result.get("evidence_catalog")
    return "evidence:" + hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _research_expired(payload: dict, code: str, moment: datetime) -> bool:
    pack = (payload.get("market_snapshot") or {}).get("unified_evidence") or {}
    for record in (pack.get("records") or {}).values():
        if record.get("kind") == "quote" or record.get("status") not in {"available", "degraded"}:
            continue
        record_code = normalize_security_code(record.get("code"))
        if record_code and record_code != code:
            continue
        deadline = _coerce_datetime(record.get("valid_until"))
        if deadline is not None and _moment(deadline) <= moment:
            return True
    return False


def _condition(row: dict) -> dict | None:
    raw = row.get("trigger_plan") or row.get("trigger")
    if not isinstance(raw, dict):
        raw = row if row.get("condition") else {}
    key = str(raw.get("condition") or "").lower()
    metric, operator = _CONDITIONS.get(key, (raw.get("metric"), str(raw.get("operator") or "").upper()))
    threshold = _number(raw.get("threshold", raw.get("trigger_price")))
    if metric in {"price", "pct_change"} and operator in {"GT", "GTE", "LT", "LTE", "CROSS_ABOVE", "CROSS_BELOW"} and threshold is not None:
        return {"metric": metric, "operator": operator, "threshold": threshold}
    return None


def latest_plan_run(db: Session, *, user_id: int, portfolio_id: int) -> AnalysisRun | None:
    return db.query(AnalysisRun).join(AnalysisJob).filter(
        AnalysisRun.user_id == user_id, AnalysisJob.portfolio_id == portfolio_id,
        AnalysisRun.status.in_(RunStatus.REPORTABLE),
    ).order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc()).first()


def refresh_daily_action_plans(db: Session, run: Any, *, now: datetime | None = None) -> list[TriggerPlan]:
    """Idempotently materialize advice; a repeated refresh never rearms old plans."""
    portfolio_id = getattr(getattr(run, "job", None), "portfolio_id", None)
    if portfolio_id is None or getattr(run, "status", None) not in RunStatus.REPORTABLE:
        return []
    existing = db.query(TriggerPlan).filter(
        TriggerPlan.portfolio_id == portfolio_id, TriggerPlan.source_type == SOURCE,
        TriggerPlan.source_id == str(run.id),
    ).order_by(TriggerPlan.id).all()
    if existing:
        return existing
    moment = _moment(now)
    created = _moment(getattr(run, "completed_at", None) or getattr(run, "created_at", None) or moment)
    payload, result = _payload(run)
    snapshot = latest_confirmed_snapshot(db, portfolio_id=portfolio_id, as_of=created)
    account = build_account_state(db, portfolio_id=portfolio_id, snapshot=snapshot, as_of=created) if snapshot else {}
    source_account = result.get("account_version") or payload.get("account_version")
    day_end = datetime.combine(created.astimezone(ZoneInfo("Asia/Shanghai")).date(), time.max, ZoneInfo("Asia/Shanghai")).astimezone(UTC)
    expiry = _coerce_datetime(result.get("expires_at") or result.get("valid_until")) or day_end
    expiry = min(_moment(expiry), day_end)
    holdings = result.get("holdings", result.get("today_actions", []))
    rows = [("HOLDING", dict(row)) for row in holdings if isinstance(row, dict)] if isinstance(holdings, list) else []
    covered = {normalize_security_code(row.get("code")) for _, row in rows}
    for position in account.get("positions", []):
        if position.get("code") not in covered and (_number(position.get("qty")) or 0) > 0:
            rows.append(("HOLDING", {"code": position["code"], "name": position.get("name"), "decision_status": "INCOMPLETE"}))
    candidates = result.get("candidates", result.get("buy_candidates", []))
    if isinstance(candidates, list):
        rows.extend(("CANDIDATE", dict(row)) for row in candidates if isinstance(row, dict))
    if not rows:
        rows = [("PORTFOLIO", {"code": "PORTFOLIO", "decision_status": "INCOMPLETE"})]
    old = db.query(TriggerPlan).filter(TriggerPlan.portfolio_id == portfolio_id, TriggerPlan.user_id == run.user_id, TriggerPlan.source_type == SOURCE).all()
    # Older runs remain visible as history but cannot replace a newer plan.
    newest = latest_plan_run(db, user_id=run.user_id, portfolio_id=portfolio_id)
    active = newest is None or newest.id == run.id
    if active:
        for previous in old:
            previous.enabled = False
    plans: list[TriggerPlan] = []
    seen_codes: set[str] = set()
    for index, (target_type, row) in enumerate(rows):
        code = normalize_security_code(row.get("code")) or ("PORTFOLIO" if target_type == "PORTFOLIO" else "UNKNOWN")
        action = str(row.get("action") or row.get("recommended_action") or row.get("candidate_type") or "").lower()
        sizing = normalize_action_sizing(row)
        duplicate = code in seen_codes
        seen_codes.add(code)
        plan = TriggerPlan(
            user_id=run.user_id, portfolio_id=portfolio_id, scope="PORTFOLIO", target_type=target_type,
            target_key=code, trigger_type="DAILY_ACTION", metric="daily_action", operator="GT", threshold=0,
            priority="P1", debounce_cycles=1, debounce_seconds=0, cooldown_seconds=1800,
            valid_from=created, expires_at=expiry, enabled=active, source_type=SOURCE, source_id=str(run.id),
            metadata_json={
                "kind": "daily_action", "version": VERSION, "sequence": index, "source_row": row,
                "action": action, "side": "BUY" if action in _BUY else "SELL" if action in _SELL else None,
                "planned_quantity": sizing.quantity, "target_weight": sizing.target_weight,
                "sizing_errors": [*sizing.errors, *(["DUPLICATE_ACTION_CODE"] if duplicate else [])], "condition": _condition(row),
                "invalidation_condition": _condition({"trigger": row.get("invalidation_plan")}),
                "source_snapshot_id": run.portfolio_snapshot_id, "source_account_version": source_account,
                "validated_account_version": source_account, "source_entry_ids": account.get("applied_entry_ids", []),
                "evidence_version": evidence_version(run), "parameter_set_hash": getattr(run, "parameter_set_hash", None),
                "fill_entry_ids": [], "depends_on_plan_ids": [], "reviews": [],
            },
        )
        db.add(plan)
        plans.append(plan)
    db.flush()
    sells = {plan.target_key: plan.id for plan in plans if plan.metadata_json["side"] == "SELL"}
    for plan in plans:
        metadata = dict(plan.metadata_json)
        row = metadata["source_row"]
        dependencies = row.get("depends_on") or row.get("depends_on_codes") or []
        dependencies = [dependencies] if isinstance(dependencies, str) else dependencies if isinstance(dependencies, list) else []
        fit = row.get("portfolio_fit") if isinstance(row.get("portfolio_fit"), dict) else {}
        source_code = row.get("funding_source_code") or fit.get("probe_source_code")
        if metadata["side"] == "BUY" and source_code:
            dependencies.append(source_code)
        metadata["depends_on_plan_ids"] = list(dict.fromkeys(sells[normalize_security_code(code)] for code in dependencies if normalize_security_code(code) in sells))
        metadata["missing_dependencies"] = [str(code) for code in dependencies if normalize_security_code(code) not in sells]
        plan.metadata_json = metadata
    db.flush()
    return plans


def _fill_projection(plan: TriggerPlan, entries: dict[int, TradeLedgerEntry], *, now: datetime) -> tuple[float, list[int], list[str]]:
    metadata = plan.metadata_json or {}
    total, ids, errors = 0.0, [], []
    for entry_id in metadata.get("fill_entry_ids", []):
        entry = entries.get(entry_id)
        if entry is None or entry.status != "CONFIRMED":
            continue
        if entry.user_id != plan.user_id or entry.portfolio_id != plan.portfolio_id or entry.security_code != plan.target_key or entry.side != metadata.get("side") or entry.entry_type != "TRADE":
            errors.append("FILL_FACT_MISMATCH")
            continue
        if _moment(entry.available_at) > now or _moment(entry.executed_at) > now:
            continue
        quantity = _number(entry.quantity)
        if quantity is None or quantity <= 0:
            errors.append("FILL_QUANTITY_INVALID")
            continue
        total += quantity
        ids.append(entry_id)
    return total, ids, errors


def _quote_reasons(code: str, quote: dict, now: datetime) -> list[str]:
    if not quote:
        return ["QUOTE_MISSING"]
    if normalize_security_code(quote.get("canonical_code") or quote.get("code")) != code:
        return ["INSTRUMENT_IDENTITY_MISMATCH"]
    if str(quote.get("quality_status") or "MISSING").upper() not in {"VALID", "DEGRADED"} or quote.get("stale"):
        return ["QUOTE_UNAVAILABLE"]
    if quote.get("instrument_status") != "ACTIVE" or quote.get("is_suspended") is not False or not isinstance(quote.get("is_st"), bool):
        return ["TRADING_STATUS_UNAVAILABLE"]
    if not quote.get("source") and not quote.get("provider"):
        return ["QUOTE_SOURCE_MISSING"]
    observed = _coerce_datetime(quote.get("source_timestamp") or quote.get("observed_at"))
    fetched = _coerce_datetime(quote.get("fetched_at"))
    if observed is None or fetched is None:
        return ["QUOTE_TIME_MISSING"]
    observed_age, fetched_age = (now - _moment(observed)).total_seconds(), (now - _moment(fetched)).total_seconds()
    if min(observed_age, fetched_age) < -5:
        return ["QUOTE_TIME_INVALID"]
    basis = quote.get("data_basis") or "live"
    if basis not in {"live", "session_close", "previous_session_close"}:
        return ["QUOTE_TIME_INVALID"]
    if fetched_age > settings.QUOTE_FRESHNESS_SECONDS or basis == "live" and observed_age > settings.QUOTE_FRESHNESS_SECONDS:
        return ["QUOTE_STALE"]
    return []


def evaluate_daily_actions(
    plans: list[TriggerPlan], *, run: Any, account: dict, entries: dict[int, TradeLedgerEntry],
    quotes: dict[str, dict], portfolio_context: dict, parameter_set_hash: str | None,
    now: datetime | None = None, latest_run_id: int | None = None,
) -> list[dict]:
    """Pure projection. Dependencies and all buys share one actual cash budget."""
    moment = _moment(now)
    payload, result = _payload(run)
    projected = {plan.id: _fill_projection(plan, entries, now=moment) for plan in plans}
    actions: list[dict] = []
    gate_rows: list[dict] = []
    source_quality = result.get("quality_gate") or {}
    required_dependencies = {plan.id: (plan.metadata_json or {}).get("planned_quantity") for plan in plans}
    for plan in plans:
        metadata = plan.metadata_json or {}
        source = metadata.get("source_row") or {}
        action = metadata.get("action", "")
        quantity = metadata.get("planned_quantity")
        filled, fill_ids, fill_errors = projected[plan.id]
        remaining = max(0.0, quantity - filled) if quantity is not None else None
        reasons: list[str] = list(fill_errors)
        status = holding_decision_status({**source, "action": "buy" if action == "new_position" else action})
        expiry = _moment(plan.expires_at) if plan.expires_at else None
        if not plan.enabled or latest_run_id is not None and str(latest_run_id) != plan.source_id:
            status, reasons = "EXPIRED", ["PLAN_SUPERSEDED"]
        elif expiry is None:
            status, reasons = "INCOMPLETE", ["PLAN_VALIDITY_MISSING"]
        elif moment >= expiry:
            status, reasons = "EXPIRED", ["PLAN_VALIDITY_EXPIRED"]
        elif evidence_version(run) != metadata.get("evidence_version"):
            status, reasons = "EXPIRED", ["EVIDENCE_CHANGED"]
        elif _research_expired(payload, plan.target_key, moment):
            status, reasons = "EXPIRED", ["EVIDENCE_EXPIRED"]
        elif metadata.get("parameter_set_hash") and metadata["parameter_set_hash"] != parameter_set_hash:
            status, reasons = "EXPIRED", ["GOVERNANCE_CHANGED"]
        elif metadata.get("source_snapshot_id") != account.get("snapshot_id"):
            status, reasons = "EXPIRED", ["PORTFOLIO_SNAPSHOT_CHANGED"]
        elif not metadata.get("source_account_version"):
            status, reasons = "INCOMPLETE", ["SOURCE_ACCOUNT_VERSION_MISSING"]
        elif metadata.get("validated_account_version") != account.get("account_version"):
            status, reasons = "EXPIRED", ["PORTFOLIO_ACCOUNT_CHANGED"]
        elif filled and remaining == 0:
            status, reasons = "NO_ACTION", ["PLAN_FILLED" if filled == quantity else "PLAN_OVERFILLED"]
        elif source_quality.get("status") == "blocked" or str(source_quality.get("grade") or "").upper() in {"D", "F"}:
            status, reasons = "DATA_GAP", ["ANALYSIS_DATA_QUALITY"]
        elif fill_errors:
            status = "DATA_GAP"
        elif plan.valid_from and moment < _moment(plan.valid_from):
            status, reasons = "WAITING", ["PLAN_NOT_YET_VALID"]
        elif metadata.get("side"):
            if quantity is None or quantity <= 0 or metadata.get("sizing_errors"):
                status, reasons = "INCOMPLETE", metadata.get("sizing_errors") or ["PLAN_QUANTITY_MISSING"]
            elif source.get("portfolio_gate") == "BLOCKED" or source.get("buyable") is False or source.get("actionable") is False:
                status, reasons = "DATA_GAP", source.get("portfolio_gate_reasons") or ["SOURCE_ACTION_BLOCKED"]
            elif source.get("decision_status") in {"INCOMPLETE", "DATA_GAP"}:
                status, reasons = source["decision_status"], ["SOURCE_ACTION_UNAVAILABLE"]
            elif plan.target_type == "CANDIDATE" and _comparison(source)["decision_status"] == "DATA_GAP":
                status, reasons = "DATA_GAP", ["COMPARISON_EVIDENCE_MISSING"]
            elif plan.target_type == "CANDIDATE" and _comparison(source)["decision_status"] == "NO_ACTION":
                status, reasons = "NO_ACTION", ["OPPORTUNITY_NOT_BETTER_THAN_ALTERNATIVES"]
            elif metadata.get("missing_dependencies"):
                status, reasons = "INCOMPLETE", ["DEPENDENCY_PLAN_MISSING"]
            elif any(dependency not in projected or projected[dependency][0] < (required_dependencies.get(dependency) or float("inf")) for dependency in metadata.get("depends_on_plan_ids", [])):
                status, reasons = "WAITING", ["SELL_DEPENDENCY_UNFILLED"]
            else:
                quote = quotes.get(plan.target_key, {})
                quote_errors = _quote_reasons(plan.target_key, quote, moment)
                condition = metadata.get("condition")
                invalidation = metadata.get("invalidation_condition")
                if quote_errors:
                    status, reasons = "DATA_GAP", quote_errors
                elif invalidation and _number(quote.get(invalidation["metric"])) is not None and compare_values(invalidation["operator"], float(quote[invalidation["metric"]]), invalidation["threshold"], _number(metadata.get("previous_invalidation_value"))):
                    status, reasons = "EXPIRED", ["PLAN_INVALIDATION_MET"]
                elif action.startswith("conditional_") and not condition:
                    status, reasons = "INCOMPLETE", ["STRUCTURED_CONDITION_MISSING"]
                elif condition and _number(quote.get(condition["metric"])) is None:
                    status, reasons = "DATA_GAP", ["CONDITION_INPUT_MISSING"]
                elif condition and condition["operator"].startswith("CROSS_") and _number(metadata.get("previous_value")) is None:
                    status, reasons = "WAITING", ["CONDITION_PREVIOUS_VALUE_MISSING"]
                elif condition and not compare_values(condition["operator"], float(quote[condition["metric"]]), condition["threshold"], _number(metadata.get("previous_value"))):
                    status, reasons = "WAITING", ["CONDITION_NOT_MET"]
                else:
                    status = "ACTION"
                    gate_rows.append({"code": plan.target_key, "action": "add" if metadata["side"] == "BUY" else "sell", "quantity": remaining, "target_weight": metadata.get("target_weight"), "_plan_id": plan.id})
        actions.append({
            "plan_id": plan.id, "code": plan.target_key, "name": source.get("name"), "target_type": plan.target_type,
            "action": action, "side": metadata.get("side"), "decision_status": status, "reason_codes": reasons,
            "planned_quantity": quantity, "filled_quantity": filled, "remaining_quantity": remaining,
            "executable_quantity": 0.0, "fill_entry_ids": fill_ids, "depends_on_plan_ids": metadata.get("depends_on_plan_ids", []),
            "condition": metadata.get("condition"), "invalidation_condition": metadata.get("invalidation_condition"), "valid_from": plan.valid_from, "expires_at": plan.expires_at,
            "source_account_version": metadata.get("source_account_version"), "current_account_version": account.get("account_version"),
            "evidence_version": metadata.get("evidence_version"), "rationale": source.get("reason") or source.get("rationale") or source.get("analysis"),
            "last_review": (metadata.get("reviews") or [None])[-1], "advisory_only": True,
        })
    if gate_rows:
        gated = apply_portfolio_decision_gate({"holdings": gate_rows, "quality_gate": source_quality, "risk_revision": result.get("risk_revision") or {}}, portfolio_context=portfolio_context)
        by_id = {row["_plan_id"]: row for row in gated["holdings"]}
        for action in actions:
            gated_row = by_id.get(action["plan_id"])
            if gated_row is None:
                continue
            action["reason_codes"].extend(gated_row.get("portfolio_gate_reasons") or [])
            if gated_row.get("portfolio_gate") == "BLOCKED":
                cash_block = any(reason in {"CASH_LIMIT", "CASH_OR_LOT_SIZE_LIMIT"} for reason in action["reason_codes"])
                action["decision_status"] = "WAITING" if cash_block else "DATA_GAP"
            else:
                action["executable_quantity"] = normalize_action_sizing(gated_row).quantity or 0.0
                if action["executable_quantity"] <= 0:
                    action["decision_status"] = "INCOMPLETE"
                    action["reason_codes"].append("EXECUTABLE_QUANTITY_MISSING")
    return actions


def _comparison(row: dict) -> dict:
    edge = row.get("decision_edge_detail") or row.get("decision_edge_details") or row.get("edge") or {}
    edge = edge if isinstance(edge, dict) else {}
    comparison = row.get("comparison") if isinstance(row.get("comparison"), dict) else {}
    no_action = _number(row.get("edge_vs_no_action", edge.get("edge_vs_no_action")))
    held = _number(row.get("edge_vs_current_holdings", edge.get("edge_vs_current_holdings")))
    return {
        "code": row.get("code"), "name": row.get("name"), "candidate_score": row.get("action_score"),
        "holding_baseline": comparison.get("held_baseline"), "edge_vs_holdings": held,
        "cash_alternative": {"label": "保留当前现金", "edge_vs_no_action": no_action, "expected_return": None},
        "decision_status": "DATA_GAP" if no_action is None or held is None else "WAITING" if no_action > 0 and held > 0 else "NO_ACTION",
        "reason_codes": ["COMPARISON_EVIDENCE_MISSING"] if no_action is None or held is None else ["OPPORTUNITY_REQUIRES_PLAN_VALIDATION"] if no_action > 0 and held > 0 else ["OPPORTUNITY_NOT_BETTER_THAN_ALTERNATIVES"],
        "funding_mode": row.get("funding_mode"), "estimated_cost": row.get("estimated_cost", edge.get("estimated_cost")),
        "advisory_only": True,
    }


def _runtime_context(db: Session, plans: list[TriggerPlan], run: AnalysisRun, *, now: datetime, force_refresh: bool) -> tuple[dict, dict, dict, dict]:
    from ..market.instrument_schemas import InstrumentQuoteResponse
    from ..market.instruments import InstrumentMarketService
    from ..services.instrument_market_evidence import _legacy_quote

    snapshot = latest_confirmed_snapshot(db, portfolio_id=run.job.portfolio_id, as_of=now)
    if snapshot is None:
        raise ValueError("confirmed_snapshot_not_found")
    account = build_account_state(db, portfolio_id=run.job.portfolio_id, snapshot=snapshot, as_of=now)
    codes = sorted({p.target_key for p in plans if normalize_security_code(p.target_key)} | {p["code"] for p in account["positions"] if p.get("code")})
    try:
        batch = InstrumentMarketService(db).batch_quotes(codes, force_refresh=force_refresh)
        quotes = {normalize_security_code(item.code): _legacy_quote(InstrumentQuoteResponse.model_validate(item.model_dump(exclude={"code"}))) for item in batch.items if item.instrument is not None}
    except Exception:
        # The facade is an external boundary; unavailable quotes close actions.
        quotes = {}
    context = portfolio_context_for_analysis(db, snapshot=snapshot, market={"quotes": quotes})
    existing = {row["code"] for row in context.get("position_constraints", [])}
    # Candidate additions use the same hard-cap builder as current positions.
    masters = db.query(SecurityMaster).filter(SecurityMaster.market == "CN", SecurityMaster.code.in_(codes)).all()
    for master in masters:
        if master.code in existing:
            continue
        quote = quotes.get(master.code, {})
        extra = build_portfolio_constraints({
            "positions": [{"code": master.code, "qty": 0, "available_qty": 0, "weight": 0,
                "security_type": master.security_type, "etf_category": master.etf_category, "current_price": quote.get("price"),
                "quote_quality": quote.get("quality_status", "MISSING"), "pct_change": quote.get("pct_change"),
                "is_suspended": quote.get("is_suspended"), "is_st": quote.get("is_st"), "board": master.board, "lot_size": master.lot_size}],
            "cash_ratio": context.get("cash_ratio"), "cash": context.get("spendable_cash"), "quality_status": context.get("portfolio_quality"),
        }, {"available": context.get("market_state_available"), "quality_status": context.get("market_quality_status"), "is_frozen": context.get("market_state_frozen")})
        context.setdefault("position_constraints", []).extend(extra["positions"])
    return account, quotes, context, resolve_production_parameters(db)


def daily_plan_view(db: Session, *, user_id: int, portfolio_id: int, now: datetime | None = None, force_refresh: bool = False) -> dict:
    moment = _moment(now)
    run = latest_plan_run(db, user_id=user_id, portfolio_id=portfolio_id)
    base = {"version": VERSION, "portfolio_id": portfolio_id, "analysis_run_id": run.id if run else None,
        "as_of": moment, "account_version": None, "evidence_version": evidence_version(run) if run else None,
        "decision_status": "INCOMPLETE", "reason_codes": [], "summary": {state: 0 for state in STATES},
        "actions": [], "comparisons": [], "advisory_only": True}
    if run is None:
        base["reason_codes"] = ["ANALYSIS_NOT_AVAILABLE"]
        return base
    plans = db.query(TriggerPlan).filter(TriggerPlan.user_id == user_id, TriggerPlan.portfolio_id == portfolio_id,
        TriggerPlan.source_type == SOURCE, TriggerPlan.source_id == str(run.id)).order_by(TriggerPlan.id).all()
    if not plans:
        base["reason_codes"] = ["DAILY_PLAN_NOT_MATERIALIZED"]
        return base
    entry_ids = {entry_id for plan in plans for entry_id in (plan.metadata_json or {}).get("fill_entry_ids", [])}
    entries = {entry.id: entry for entry in db.query(TradeLedgerEntry).filter(TradeLedgerEntry.id.in_(entry_ids), TradeLedgerEntry.user_id == user_id, TradeLedgerEntry.portfolio_id == portfolio_id).all()} if entry_ids else {}
    account, quotes, context, governance = _runtime_context(db, plans, run, now=moment, force_refresh=force_refresh)
    actions = evaluate_daily_actions(plans, run=run, account=account, entries=entries, quotes=quotes,
        portfolio_context=context, parameter_set_hash=governance.get("config_hash"), now=moment, latest_run_id=run.id)
    _, result = _payload(run)
    base.update(account_version=account["account_version"], actions=actions, cash=account.get("cash"),
        comparisons=[_comparison(row) for row in result.get("candidates", result.get("buy_candidates", [])) if isinstance(row, dict)])
    for action in actions:
        base["summary"][action["decision_status"]] += 1
    base["decision_status"] = next((state for state in ("EXPIRED", "DATA_GAP", "INCOMPLETE", "ACTION", "WAITING", "NO_ACTION") if base["summary"][state]), "INCOMPLETE")
    base["reason_codes"] = list(dict.fromkeys(reason for action in actions for reason in action["reason_codes"]))
    return base


def link_plan_fill(db: Session, plan: TriggerPlan, *, ledger_entry_id: int, now: datetime | None = None) -> None:
    """Associate one confirmed real fill, never create or infer a fill."""
    moment = _moment(now)
    metadata = deepcopy(plan.metadata_json or {})
    if metadata.get("kind") != "daily_action":
        raise ValueError("not_a_daily_action_plan")
    entry = db.query(TradeLedgerEntry).filter(TradeLedgerEntry.id == ledger_entry_id, TradeLedgerEntry.user_id == plan.user_id, TradeLedgerEntry.portfolio_id == plan.portfolio_id).first()
    if entry is None or entry.status != "CONFIRMED" or entry.entry_type != "TRADE":
        raise ValueError("confirmed_trade_not_found")
    if entry.security_code != plan.target_key or entry.side != metadata.get("side"):
        raise ValueError("fill_does_not_match_plan")
    if _moment(entry.executed_at) < _moment(plan.valid_from) or _moment(entry.available_at) > moment or _moment(entry.executed_at) > moment:
        raise ValueError("fill_outside_plan_time")
    for other in db.query(TriggerPlan).filter(TriggerPlan.portfolio_id == plan.portfolio_id, TriggerPlan.source_type == SOURCE).all():
        if other.id != plan.id and ledger_entry_id in (other.metadata_json or {}).get("fill_entry_ids", []):
            raise ValueError("fill_already_associated")
    metadata["fill_entry_ids"] = list(dict.fromkeys([*metadata.get("fill_entry_ids", []), ledger_entry_id]))
    plan.metadata_json = metadata
    db.flush()


def recheck_daily_plan(db: Session, plan: TriggerPlan, *, now: datetime | None = None) -> dict:
    """Revalidate only expected linked-fill account deltas; other changes need a new analysis."""
    moment = _moment(now)
    run = db.get(AnalysisRun, int(plan.source_id))
    if run is None or plan.source_type != SOURCE:
        raise ValueError("daily_analysis_not_found")
    plans = db.query(TriggerPlan).filter(TriggerPlan.portfolio_id == plan.portfolio_id, TriggerPlan.source_type == SOURCE, TriggerPlan.source_id == plan.source_id).all()
    account, quotes, context, governance = _runtime_context(db, plans, run, now=moment, force_refresh=True)
    all_fill_ids = {entry_id for p in plans for entry_id in (p.metadata_json or {}).get("fill_entry_ids", [])}
    metadata = deepcopy(plan.metadata_json or {})
    baseline = set(metadata.get("source_entry_ids", []))
    current_ids = set(account["applied_entry_ids"])
    if not baseline <= current_ids or current_ids - baseline - all_fill_ids:
        raise ValueError("account_change_requires_new_analysis")
    # Baseline revisions cannot be acknowledged by attaching an unrelated fill.
    source_run_time = _moment(run.completed_at or run.created_at)
    changed_baseline = db.query(TradeLedgerEntry.id).filter(TradeLedgerEntry.id.in_(baseline), TradeLedgerEntry.updated_at > source_run_time.replace(tzinfo=None)).first() if baseline else None
    if changed_baseline:
        raise ValueError("account_revision_requires_new_analysis")
    metadata["validated_account_version"] = account["account_version"]
    entries = {entry.id: entry for entry in db.query(TradeLedgerEntry).filter(TradeLedgerEntry.id.in_(all_fill_ids)).all()} if all_fill_ids else {}
    plan.metadata_json = metadata
    newest = latest_plan_run(db, user_id=plan.user_id, portfolio_id=plan.portfolio_id)
    actions = evaluate_daily_actions(plans, run=run, account=account, entries=entries, quotes=quotes,
        portfolio_context=context, parameter_set_hash=governance.get("config_hash"), now=moment, latest_run_id=newest.id if newest else None)
    action = next(row for row in actions if row["plan_id"] == plan.id)
    review = {"reviewed_at": moment.isoformat(), "account_version": account["account_version"], "evidence_version": evidence_version(run),
        "decision_status": action["decision_status"], "reason_codes": action["reason_codes"], "remaining_quantity": action["remaining_quantity"],
        "executable_quantity": action["executable_quantity"]}
    metadata["reviews"] = [*metadata.get("reviews", []), review]
    condition = metadata.get("condition")
    if condition and not _quote_reasons(plan.target_key, quotes.get(plan.target_key, {}), moment):
        quote = quotes[plan.target_key]
        current = _number(quote.get(condition["metric"]))
        previous = _number(metadata.get("previous_value"))
        if current is not None and (not condition["operator"].startswith("CROSS_") or previous is not None):
            review["condition_observation"] = {"observed_at": moment.isoformat(), "current_value": current,
                "met": compare_values(condition["operator"], current, condition["threshold"], previous),
                "quote_source": quote.get("source") or quote.get("provider"),
                "quote_timestamp": quote.get("source_timestamp") or quote.get("observed_at")}
        metadata["previous_value"] = current
    invalidation = metadata.get("invalidation_condition")
    if invalidation and not _quote_reasons(plan.target_key, quotes.get(plan.target_key, {}), moment):
        metadata["previous_invalidation_value"] = _number(quotes[plan.target_key].get(invalidation["metric"]))
    plan.metadata_json = metadata
    plan.last_evaluated_at = moment
    db.flush()
    action["last_review"] = review
    return action
