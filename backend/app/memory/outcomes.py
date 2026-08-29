"""Deterministic trading-day Outcome calculation and refresh."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache
from ..market_models import TradingCalendar
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from ..portfolio_models import TradeLedgerEntry
from .config import (
    CHINA_SESSION_CLOSE,
    OUTCOME_HORIZONS,
    OUTCOME_VERSION,
)
from .decision import canonical_action
from .models import DecisionMemory, DecisionOutcome

OUTCOME_ACTIONS_LONG = frozenset({"add", "conditional_add", "new_position"})
OUTCOME_ACTIONS_SHORT = frozenset({"reduce", "sell", "exit"})
VALID_BAR_QUALITY = frozenset({"VALID", "DEGRADED"})
QFQ_REFERENCE_BASES = frozenset({"QFQ", "ADJUSTED_QFQ", "QFQ_CLOSE", "DAILY_BAR_QFQ"})


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _contains_quote_conflict(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("quality_status", "quote_quality", "data_quality", "quality_grade"):
        if str(value.get(key) or "").upper() in {"CONFLICT", "BLOCKED", "INVALID"}:
            return True
    quotes = value.get("quotes")
    if isinstance(quotes, dict):
        return any(_contains_quote_conflict(item) for item in quotes.values())
    return False


def _utc_session_close(day: date) -> datetime:
    return datetime.combine(day, CHINA_SESSION_CLOSE, tzinfo=CHINA_TZ).astimezone(UTC).replace(tzinfo=None)


def _decision_local(value: datetime) -> datetime:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(CHINA_TZ)


def _is_intraday_decision(db: Session, decision_at: datetime) -> bool:
    local = _decision_local(decision_at)
    row = TradingCalendarService(db).row_for(local.date())
    return bool(row and row.is_open and local.time() < CHINA_SESSION_CLOSE)


def _normalise_price_basis(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _price_basis_compatible(reference_basis: Any, adjustment: str) -> bool:
    basis = _normalise_price_basis(reference_basis)
    target = _normalise_price_basis(adjustment)
    if not basis or not target:
        return False
    if target == "QFQ":
        return basis in QFQ_REFERENCE_BASES or basis.endswith("_QFQ")
    return basis == target


def completed_session_dates(
    db: Session,
    decision_at: datetime,
    horizon: int,
    *,
    market: str = "CN",
) -> list[date]:
    """Return the completed session dates after a decision, using persisted calendar facts."""

    if horizon <= 0:
        raise ValueError("horizon_must_be_positive")
    local = _decision_local(decision_at)
    calendar = TradingCalendarService(db, market=market)
    row = calendar.row_for(local.date())
    same_day = bool(row and row.is_open and local.time() < CHINA_SESSION_CLOSE)
    start = local.date() if same_day else calendar.next_trading_day(local.date())
    if start is None:
        return []
    rows = db.execute(
        select(TradingCalendar.trade_date).where(
            TradingCalendar.market == market,
            TradingCalendar.trade_date >= start,
            TradingCalendar.is_open.is_(True),
        ).order_by(TradingCalendar.trade_date.asc()).limit(horizon)
    ).scalars().all()
    return list(rows)


def horizon_trade_date(db: Session, decision_at: datetime, horizon: int, *, market: str = "CN") -> date | None:
    dates = completed_session_dates(db, decision_at, horizon, market=market)
    return dates[-1] if len(dates) == horizon else None


def _bars_for_target(
    db: Session,
    *,
    code: str,
    start_date: date,
    end_date: date,
    cutoff: datetime,
    adjustment: str = "QFQ",
) -> list[DailyBarCache]:
    return db.execute(
        select(DailyBarCache).where(
            DailyBarCache.market == "CN",
            DailyBarCache.code == code,
            DailyBarCache.trade_date >= start_date,
            DailyBarCache.trade_date <= end_date,
            DailyBarCache.adjustment == adjustment,
            DailyBarCache.available_at.is_not(None),
            DailyBarCache.available_at <= cutoff,
            DailyBarCache.quality_status.in_(tuple(VALID_BAR_QUALITY)),
        ).order_by(DailyBarCache.trade_date.asc(), DailyBarCache.id.asc())
    ).scalars().all()


def _raw_bars_at_date(db: Session, *, code: str, trade_date: date, adjustment: str = "QFQ") -> list[DailyBarCache]:
    return db.execute(select(DailyBarCache).where(
        DailyBarCache.market == "CN",
        DailyBarCache.code == code,
        DailyBarCache.trade_date == trade_date,
        DailyBarCache.adjustment == adjustment,
    ).order_by(DailyBarCache.id.desc())).scalars().all()


def _benchmark_rows(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    cutoff: datetime,
) -> list[AllAMedianIndexDaily]:
    return db.execute(select(AllAMedianIndexDaily).where(
        AllAMedianIndexDaily.market == "CN",
        AllAMedianIndexDaily.trade_date >= start_date,
        AllAMedianIndexDaily.trade_date <= end_date,
        AllAMedianIndexDaily.available_at.is_not(None),
        AllAMedianIndexDaily.available_at <= cutoff,
        AllAMedianIndexDaily.quality_status.in_(tuple(VALID_BAR_QUALITY)),
    ).order_by(AllAMedianIndexDaily.trade_date.asc(), AllAMedianIndexDaily.id.asc())).scalars().all()


def _execution_rows(db: Session, memory: DecisionMemory, target_key: str, *, cutoff: datetime) -> list[TradeLedgerEntry]:
    from .execution import execution_window

    start, end = execution_window(db, memory.decision_at)
    if start is None or end is None:
        return []
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
    return explicit


def _weighted_execution(rows: list[TradeLedgerEntry], action: str) -> dict[str, Any]:
    direction = "BUY" if action in OUTCOME_ACTIONS_LONG else "SELL" if action in OUTCOME_ACTIONS_SHORT else None
    relevant = [row for row in rows if direction is None or row.side == direction]
    qty = sum(float(row.quantity or 0.0) for row in relevant)
    if qty <= 0:
        return {"rows": relevant, "executed_qty": None, "price": None, "fees": None, "taxes": None, "net_price": None}
    price = sum(float(row.quantity or 0.0) * float(row.price or 0.0) for row in relevant) / qty
    fees = sum(float(row.fees or 0.0) for row in relevant)
    taxes = sum(float(row.taxes or 0.0) for row in relevant)
    net_price = price + (fees + taxes) / qty if direction == "BUY" else price - (fees + taxes) / qty
    return {
        "rows": relevant,
        "executed_qty": qty,
        "price": price,
        "fees": fees,
        "taxes": taxes,
        "net_price": net_price,
    }


def execution_dependent_values(
    db: Session,
    memory: DecisionMemory,
    outcome: DecisionOutcome,
    *,
    calculation_as_of: datetime | None = None,
) -> dict[str, Any]:
    """Refresh only ledger-derived fields without reinterpreting market facts."""

    cutoff = _utc_naive(calculation_as_of) or _now()
    action = canonical_action(outcome.recommended_action, default="no_action")
    rows = _execution_rows(db, memory, str(outcome.target_key or "").strip(), cutoff=cutoff)
    execution = _weighted_execution(rows, action)
    end_price = _number(outcome.end_price)
    adjustment = str((outcome.source_refs_json or {}).get("adjustment") or "QFQ")
    compatible = _price_basis_compatible(outcome.reference_price_basis, adjustment)
    actual_price = execution["price"]
    net_price = execution.get("net_price")
    actual_return = (
        end_price / actual_price - 1.0
        if compatible and end_price is not None and actual_price and actual_price > 0
        else None
    )
    net_return = (
        end_price / net_price - 1.0
        if compatible and end_price is not None and net_price and net_price > 0
        else None
    )
    return {
        "actual_execution_price": actual_price,
        "actual_executed_qty": execution["executed_qty"],
        "actual_execution_return": actual_return,
        "net_execution_return": net_return,
        "execution_fees": execution["fees"],
        "execution_taxes": execution["taxes"],
    }


def _directional_values(action: str, raw_return: float | None, mfe: float | None, mae: float | None) -> dict[str, float | None]:
    if action in OUTCOME_ACTIONS_LONG:
        return {"directional_return": raw_return, "directional_mfe": mfe, "directional_mae": mae}
    if action in OUTCOME_ACTIONS_SHORT:
        return {
            "directional_return": -raw_return if raw_return is not None else None,
            "directional_mfe": -mae if mae is not None else None,
            "directional_mae": -mfe if mfe is not None else None,
        }
    return {"directional_return": None, "directional_mfe": None, "directional_mae": None}


def calculate_decision_outcome(
    db: Session,
    memory: DecisionMemory,
    outcome: DecisionOutcome,
    *,
    calculation_as_of: datetime | None = None,
) -> dict[str, Any]:
    """Calculate one target/horizon without reading data after ``calculation_as_of``."""

    cutoff = _utc_naive(calculation_as_of) or _now()
    target_key = str(outcome.target_key or "").strip()
    action = canonical_action(outcome.recommended_action, default="no_action")
    if outcome.target_type == "PORTFOLIO" or not target_key or target_key == "PORTFOLIO":
        return {
            "status": "NOT_APPLICABLE",
            "quality_status": "DEGRADED",
            "confidence": 0.0,
            "available_at": None,
            "computed_at": cutoff,
            "source_refs": {"reason": "portfolio_descriptive_outcome_not_implemented"},
        }
    dates = completed_session_dates(db, memory.decision_at, int(outcome.horizon_trading_days))
    if len(dates) < int(outcome.horizon_trading_days):
        return {
            "status": "BLOCKED",
            "quality_status": "BLOCKED",
            "confidence": 0.0,
            "available_at": None,
            "computed_at": cutoff,
            "source_refs": {"reason": "trading_calendar_range_missing"},
        }
    target_date = dates[-1]
    target_close = _utc_session_close(target_date)
    if cutoff < target_close:
        return {
            "status": "PENDING",
            "quality_status": "PENDING",
            "confidence": 0.0,
            "target_trade_date": target_date,
            "available_at": target_close,
            "computed_at": cutoff,
            "source_refs": {"reason": "horizon_not_matured", "target_trade_date": target_date.isoformat()},
        }
    if str(memory.quality_status or "").upper() in {"BLOCKED", "MISSING"} or _contains_quote_conflict(memory.market_context_json):
        return {
            "status": "BLOCKED",
            "quality_status": "BLOCKED",
            "confidence": 0.0,
            "target_trade_date": target_date,
            "available_at": target_close,
            "computed_at": cutoff,
            "source_refs": {"reason": "decision_reference_quality_blocked"},
        }
    reference_date = memory.decision_at.replace(tzinfo=UTC).astimezone(CHINA_TZ).date()
    reference_price = outcome.reference_price
    if reference_price is None or reference_price <= 0:
        return {
            "status": "BLOCKED",
            "quality_status": "BLOCKED",
            "confidence": 0.0,
            "target_trade_date": target_date,
            "computed_at": cutoff,
            "source_refs": {"reason": "reference_price_missing"},
        }
    adjustment = "QFQ"
    target_rows = _raw_bars_at_date(db, code=target_key, trade_date=target_date, adjustment=adjustment)
    available_target_rows = [
        row for row in target_rows
        if row.available_at is not None and _utc_naive(row.available_at) <= cutoff and row.quality_status in VALID_BAR_QUALITY
    ]
    if not available_target_rows:
        future_rows = [row for row in target_rows if row.available_at is not None and _utc_naive(row.available_at) > cutoff]
        return {
            "status": "PENDING" if future_rows else "WAITING_DATA",
            "quality_status": "PENDING" if future_rows else "WAITING_DATA",
            "confidence": 0.0,
            "target_trade_date": target_date,
            "available_at": min((_utc_naive(row.available_at) for row in future_rows if row.available_at), default=None),
            "computed_at": cutoff,
            "source_refs": {"reason": "target_daily_bar_missing", "target_trade_date": target_date.isoformat()},
        }
    end_bar = available_target_rows[0]
    bars = _bars_for_target(
        db,
        code=target_key,
        start_date=reference_date,
        end_date=target_date,
        cutoff=cutoff,
        adjustment=adjustment,
    )
    if not bars:
        return {
            "status": "WAITING_DATA",
            "quality_status": "WAITING_DATA",
            "confidence": 0.0,
            "target_trade_date": target_date,
            "end_price": end_bar.close,
            "computed_at": cutoff,
            "source_refs": {"reason": "path_daily_bars_missing"},
        }
    end_price = _number(end_bar.close)
    if end_price is None or end_price <= 0:
        return {
            "status": "WAITING_DATA",
            "quality_status": "WAITING_DATA",
            "confidence": 0.0,
            "target_trade_date": target_date,
            "computed_at": cutoff,
            "source_refs": {"reason": "target_close_missing"},
        }
    execution = _weighted_execution(_execution_rows(db, memory, target_key, cutoff=cutoff), action)
    actual_price = execution["price"]
    reference_basis = _normalise_price_basis(outcome.reference_price_basis)
    basis_compatible = _price_basis_compatible(reference_basis, adjustment)
    actual_return = end_price / actual_price - 1.0 if basis_compatible and actual_price and actual_price > 0 else None
    net_return = (
        end_price / execution["net_price"] - 1.0
        if basis_compatible and execution.get("net_price") and execution["net_price"] > 0
        else None
    )
    if not basis_compatible:
        return {
            "status": "DEGRADED",
            "quality_status": "DEGRADED",
            "confidence": 0.0,
            "target_trade_date": target_date,
            "end_price": end_price,
            "raw_return": None,
            "benchmark_return": None,
            "excess_return": None,
            "mfe": None,
            "mae": None,
            "directional_mfe": None,
            "directional_mae": None,
            "directional_return": None,
            "directional_excess_return": None,
            "actual_execution_price": actual_price,
            "actual_executed_qty": execution["executed_qty"],
            "actual_execution_return": None,
            "net_execution_return": None,
            "execution_fees": execution["fees"],
            "execution_taxes": execution["taxes"],
            "available_at": max(
                target_close,
                *[_utc_naive(row.available_at) for row in bars if row.available_at is not None],
            ),
            "computed_at": cutoff,
            "source_refs": {
                "adjustment": adjustment,
                "price_return_basis": adjustment,
                "reference_price_basis": reference_basis or None,
                "price_basis_compatible": False,
                "reason_codes": ["PRICE_BASIS_MISMATCH"],
                "bar_ids": [row.id for row in bars],
                "reference_bar_ids": [row.id for row in bars if row.trade_date == reference_date],
                "target_bar_id": end_bar.id,
                "ledger_entry_ids": [row.id for row in execution["rows"]],
                "execution_window_trading_days": 2,
            },
        }

    intraday = _is_intraday_decision(db, memory.decision_at)
    path_start_date = dates[1] if intraday and len(dates) > 1 else reference_date
    path_bars = [row for row in bars if row.trade_date >= path_start_date] if not (intraday and len(dates) == 1) else []
    highs = [float(row.high) for row in path_bars if row.high is not None and float(row.high) > 0]
    lows = [float(row.low) for row in path_bars if row.low is not None and float(row.low) > 0]
    raw_return = end_price / float(reference_price) - 1.0
    mfe = max((high / float(reference_price) - 1.0 for high in highs), default=None)
    mae = min((low / float(reference_price) - 1.0 for low in lows), default=None)
    directional = _directional_values(action, raw_return, mfe, mae)
    benchmark_rows = _benchmark_rows(db, start_date=reference_date, end_date=target_date, cutoff=cutoff)
    benchmark_by_date = {row.trade_date: row for row in benchmark_rows}
    reference_index = benchmark_by_date.get(reference_date)
    target_index = benchmark_by_date.get(target_date)
    benchmark_return = None
    benchmark_refs: dict[str, Any] = {}
    if not intraday and reference_index and target_index and reference_index.index_value:
        benchmark_return = target_index.index_value / reference_index.index_value - 1.0
        benchmark_refs = {"reference_index_id": reference_index.id, "target_index_id": target_index.id}
    excess_return = raw_return - benchmark_return if benchmark_return is not None else None
    directional_excess = None
    if excess_return is not None and action in OUTCOME_ACTIONS_LONG | OUTCOME_ACTIONS_SHORT:
        directional_excess = excess_return if action in OUTCOME_ACTIONS_LONG else -excess_return
    reason_codes: list[str] = []
    if intraday:
        reason_codes.extend(["PARTIAL_INTRADAY_PATH", "INTRADAY_BENCHMARK_UNAVAILABLE"])
    elif benchmark_return is None:
        reason_codes.append("BENCHMARK_UNAVAILABLE")
    bar_quality = "DEGRADED" if any(str(row.quality_status).upper() != "VALID" for row in bars) else "VALID"
    decision_quality = str(memory.quality_status or "VALID").upper()
    quality_status = "VALID" if not reason_codes and benchmark_return is not None and bar_quality == "VALID" and decision_quality == "VALID" else "DEGRADED"
    confidence = 0.95 if quality_status == "VALID" else 0.70
    return {
        "status": quality_status,
        "quality_status": quality_status,
        "confidence": confidence,
        "target_trade_date": target_date,
        "end_price": end_price,
        "raw_return": raw_return,
        "benchmark_return": benchmark_return,
        "excess_return": excess_return,
        "mfe": mfe,
        "mae": mae,
        "directional_mfe": directional["directional_mfe"],
        "directional_mae": directional["directional_mae"],
        "directional_return": directional["directional_return"],
        "directional_excess_return": directional_excess,
        "actual_execution_price": actual_price,
        "actual_executed_qty": execution["executed_qty"],
        "actual_execution_return": actual_return,
        "net_execution_return": net_return,
        "execution_fees": execution["fees"],
        "execution_taxes": execution["taxes"],
        "available_at": max(
            target_close,
            *[_utc_naive(row.available_at) for row in bars if row.available_at is not None],
            *[_utc_naive(row.available_at) for row in (reference_index, target_index) if row is not None and row.available_at is not None],
        ),
        "computed_at": cutoff,
        "source_refs": {
            "adjustment": adjustment,
            "price_return_basis": adjustment,
            "reference_price_basis": reference_basis or None,
            "price_basis_compatible": True,
            "bar_ids": [row.id for row in bars],
            "path_bar_ids": [row.id for row in path_bars],
            "reference_bar_ids": [row.id for row in bars if row.trade_date == reference_date],
            "target_bar_id": end_bar.id,
            "ledger_entry_ids": [row.id for row in execution["rows"]],
            "benchmark": benchmark_refs,
            "reason_codes": reason_codes,
            "intraday_decision": intraday,
            "path_start_date": path_start_date.isoformat() if intraday else reference_date.isoformat(),
            "execution_window_trading_days": 2,
        },
    }


def ensure_outcome_rows(db: Session, memory: DecisionMemory) -> list[DecisionOutcome]:
    targets = [
        item for item in [*(memory.holding_decisions_json or []), *(memory.candidate_decisions_json or [])]
        if isinstance(item, dict)
    ]
    if not targets:
        targets = [{
            "target_type": "PORTFOLIO",
            "target_key": "PORTFOLIO",
            "recommended_action": memory.portfolio_action or "no_action",
        }]
    result: list[DecisionOutcome] = []
    reference_date = memory.decision_at.replace(tzinfo=UTC).astimezone(CHINA_TZ).date()
    for target in targets:
        target_type = str(target.get("target_type") or "PORTFOLIO")
        target_key = str(target.get("target_key") or "PORTFOLIO")
        for horizon in OUTCOME_HORIZONS:
            row = db.execute(select(DecisionOutcome).where(
                DecisionOutcome.decision_memory_id == memory.id,
                DecisionOutcome.target_type == target_type,
                DecisionOutcome.target_key == target_key,
                DecisionOutcome.horizon_trading_days == horizon,
                DecisionOutcome.calculation_version == OUTCOME_VERSION,
            )).scalar_one_or_none()
            if row is None:
                row = DecisionOutcome(
                    decision_memory_id=memory.id,
                    target_type=target_type,
                    target_key=target_key,
                    recommended_action=canonical_action(target.get("recommended_action"), default="no_action"),
                    horizon_trading_days=horizon,
                    recommended_qty=_number(target.get("recommended_qty")),
                    recommended_weight=_number(target.get("recommended_weight")),
                    target_weight=_number(target.get("target_weight")),
                    reference_trade_date=reference_date,
                    reference_at=memory.decision_at,
                    reference_price=_number(target.get("reference_price")),
                    reference_price_basis=target.get("reference_price_basis"),
                    status="PENDING" if target_type != "PORTFOLIO" else "NOT_APPLICABLE",
                    quality_status="PENDING" if target_type != "PORTFOLIO" else "DEGRADED",
                    confidence=0.0,
                    calculation_version=OUTCOME_VERSION,
                )
                db.add(row)
            result.append(row)
    db.flush()
    return result


def _apply_calculation(row: DecisionOutcome, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key == "source_refs":
            setattr(row, "source_refs_json", value)
        elif hasattr(row, key):
            setattr(row, key, value)


def refresh_due_decision_outcomes(
    db: Session,
    *,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    calculation_as_of: datetime | None = None,
    persist: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    cutoff = _utc_naive(calculation_as_of) or _now()
    query = select(DecisionMemory)
    if user_id is not None:
        query = query.where(DecisionMemory.user_id == user_id)
    if portfolio_id is not None:
        query = query.where(DecisionMemory.portfolio_id == portfolio_id)
    memories = db.execute(query.order_by(DecisionMemory.decision_at.asc(), DecisionMemory.id.asc())).scalars().all()
    completed = pending = missing = blocked = 0
    touched: list[DecisionOutcome] = []
    for memory in memories:
        rows = ensure_outcome_rows(db, memory)
        for row in rows:
            if not force and row.status in {"VALID", "DEGRADED", "NOT_APPLICABLE"}:
                continue
            if not force and row.available_at is not None and cutoff < _utc_naive(row.available_at):
                pending += 1
                continue
            before_status = row.status
            values = calculate_decision_outcome(db, memory, row, calculation_as_of=cutoff)
            if before_status in {"VALID", "DEGRADED"}:
                row.recalculation_count = int(row.recalculation_count or 0) + 1
            _apply_calculation(row, values)
            touched.append(row)
            if row.status in {"VALID", "DEGRADED"}:
                completed += 1
            elif row.status == "PENDING":
                pending += 1
            elif row.status in {"WAITING_DATA", "UNAVAILABLE"}:
                missing += 1
            else:
                blocked += 1
    if persist:
        db.commit()
    return {
        "status": "completed",
        "memories_considered": len(memories),
        "outcomes_touched": len(touched),
        "completed": completed,
        "pending": pending,
        "missing": missing,
        "blocked": blocked,
        "calculation_as_of": cutoff,
    }


def invalidate_execution_dependent_outcomes(
    db: Session,
    *,
    memory_id: int | None = None,
    ledger_entry: TradeLedgerEntry | None = None,
    calculation_as_of: datetime | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    """Recompute mature execution facts after a ledger mutation or link."""

    cutoff = _utc_naive(calculation_as_of) or _now()
    query = select(DecisionMemory).where()
    if memory_id is not None:
        query = query.where(DecisionMemory.id == memory_id)
    elif ledger_entry is not None and ledger_entry.analysis_run_id is not None:
        query = query.where(
            DecisionMemory.analysis_run_id == ledger_entry.analysis_run_id,
            DecisionMemory.user_id == ledger_entry.user_id,
            DecisionMemory.portfolio_id == ledger_entry.portfolio_id,
        )
    else:
        if persist:
            db.commit()
        return {"status": "skipped", "memories_considered": 0, "outcomes_invalidated": 0}

    memories = db.execute(query).scalars().all()
    if not memories:
        if persist:
            db.commit()
        return {"status": "skipped", "memories_considered": 0, "outcomes_invalidated": 0}

    from .execution import refresh_execution_alignments

    for memory in memories:
        refresh_execution_alignments(
            db,
            user_id=memory.user_id,
            portfolio_id=memory.portfolio_id,
            decision_memory_id=memory.id,
            calculation_as_of=cutoff,
            persist=False,
        )

    outcomes = db.execute(select(DecisionOutcome).where(
        DecisionOutcome.decision_memory_id.in_([memory.id for memory in memories]),
    )).scalars().all()
    invalidated = 0
    for outcome in outcomes:
        if (
            outcome.status in {"VALID", "DEGRADED"}
            and outcome.available_at is not None
            and _utc_naive(outcome.available_at) <= cutoff
        ):
            outcome.recalculation_count = int(outcome.recalculation_count or 0) + 1
            outcome.last_source_change_at = cutoff
            invalidated += 1
    if persist:
        db.commit()
    return {
        "status": "completed",
        "memories_considered": len(memories),
        "outcomes_invalidated": invalidated,
        "calculation_as_of": cutoff,
    }


__all__ = [
    "calculate_decision_outcome",
    "completed_session_dates",
    "ensure_outcome_rows",
    "execution_dependent_values",
    "horizon_trade_date",
    "invalidate_execution_dependent_outcomes",
    "refresh_due_decision_outcomes",
]
