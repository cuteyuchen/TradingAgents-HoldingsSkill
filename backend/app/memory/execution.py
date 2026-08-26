"""Deterministic recommendation-to-execution alignment."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..portfolio.snapshot_diff import upsert_snapshot_diff
from ..portfolio_models import TradeLedgerEntry, TradeLedgerRevision
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from ..v2_models import AnalysisJob, AnalysisRun, PortfolioSnapshot
from .config import (
    CHINA_SESSION_CLOSE,
    DECISION_EXECUTION_WINDOW_TRADING_DAYS,
    EXECUTION_FULL_RATIO_MIN,
)
from .decision import canonical_action
from .models import DecisionMemory, DecisionOutcome

LONG_ACTIONS = frozenset({"add", "conditional_add", "new_position"})
SHORT_ACTIONS = frozenset({"reduce", "sell", "exit"})
PASSIVE_ACTIONS = frozenset({"hold", "no_action", "watch_only", "watch"})


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def execution_window(
    db: Session,
    decision_at: datetime,
    *,
    trading_days: int = DECISION_EXECUTION_WINDOW_TRADING_DAYS,
) -> tuple[datetime | None, datetime | None]:
    """Return UTC-naive execution window bounds through a completed session close."""

    if trading_days <= 0:
        raise ValueError("execution_window_trading_days_must_be_positive")
    local = (decision_at.replace(tzinfo=UTC) if decision_at.tzinfo is None else decision_at).astimezone(CHINA_TZ)
    calendar = TradingCalendarService(db)
    row = calendar.row_for(local.date())
    same_day = bool(row and row.is_open and local.time() < CHINA_SESSION_CLOSE)
    first_date = local.date() if same_day else calendar.next_trading_day(local.date())
    if first_date is None:
        return None, None
    from ..market_models import TradingCalendar

    dates = db.execute(select(TradingCalendar.trade_date).where(
        TradingCalendar.market == "CN",
        TradingCalendar.trade_date >= first_date,
        TradingCalendar.is_open.is_(True),
    ).order_by(TradingCalendar.trade_date.asc()).limit(trading_days)).scalars().all()
    if len(dates) < trading_days:
        return None, None
    end_date = dates[-1]
    end_at = datetime.combine(end_date, CHINA_SESSION_CLOSE, tzinfo=CHINA_TZ).astimezone(UTC).replace(tzinfo=None)
    return _utc_naive(decision_at), end_at


def _target_payload(outcome: DecisionOutcome) -> dict[str, Any]:
    return {
        "target_type": outcome.target_type,
        "target_key": outcome.target_key,
        "recommended_action": canonical_action(outcome.recommended_action, default="no_action"),
        "recommended_qty": outcome.recommended_qty,
        "recommended_weight": outcome.recommended_weight,
        "target_weight": outcome.target_weight,
    }


def _ledger_rows(
    db: Session,
    memory: DecisionMemory,
    *,
    target_key: str,
    start: datetime,
    end: datetime,
    cutoff: datetime,
) -> tuple[list[TradeLedgerEntry], str]:
    base = [
        TradeLedgerEntry.user_id == memory.user_id,
        TradeLedgerEntry.portfolio_id == memory.portfolio_id,
        TradeLedgerEntry.security_code == target_key,
        TradeLedgerEntry.entry_type == "TRADE",
        TradeLedgerEntry.status == "CONFIRMED",
        TradeLedgerEntry.executed_at >= start,
        TradeLedgerEntry.executed_at <= end,
        TradeLedgerEntry.available_at <= cutoff,
    ]
    explicit = db.execute(select(TradeLedgerEntry).where(
        *base,
        TradeLedgerEntry.analysis_run_id == memory.analysis_run_id,
    ).order_by(TradeLedgerEntry.executed_at.asc(), TradeLedgerEntry.id.asc())).scalars().all()
    if explicit:
        return explicit, "explicit_analysis_run"
    inferred = db.execute(select(TradeLedgerEntry).where(*base).order_by(
        TradeLedgerEntry.executed_at.asc(), TradeLedgerEntry.id.asc()
    )).scalars().all()
    return inferred, "unlinked_trade_in_window" if inferred else "none"


def _snapshot_evidence(
    db: Session,
    memory: DecisionMemory,
    *,
    target_key: str,
    end: datetime,
    cutoff: datetime,
) -> dict[str, Any]:
    before = db.execute(select(PortfolioSnapshot).where(
        PortfolioSnapshot.user_id == memory.user_id,
        PortfolioSnapshot.portfolio_id == memory.portfolio_id,
        PortfolioSnapshot.status == "confirmed",
        PortfolioSnapshot.snapshot_time <= memory.decision_at,
    ).order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).limit(1)).scalar_one_or_none()
    after = db.execute(select(PortfolioSnapshot).where(
        PortfolioSnapshot.user_id == memory.user_id,
        PortfolioSnapshot.portfolio_id == memory.portfolio_id,
        PortfolioSnapshot.status == "confirmed",
        PortfolioSnapshot.snapshot_time >= end,
        PortfolioSnapshot.snapshot_time <= cutoff,
    ).order_by(PortfolioSnapshot.snapshot_time.asc(), PortfolioSnapshot.id.asc()).limit(1)).scalar_one_or_none()
    if before is None or after is None:
        return {
            "before_snapshot_id": before.id if before else None,
            "after_snapshot_id": after.id if after else None,
            "snapshot_diff_ids": [],
            "reconciliation_status": None,
            "quantity_delta": None,
            "before_qty": None,
            "after_qty": None,
            "reliable": False,
        }
    diff_row = upsert_snapshot_diff(db, before=before, after=after)
    diff = diff_row.diff_json or {}
    rows = [row for row in diff.get("positions") or [] if str(row.get("code") or "") == target_key]
    row = rows[0] if rows else {"before_qty": 0.0, "after_qty": 0.0, "qty_delta": 0.0}
    return {
        "before_snapshot_id": before.id,
        "after_snapshot_id": after.id,
        "snapshot_diff_ids": [diff_row.id],
        "reconciliation_status": diff_row.reconciliation_status,
        "quantity_delta": row.get("qty_delta"),
        "before_qty": row.get("before_qty"),
        "after_qty": row.get("after_qty"),
        "reliable": diff_row.reconciliation_status == "MATCHED",
    }


def evaluate_execution_alignment(
    db: Session,
    memory: DecisionMemory,
    outcome: DecisionOutcome,
    *,
    calculation_as_of: datetime | None = None,
) -> dict[str, Any]:
    """Classify execution facts without interpreting model prose."""

    cutoff = _utc_naive(calculation_as_of) or datetime.now(UTC).replace(tzinfo=None)
    action = canonical_action(outcome.recommended_action, default="no_action")
    if outcome.target_type == "PORTFOLIO" or outcome.target_key == "PORTFOLIO":
        return {
            "alignment": "NOT_APPLICABLE",
            "execution_ratio": None,
            "executed_qty": None,
            "weighted_avg_execution_price": None,
            "evidence": {"alignment_reason_codes": ["PORTFOLIO_TARGET"]},
        }
    if action == "replace":
        return {
            "alignment": "UNRESOLVED",
            "execution_ratio": None,
            "executed_qty": None,
            "weighted_avg_execution_price": None,
            "evidence": {
                "ledger_entry_ids": [],
                "snapshot_diff_ids": [],
                "before_snapshot_id": None,
                "after_snapshot_id": None,
                "execution_window_start": None,
                "execution_window_end": None,
                "quantity_delta": None,
                "executed_qty": None,
                "weighted_avg_execution_price": None,
                "execution_ratio": None,
                "alignment_reason_codes": ["REPLACE_PAIR_REQUIRED"],
                "association_mode": "none",
            },
        }
    start, end = execution_window(db, memory.decision_at)
    if start is None or end is None:
        return {
            "alignment": "UNRESOLVED",
            "execution_ratio": None,
            "executed_qty": None,
            "weighted_avg_execution_price": None,
            "evidence": {"alignment_reason_codes": ["TRADING_CALENDAR_RANGE_MISSING"]},
        }
    rows, association_mode = _ledger_rows(
        db,
        memory,
        target_key=outcome.target_key,
        start=start,
        end=end,
        cutoff=cutoff,
    )
    if association_mode == "unlinked_trade_in_window":
        return {
            "alignment": "UNRESOLVED",
            "execution_ratio": None,
            "executed_qty": None,
            "weighted_avg_execution_price": None,
            "evidence": {
                "ledger_entry_ids": [row.id for row in rows],
                "snapshot_diff_ids": [],
                "before_snapshot_id": None,
                "after_snapshot_id": None,
                "execution_window_start": _json_value(start),
                "execution_window_end": _json_value(end),
                "quantity_delta": None,
                "executed_qty": None,
                "weighted_avg_execution_price": None,
                "execution_ratio": None,
                "alignment_reason_codes": ["UNLINKED_LEDGER_IN_WINDOW"],
                "association_mode": association_mode,
            },
        }
    expected_side = "BUY" if action in LONG_ACTIONS else "SELL" if action in SHORT_ACTIONS else None
    aligned = [row for row in rows if expected_side and row.side == expected_side]
    opposite = [row for row in rows if expected_side and row.side != expected_side]
    executed_qty = sum(float(row.quantity or 0.0) for row in aligned)
    weighted_price = (
        sum(float(row.quantity or 0.0) * float(row.price or 0.0) for row in aligned) / executed_qty
        if executed_qty > 0 else None
    )
    recommended_qty = outcome.recommended_qty
    ratio = executed_qty / recommended_qty if recommended_qty and recommended_qty > 0 else (1.0 if executed_qty > 0 else None)
    reason_codes: list[str] = []
    if association_mode == "explicit_analysis_run":
        reason_codes.append("EXPLICIT_ANALYSIS_RUN_LINK")
    elif association_mode == "window_code_inference":
        reason_codes.append("WINDOW_CODE_ASSOCIATION")
    if aligned:
        reason_codes.append("DIRECTION_MATCH")
    if opposite:
        reason_codes.append("DIRECTION_OPPOSITE")
    if expected_side is None and rows:
        reason_codes.append("ACTIVE_TRADE_DURING_PASSIVE_RECOMMENDATION")
        return {
            "alignment": "OPPOSITE",
            "execution_ratio": None,
            "executed_qty": executed_qty or None,
            "weighted_avg_execution_price": weighted_price,
            "evidence": {
                "ledger_entry_ids": [row.id for row in rows],
                "snapshot_diff_ids": [],
                "before_snapshot_id": None,
                "after_snapshot_id": None,
                "execution_window_start": _json_value(start),
                "execution_window_end": _json_value(end),
                "quantity_delta": executed_qty,
                "executed_qty": executed_qty or None,
                "weighted_avg_execution_price": weighted_price,
                "execution_ratio": None,
                "alignment_reason_codes": reason_codes,
                "association_mode": association_mode,
            },
        }
    if opposite and not aligned:
        alignment = "OPPOSITE"
    elif aligned and opposite:
        alignment = "PARTIAL"
    elif aligned and (ratio is None or ratio >= 0.80):
        alignment = "FOLLOWED"
    elif aligned:
        alignment = "PARTIAL"
    else:
        snapshot = _snapshot_evidence(db, memory, target_key=outcome.target_key, end=end, cutoff=cutoff)
        if cutoff < end:
            alignment = "UNRESOLVED"
            reason_codes.append("EXECUTION_WINDOW_OPEN")
        elif snapshot["reliable"] and abs(float(snapshot.get("quantity_delta") or 0.0)) < 1e-8:
            alignment = "FOLLOWED" if action in PASSIVE_ACTIONS else "IGNORED"
            reason_codes.append("CONFIRMED_SNAPSHOT_NO_QUANTITY_CHANGE")
        elif snapshot["reliable"]:
            alignment = "UNRESOLVED"
            reason_codes.append("SNAPSHOT_QUANTITY_CHANGED_WITHOUT_LINKED_LEDGER")
        else:
            alignment = "UNRESOLVED"
            reason_codes.append("NO_CONFIRMED_SNAPSHOT_EVIDENCE")
        return {
            "alignment": alignment,
            "execution_ratio": None,
            "executed_qty": None,
            "weighted_avg_execution_price": None,
            "evidence": {
                **snapshot,
                "ledger_entry_ids": [],
                "execution_window_start": _json_value(start),
                "execution_window_end": _json_value(end),
                "quantity_delta": snapshot.get("quantity_delta"),
                "executed_qty": None,
                "weighted_avg_execution_price": None,
                "execution_ratio": None,
                "alignment_reason_codes": reason_codes,
                "association_mode": association_mode,
            },
        }
    return {
        "alignment": alignment,
        "execution_ratio": ratio,
        "executed_qty": executed_qty or None,
        "weighted_avg_execution_price": weighted_price,
        "evidence": {
            "ledger_entry_ids": [row.id for row in rows],
            "snapshot_diff_ids": [],
            "before_snapshot_id": None,
            "after_snapshot_id": None,
            "execution_window_start": _json_value(start),
            "execution_window_end": _json_value(end),
            "quantity_delta": executed_qty,
            "executed_qty": executed_qty or None,
            "weighted_avg_execution_price": weighted_price,
            "execution_ratio": ratio,
            "alignment_reason_codes": reason_codes,
            "association_mode": association_mode,
        },
    }


def refresh_execution_alignments(
    db: Session,
    *,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    decision_memory_id: int | None = None,
    calculation_as_of: datetime | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    query = select(DecisionOutcome, DecisionMemory).join(
        DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id
    )
    if user_id is not None:
        query = query.where(DecisionMemory.user_id == user_id)
    if portfolio_id is not None:
        query = query.where(DecisionMemory.portfolio_id == portfolio_id)
    if decision_memory_id is not None:
        query = query.where(DecisionMemory.id == decision_memory_id)
    rows = db.execute(query).all()
    counts: dict[str, int] = {}
    touched = 0
    from .outcomes import execution_dependent_values

    for outcome, memory in rows:
        result = evaluate_execution_alignment(db, memory, outcome, calculation_as_of=calculation_as_of)
        outcome.execution_alignment = result["alignment"]
        outcome.source_refs_json = {
            **(outcome.source_refs_json or {}),
            "execution": result.get("evidence") or {},
        }
        execution_values = execution_dependent_values(
            db,
            memory,
            outcome,
            calculation_as_of=calculation_as_of,
        )
        for field, value in execution_values.items():
            setattr(outcome, field, value)
        counts[result["alignment"]] = counts.get(result["alignment"], 0) + 1
        touched += 1
    if persist:
        db.commit()
    return {"status": "completed", "outcomes_considered": len(rows), "outcomes_touched": touched, "counts": counts}


def link_ledger_entry_to_decision(
    db: Session,
    *,
    memory: DecisionMemory,
    ledger_entry: TradeLedgerEntry,
    user_id: int,
    reason: str,
) -> TradeLedgerEntry:
    """Attach an existing ledger fact through the existing revision audit trail."""

    if memory.user_id != user_id or ledger_entry.user_id != user_id:
        raise ValueError("memory_ledger_ownership_mismatch")
    if memory.portfolio_id != ledger_entry.portfolio_id:
        raise ValueError("memory_ledger_portfolio_mismatch")
    if ledger_entry.entry_type != "TRADE":
        raise ValueError("only_trade_entries_can_be_linked")
    target_keys = {
        str(item.get("target_key") or "").strip()
        for item in [*(memory.holding_decisions_json or []), *(memory.candidate_decisions_json or [])]
        if isinstance(item, dict) and item.get("target_key")
    }
    if not target_keys or str(ledger_entry.security_code or "").strip() not in target_keys:
        raise ValueError("ledger_security_not_in_decision")
    run = db.get(AnalysisRun, memory.analysis_run_id)
    if run is None or run.user_id != user_id:
        raise ValueError("memory_analysis_run_ownership_mismatch")
    job = db.get(AnalysisJob, run.job_id)
    if job is None or job.user_id != user_id or job.portfolio_id != memory.portfolio_id:
        raise ValueError("memory_analysis_job_portfolio_mismatch")
    snapshot = db.get(PortfolioSnapshot, run.portfolio_snapshot_id)
    if snapshot is None or snapshot.user_id != user_id or snapshot.portfolio_id != memory.portfolio_id:
        raise ValueError("memory_portfolio_snapshot_ownership_mismatch")
    if ledger_entry.status == "VOIDED":
        raise ValueError("voided_ledger_entry_cannot_be_linked")
    if not reason or not reason.strip():
        raise ValueError("link_reason_is_required")
    if ledger_entry.analysis_run_id not in (None, memory.analysis_run_id):
        raise ValueError("ledger_already_linked_to_other_analysis")
    if ledger_entry.analysis_run_id == memory.analysis_run_id:
        return ledger_entry
    revision_no = (db.scalar(select(func.max(TradeLedgerRevision.revision_no)).where(
        TradeLedgerRevision.ledger_entry_id == ledger_entry.id
    )) or 0) + 1
    db.add(TradeLedgerRevision(
        ledger_entry_id=ledger_entry.id,
        revision_no=revision_no,
        changes_json={
            "before": {"analysis_run_id": ledger_entry.analysis_run_id},
            "changes": {"analysis_run_id": memory.analysis_run_id},
            "after": {"analysis_run_id": memory.analysis_run_id},
        },
        reason=reason.strip(),
        created_by_user_id=user_id,
    ))
    ledger_entry.analysis_run_id = memory.analysis_run_id
    db.flush()
    from .outcomes import invalidate_execution_dependent_outcomes

    invalidate_execution_dependent_outcomes(
        db,
        memory_id=memory.id,
        ledger_entry=ledger_entry,
        calculation_as_of=datetime.now(UTC).replace(tzinfo=None),
        persist=False,
    )
    return ledger_entry


__all__ = [
    "evaluate_execution_alignment",
    "execution_window",
    "link_ledger_entry_to_decision",
    "refresh_execution_alignments",
]
