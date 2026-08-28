"""PIT input and engine capability manifest for deterministic recompute."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from sqlalchemy.orm import Session

from ..history.availability import history_manifest_items, pit_recompute_gate
from .config import (
    CANDIDATE_REQUIRED_INPUTS,
    CANDIDATE_ETF_REQUIRED_INPUTS,
    CANDIDATE_STOCK_REQUIRED_INPUTS,
    INTRADAY_SUPPORTED,
    MARKET_REQUIRED_INPUTS,
    PORTFOLIO_DECISION_REQUIRED_INPUTS,
    RECOMPUTE_ENGINE_VERSION,
    UNIVERSE_VERSION,
    KNOWN_MISSING_FACTORS,
    RecomputeCapability,
    RecomputeScope,
)


def _required_inputs(scope: str) -> tuple[str, ...]:
    return {
        RecomputeScope.MARKET: MARKET_REQUIRED_INPUTS,
        RecomputeScope.CANDIDATE: CANDIDATE_REQUIRED_INPUTS,
        RecomputeScope.CANDIDATE_STOCK: CANDIDATE_STOCK_REQUIRED_INPUTS,
        RecomputeScope.CANDIDATE_ETF: CANDIDATE_ETF_REQUIRED_INPUTS,
        RecomputeScope.PORTFOLIO_DECISION: PORTFOLIO_DECISION_REQUIRED_INPUTS,
    }.get(str(scope).upper(), MARKET_REQUIRED_INPUTS)


def build_recompute_capability_manifest(
    db: Session,
    *,
    scope: str,
    start_date: date,
    end_date: date,
    market: str = "CN",
    checkpoint: str = "EOD",
    parameter_version: str | None = None,
    config_hash: str | None = None,
    universe_version: str = UNIVERSE_VERSION,
) -> dict[str, Any]:
    """Return one honest capability manifest per Phase M scope."""

    normalized_scope = str(scope).upper()
    gate = pit_recompute_gate(
        db,
        scope=normalized_scope,
        start_date=start_date,
        end_date=end_date,
        market=market,
    )
    items = history_manifest_items(
        db,
        start_date=start_date,
        end_date=end_date,
        market=market,
    ) or {}
    if "daily_bars" in _required_inputs(normalized_scope) and "daily_bars" not in items:
        items["daily_bars"] = _daily_bar_manifest_item(db, start_date=start_date, end_date=end_date, market=market)
    price_basis = items.get("price_basis") or {}
    if (
        "price_basis" in _required_inputs(normalized_scope)
        and str(price_basis.get("status") or "DATA_GAP") == "DATA_GAP"
        and "expected_security_universe_unknown" in str(price_basis.get("reason") or "")
    ):
        items["price_basis"] = _price_basis_manifest_item(db, start_date=start_date, end_date=end_date, market=market)
    required = _required_inputs(normalized_scope)

    available_inputs: list[str] = []
    partial_inputs: list[str] = []
    missing_inputs: list[str] = []
    coverage_values: list[float | None] = []
    for key in required:
        item = items.get(key) or {}
        status = str(item.get("status") or "DATA_GAP")
        if status == "FULL":
            available_inputs.append(key)
        elif status in {"PARTIAL", "DIAGNOSTIC_ONLY"}:
            partial_inputs.append(key)
        else:
            missing_inputs.append(key)
        coverage_values.append(item.get("coverage") if item.get("coverage") is not None else None)
    available_inputs.extend(key for key in gate.get("partial_inputs", []) if key not in available_inputs)
    missing_inputs.extend(key for key in gate.get("missing_inputs", []) if key not in missing_inputs)
    partial_inputs.extend(key for key in gate.get("partial_inputs", []) if key not in partial_inputs)
    available_inputs = sorted(set(available_inputs))
    partial_inputs = sorted(set(partial_inputs))
    missing_inputs = sorted(set(missing_inputs))

    if gate["status"] in {"DATA_GAP", "LEAKAGE_BLOCKED"} or missing_inputs:
        capability = (
            RecomputeCapability.LEAKAGE_BLOCKED
            if gate["status"] == "LEAKAGE_BLOCKED"
            else RecomputeCapability.DATA_GAP
        )
    elif str(checkpoint).upper() != "EOD" and not INTRADAY_SUPPORTED:
        capability = RecomputeCapability.UNSUPPORTED
    else:
        capability = _engine_capability(normalized_scope, items, partial_inputs)

    limitations: list[str] = []
    if normalized_scope == RecomputeScope.MARKET:
        limitations.append("historical industry/diffusion input is not persisted; market is PARTIAL unless industry history is complete")
    if normalized_scope in {RecomputeScope.CANDIDATE, RecomputeScope.CANDIDATE_STOCK, RecomputeScope.CANDIDATE_ETF}:
        limitations.extend(
            f"historical {factor} input is not persisted"
            for factor in KNOWN_MISSING_FACTORS.get(normalized_scope, ())
        )
    if normalized_scope in {RecomputeScope.CANDIDATE, RecomputeScope.CANDIDATE_ETF}:
        limitations.append("ETF constituent breadth/lookthrough history is unsupported")
    if str(checkpoint).upper() != "EOD":
        limitations.append("only EOD/15:10 deep recompute is supported; intraday PIT is unsupported")

    return {
        "manifest_version": "recompute-capability-v1",
        "scope": normalized_scope,
        "requested_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "checkpoint": str(checkpoint).upper(),
        "capability": str(capability),
        "required_inputs": list(required),
        "available_inputs": available_inputs,
        "partial_inputs": partial_inputs,
        "missing_inputs": missing_inputs,
        "coverage": {
            key: coverage_values[index]
            for index, key in enumerate(required)
            if coverage_values[index] is not None
        },
        "parameter_version": parameter_version,
        "config_hash": config_hash,
        "universe_version": universe_version,
        "price_basis": "QFQ",
        "engine_version": RECOMPUTE_ENGINE_VERSION,
        "limitations": limitations,
    }


def _engine_capability(scope: str, items: Mapping[str, Any], partial_inputs: list[str]) -> RecomputeCapability:
    if partial_inputs:
        return RecomputeCapability.PARTIAL_PIT_RECOMPUTE
    if scope == RecomputeScope.MARKET:
        industry = items.get("industry") or {}
        if str(industry.get("status") or "UNSUPPORTED") != "FULL":
            return RecomputeCapability.PARTIAL_PIT_RECOMPUTE
        return RecomputeCapability.FULL_PIT_EQUIVALENT
    if scope in {RecomputeScope.CANDIDATE, RecomputeScope.CANDIDATE_STOCK}:
        return RecomputeCapability.PARTIAL_PIT_RECOMPUTE
    if scope == RecomputeScope.CANDIDATE_ETF:
        return RecomputeCapability.PARTIAL_PIT_RECOMPUTE
    if scope == RecomputeScope.PORTFOLIO_DECISION:
        return RecomputeCapability.PARTIAL_PIT_RECOMPUTE
    return RecomputeCapability.DIAGNOSTIC_ONLY


def _daily_bar_manifest_item(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    market: str,
) -> dict[str, Any]:
    """Count persisted QFQ bars for the requested range when Phase L has no entry."""

    from sqlalchemy import func, select

    from ..market_engine_models import DailyBarCache

    try:
        row_count = int(db.execute(
            select(func.count()).select_from(DailyBarCache).where(
                DailyBarCache.market == market,
                DailyBarCache.trade_date >= start_date,
                DailyBarCache.trade_date <= end_date,
                DailyBarCache.adjustment == "QFQ",
            )
        ).scalar_one() or 0)
        distinct_dates = int(db.execute(
            select(func.count(func.distinct(DailyBarCache.trade_date))).select_from(DailyBarCache).where(
                DailyBarCache.market == market,
                DailyBarCache.trade_date >= start_date,
                DailyBarCache.trade_date <= end_date,
                DailyBarCache.adjustment == "QFQ",
            )
        ).scalar_one() or 0)
    except Exception:  # noqa: BLE001
        row_count = 0
        distinct_dates = 0
    if not row_count or not distinct_dates:
        return {"name": "daily_bars", "status": "DATA_GAP", "row_count": row_count, "distinct_trade_dates": distinct_dates, "coverage": None, "reason": "no_qfq_daily_bars_in_requested_range"}
    return {
        "name": "daily_bars",
        "status": "PARTIAL",
        "row_count": row_count,
        "distinct_trade_dates": distinct_dates,
        "coverage": 1.0,
        "reason": "daily_bars_are_present_but_are_eod_close_proxies_for_historical_recompute",
        "capabilities": {"DETERMINISTIC_RECOMPUTE": "PARTIAL"},
    }


def _price_basis_manifest_item(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    market: str,
) -> dict[str, Any]:
    """Count PIT price-basis rows when Phase L could not prove a denominator."""

    from sqlalchemy import func, select

    from ..history.models import PriceBasisMetadata

    try:
        row_count = int(db.execute(
            select(func.count()).select_from(PriceBasisMetadata).where(
                PriceBasisMetadata.market == market,
                PriceBasisMetadata.trade_date >= start_date,
                PriceBasisMetadata.trade_date <= end_date,
            )
        ).scalar_one() or 0)
        distinct_dates = int(db.execute(
            select(func.count(func.distinct(PriceBasisMetadata.trade_date))).select_from(PriceBasisMetadata).where(
                PriceBasisMetadata.market == market,
                PriceBasisMetadata.trade_date >= start_date,
                PriceBasisMetadata.trade_date <= end_date,
            )
        ).scalar_one() or 0)
    except Exception:  # noqa: BLE001
        row_count = 0
        distinct_dates = 0
    if not row_count:
        return {"name": "price_basis", "status": "DATA_GAP", "row_count": 0, "distinct_trade_dates": 0, "coverage": None, "reason": "no_price_basis_rows_in_requested_range"}
    return {
        "name": "price_basis",
        "status": "PARTIAL",
        "row_count": row_count,
        "distinct_trade_dates": distinct_dates,
        "coverage": 1.0,
        "reason": "price_basis_rows_are_present_without_a_current_security_master_denominator",
        "capabilities": {"DETERMINISTIC_RECOMPUTE": "PARTIAL"},
    }


__all__ = ["build_recompute_capability_manifest"]
