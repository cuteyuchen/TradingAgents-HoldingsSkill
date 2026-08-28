"""Forward outcome calculations for offline research cases."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..market_engine_models import AllAMedianIndexDaily, DailyBarCache
from ..market_models import TradingCalendar
from ..portfolio import ledger as portfolio_ledger
from .config import TransactionCostModel, current_transaction_cost_model

CHINA_TZ = ZoneInfo("Asia/Shanghai")
VALID_QUALITY = {"VALID", "DEGRADED"}
LONG_ACTIONS = {"BUY", "ADD", "NEW_POSITION", "CONDITIONAL_ADD", "HOLD"}
SHORT_ACTIONS = {"SELL", "REDUCE", "EXIT"}
QFQ_BASES = {"QFQ", "ADJUSTED_QFQ", "QFQ_CLOSE", "DAILY_BAR_QFQ"}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _as_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(CHINA_TZ).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _get(row: Any, key: str, default=None):
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def price_basis_compatible(reference_basis: str | None, adjustment: str | None) -> bool:
    basis = str(reference_basis or "").strip().upper().replace("-", "_").replace(" ", "_")
    target = str(adjustment or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not basis or not target:
        return False
    if target == "QFQ":
        return basis in QFQ_BASES or basis.endswith("_QFQ")
    return basis == target


def trading_dates_after(db: Session, day: date, horizon: int, *, market: str = "CN") -> list[date]:
    if horizon <= 0:
        raise ValueError("horizon_must_be_positive")
    return list(db.execute(select(TradingCalendar.trade_date).where(
        TradingCalendar.market == market,
        TradingCalendar.trade_date > day,
        TradingCalendar.is_open.is_(True),
    ).order_by(TradingCalendar.trade_date.asc()).limit(horizon)).scalars())


def estimate_transaction_cost(
    *,
    price: float,
    quantity: float = 1.0,
    action: str = "BUY",
    model: TransactionCostModel | None = None,
) -> dict[str, Any]:
    """Use the same commission/tax calculation as the Phase E ledger.

    There is no authoritative historical slippage series in the repository,
    so slippage is reported as unmodeled instead of being fabricated.
    """

    model = model or current_transaction_cost_model()
    price = max(0.0, float(price))
    quantity = max(0.0, float(quantity))
    notional = price * quantity
    is_sell = str(action or "BUY").upper() in SHORT_ACTIONS
    total = portfolio_ledger.transaction_cost_estimate(
        side="SELL" if is_sell else "BUY",
        gross_amount=notional,
        commission_bps=model.commission_bps,
        minimum_commission=model.minimum_commission,
        sell_tax_bps=model.sell_tax_bps,
    )
    fees = None
    if model.commission_bps is not None and model.minimum_commission is not None:
        fees = max(notional * model.commission_bps / 10_000.0, model.minimum_commission)
    taxes = notional * model.sell_tax_bps / 10_000.0 if is_sell and model.sell_tax_bps is not None else 0.0
    slippage = None
    return {
        "notional": notional,
        "fees": fees,
        "taxes": taxes,
        "slippage": slippage,
        "total_cost": total,
        "cost_rate": total / notional if total is not None and notional else None,
        "slippage_not_modeled": model.slippage_not_modeled,
        "model_version": model.model_version,
    }


def _locked_limit(bar: Any, *, side: str) -> bool:
    metadata = _get(bar, "metadata_json", _get(bar, "metadata", {})) or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    explicit = metadata.get("locked_limit_up" if side == "BUY" else "locked_limit_down")
    if explicit is not None:
        return bool(explicit)
    if bool(metadata.get("is_suspended")):
        return False
    open_price = _number(_get(bar, "open"))
    high = _number(_get(bar, "high"))
    low = _number(_get(bar, "low"))
    close = _number(_get(bar, "close"))
    previous = _number(_get(bar, "prev_close"))
    if None in (open_price, high, low, close, previous) or previous <= 0:
        return False
    locked = abs(open_price - high) < 1e-9 and abs(high - close) < 1e-9 if side == "BUY" else abs(open_price - low) < 1e-9 and abs(low - close) < 1e-9
    return bool(locked and abs((close / previous) - 1.0) >= 0.095)


def _suspended(bar: Any) -> bool:
    metadata = _get(bar, "metadata_json", _get(bar, "metadata", {})) or {}
    if isinstance(metadata, Mapping) and bool(metadata.get("is_suspended")):
        return True
    return _get(bar, "quality_status") in {"MISSING", "INVALID"} or (
        _number(_get(bar, "open")) is None and _number(_get(bar, "close")) is None
    )


def _bar_dict(row: Any) -> dict[str, Any]:
    return {
        "id": _get(row, "id"),
        "trade_date": _as_date(_get(row, "trade_date")),
        "open": _number(_get(row, "open")),
        "high": _number(_get(row, "high")),
        "low": _number(_get(row, "low")),
        "close": _number(_get(row, "close")),
        "prev_close": _number(_get(row, "prev_close")),
        "adjustment": _get(row, "adjustment"),
        "available_at": _naive_utc(_get(row, "available_at")),
        "quality_status": str(_get(row, "quality_status") or "MISSING").upper(),
        "metadata_json": _get(row, "metadata_json", {}) or {},
    }


def calculate_forward_outcome(
    *,
    decision_date: date,
    horizon: int,
    reference_price: float | None,
    reference_price_basis: str | None,
    bars: Iterable[Any],
    benchmark_rows: Iterable[Any] = (),
    action: str = "BUY",
    execution_basis: str = "EOD_CLOSE",
    target_dates: Iterable[date] | None = None,
    as_of: datetime | None = None,
    transaction_cost_model: TransactionCostModel | None = None,
    intraday: bool = False,
) -> dict[str, Any]:
    """Calculate one horizon while keeping incomplete evidence explicitly degraded."""

    if horizon <= 0:
        raise ValueError("horizon_must_be_positive")
    cutoff = _naive_utc(as_of)
    action_upper = str(action or "BUY").upper()
    direction = -1.0 if action_upper in SHORT_ACTIONS else 1.0 if action_upper in LONG_ACTIONS else None
    materialised = sorted((_bar_dict(row) for row in bars), key=lambda row: (row["trade_date"] or date.min, row["id"] or 0))
    if intraday:
        # An intraday decision can use the same session's close for H1.  The
        # daily high/low from that session remains excluded from MFE/MAE below.
        materialised = [row for row in materialised if row["trade_date"] is not None and row["trade_date"] >= decision_date]
    else:
        materialised = [row for row in materialised if row["trade_date"] is not None and row["trade_date"] > decision_date]
    if cutoff is not None:
        materialised = [row for row in materialised if row["available_at"] is None or row["available_at"] <= cutoff]
    if not materialised:
        return _blocked("FORWARD_BARS_MISSING", horizon=horizon, execution_basis=execution_basis)
    if reference_price is None or reference_price <= 0:
        return _blocked("REFERENCE_PRICE_MISSING", horizon=horizon, execution_basis=execution_basis)
    adjustment = str(materialised[0].get("adjustment") or "").upper()
    if not price_basis_compatible(reference_price_basis, adjustment):
        return _blocked("PRICE_BASIS_MISMATCH", horizon=horizon, execution_basis=execution_basis, status="DEGRADED")
    target_dates_list = list(target_dates or [])
    if intraday and target_dates_list and target_dates_list[0] != decision_date:
        target_dates_list.insert(0, decision_date)
    if len(target_dates_list) < horizon:
        return _blocked("TRADING_CALENDAR_RANGE_MISSING", horizon=horizon, execution_basis=execution_basis)
    target_date = target_dates_list[horizon - 1]
    path = [row for row in materialised if row["trade_date"] <= target_date]
    target_bar = next((row for row in reversed(path) if row["trade_date"] == target_date and row["close"] is not None), None)
    if target_bar is None:
        return _blocked("TARGET_CLOSE_MISSING", horizon=horizon, execution_basis=execution_basis)

    # Corporate-action basis must remain compatible for every bar used in the
    # path.  Checking only the first future bar would silently mix RAW/QFQ data
    # when a cache was repaired or backfilled mid-window.
    if any(not price_basis_compatible(reference_price_basis, row.get("adjustment")) for row in path):
        return _blocked("PRICE_BASIS_MISMATCH", horizon=horizon, execution_basis=execution_basis, status="DEGRADED")

    first_bar = path[0]
    if execution_basis == "NEXT_OPEN_PROXY" and not intraday:
        side = "SELL" if action_upper in SHORT_ACTIONS else "BUY"
        if _suspended(first_bar):
            return _blocked("SUSPENDED_NON_EXECUTABLE", horizon=horizon, execution_basis=execution_basis, status="DEGRADED", extra={"execution_status": "NON_EXECUTABLE"})
        if _locked_limit(first_bar, side=side):
            return _blocked("LOCKED_LIMIT_NON_EXECUTABLE", horizon=horizon, execution_basis=execution_basis, status="DEGRADED", extra={"execution_status": "NON_EXECUTABLE"})
        execution_price = first_bar["open"]
        if execution_price is None or execution_price <= 0:
            return _blocked("NEXT_OPEN_MISSING", horizon=horizon, execution_basis=execution_basis, status="DEGRADED")
    else:
        execution_price = reference_price

    benchmark = {
        _as_date(_get(row, "trade_date")): _number(_get(row, "index_value"))
        for row in benchmark_rows
        if _as_date(_get(row, "trade_date")) is not None and (
            cutoff is None or _naive_utc(_get(row, "available_at")) is None or _naive_utc(_get(row, "available_at")) <= cutoff
        )
    }
    benchmark_return = None
    benchmark_start_date = decision_date
    benchmark_end_date = target_date
    if not intraday and benchmark.get(benchmark_start_date) and benchmark.get(benchmark_end_date):
        benchmark_return = benchmark[benchmark_end_date] / benchmark[benchmark_start_date] - 1.0
    reason_codes: list[str] = []
    if intraday:
        reason_codes.extend(["PARTIAL_INTRADAY_PATH", "INTRADAY_BENCHMARK_UNAVAILABLE"])
    elif benchmark_return is None:
        reason_codes.append("BENCHMARK_UNAVAILABLE")

    path_for_extremes = path
    highs = [row["high"] for row in path_for_extremes if row["high"] is not None and row["high"] > 0]
    lows = [row["low"] for row in path_for_extremes if row["low"] is not None and row["low"] > 0]
    # A same-day intraday decision must not use the morning's daily high/low.
    if intraday:
        path_for_extremes = [row for row in path_for_extremes if row["trade_date"] > decision_date]
        highs = [row["high"] for row in path_for_extremes if row["high"] is not None and row["high"] > 0]
        lows = [row["low"] for row in path_for_extremes if row["low"] is not None and row["low"] > 0]
    raw_return = target_bar["close"] / execution_price - 1.0
    mfe = max((high / execution_price - 1.0 for high in highs), default=None)
    mae = min((low / execution_price - 1.0 for low in lows), default=None)
    if direction is None:
        directional_return = directional_mfe = directional_mae = None
    elif direction > 0:
        directional_return, directional_mfe, directional_mae = raw_return, mfe, mae
    else:
        directional_return = -raw_return
        directional_mfe = -mae if mae is not None else None
        directional_mae = -mfe if mfe is not None else None
    excess = raw_return - benchmark_return if benchmark_return is not None else None
    directional_excess = excess * direction if excess is not None and direction is not None else None
    cost = estimate_transaction_cost(price=execution_price, action=action_upper, model=transaction_cost_model)
    net_return = raw_return - cost["cost_rate"] if cost["cost_rate"] is not None else None
    quality = "VALID" if not reason_codes and str(first_bar["quality_status"]).upper() == "VALID" else "DEGRADED"
    return {
        "status": quality,
        "quality_status": quality,
        "confidence": 0.95 if quality == "VALID" else 0.70,
        "horizon": horizon,
        "target_trade_date": target_date,
        "reference_price": reference_price,
        "reference_price_basis": reference_price_basis,
        "execution_price": execution_price,
        "execution_basis": execution_basis,
        "end_price": target_bar["close"],
        "raw_return": raw_return,
        "benchmark_return": benchmark_return,
        "excess_return": excess,
        "mfe": mfe,
        "mae": mae,
        "directional_return": directional_return,
        "directional_mfe": directional_mfe,
        "directional_mae": directional_mae,
        "directional_excess_return": directional_excess,
        "transaction_cost_estimate": cost["total_cost"],
        "transaction_cost_rate": cost["cost_rate"],
        "net_return_estimate": net_return,
        "reason_codes": reason_codes,
        "path_bar_ids": [row["id"] for row in path_for_extremes if row["id"] is not None],
        "target_bar_id": target_bar["id"],
        "benchmark_basis": "ALL_A_MEDIAN_INDEX_DAILY" if benchmark_return is not None else None,
        "source_refs": {
            "adjustment": adjustment,
            "target_trade_date": target_date.isoformat(),
            "execution_basis": execution_basis,
        },
    }


def _blocked(reason: str, *, horizon: int, execution_basis: str, status: str = "BLOCKED", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "quality_status": status,
        "confidence": 0.0,
        "horizon": horizon,
        "execution_basis": execution_basis,
        "raw_return": None,
        "benchmark_return": None,
        "excess_return": None,
        "mfe": None,
        "mae": None,
        "directional_return": None,
        "directional_excess_return": None,
        "transaction_cost_estimate": None,
        "reason_codes": [reason],
    }
    if extra:
        result.update(extra)
    return result


def calculate_market_forward_outcome(
    *,
    as_of_date: date,
    horizon: int,
    benchmark_rows: Iterable[Any],
    as_of: datetime | None = None,
) -> dict[str, Any]:
    source_rows = list(benchmark_rows)
    rows = sorted((_bar_dict(row) if isinstance(row, DailyBarCache) else {
        "trade_date": _as_date(_get(row, "trade_date")),
        "index_value": _number(_get(row, "index_value")),
        "available_at": _naive_utc(_get(row, "available_at")),
    } for row in source_rows), key=lambda item: item["trade_date"] or date.min)
    cutoff = _naive_utc(as_of)
    rows = [row for row in rows if row["trade_date"] and row["trade_date"] > as_of_date and (cutoff is None or row.get("available_at") is None or row["available_at"] <= cutoff)]
    if len(rows) < horizon:
        return _blocked("BENCHMARK_HORIZON_MISSING", horizon=horizon, execution_basis="INDEX_CLOSE")
    all_rows = [row for row in source_rows if _as_date(_get(row, "trade_date")) is not None and (
        cutoff is None or _naive_utc(_get(row, "available_at")) is None or _naive_utc(_get(row, "available_at")) <= cutoff
    )]
    start = next((row for row in all_rows if _as_date(_get(row, "trade_date")) == as_of_date), None)
    if start is None or _number(_get(start, "index_value")) is None:
        return _blocked("BENCHMARK_START_MISSING", horizon=horizon, execution_basis="INDEX_CLOSE")
    start_value = _number(_get(start, "index_value"))
    end = rows[horizon - 1]
    end_value = _number(end.get("index_value"))
    if end_value is None or start_value is None or start_value <= 0:
        return _blocked("BENCHMARK_VALUE_MISSING", horizon=horizon, execution_basis="INDEX_CLOSE")
    path_values = [row.get("index_value") for row in rows[:horizon] if row.get("index_value") is not None]
    peak = start_value
    max_drawdown = 0.0
    for value in path_values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1.0 if peak else 0.0)
    forward = end_value / start_value - 1.0
    return {
        "status": "VALID",
        "quality_status": "VALID",
        "horizon": horizon,
        "target_trade_date": _as_date(end.get("trade_date")),
        "forward_return": forward,
        "median_forward_return": forward,
        "max_drawdown": max_drawdown,
        "source_refs": {"benchmark_basis": "ALL_A_MEDIAN_INDEX_DAILY"},
    }


__all__ = [
    "price_basis_compatible",
    "trading_dates_after",
    "estimate_transaction_cost",
    "calculate_forward_outcome",
    "calculate_market_forward_outcome",
]
