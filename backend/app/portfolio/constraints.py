"""Deterministic position and portfolio constraints.

These helpers deliberately describe maximum allowed risk.  They do not create
buy/sell recommendations or infer an optimal portfolio size.
"""
from __future__ import annotations

from typing import Any

from .config import SECTOR_THEME_ETF_HARD_CAP_RATIO, STOCK_HARD_CAP_RATIO


def hard_cap_for_security(security_type: str | None, etf_category: str | None) -> tuple[float | None, list[str]]:
    kind = str(security_type or "").upper()
    category = str(etf_category or "").upper()
    if kind == "STOCK":
        return STOCK_HARD_CAP_RATIO, []
    if kind == "ETF":
        if category in {"SECTOR_ETF", "THEME_ETF"}:
            return SECTOR_THEME_ETF_HARD_CAP_RATIO, []
        if not category or category == "UNKNOWN":
            return None, ["ETF_CATEGORY_UNKNOWN"]
        # Broad and other classified ETFs intentionally have no invented cap.
        return None, []
    return None, ["SECURITY_CLASSIFICATION_UNKNOWN"]


def build_portfolio_constraints(state: dict[str, Any], market_state: dict[str, Any] | None) -> dict[str, Any]:
    """Build server-owned maximum-action constraints for current positions."""

    market_state = market_state or {}
    frozen = bool(market_state.get("is_frozen"))
    market_quality = str(market_state.get("quality_status") or "MISSING").upper()
    market_available = bool(market_state.get("available", market_quality in {"VALID", "DEGRADED"}))
    market_unavailable = not market_available or market_quality in {"MISSING", "INVALID"}
    cash_ratio = state.get("cash_ratio")
    spendable_cash = state.get("spendable_cash", state.get("cash"))
    cash_known = cash_ratio is not None and spendable_cash is not None
    base_quality = str(state.get("quality_status") or "DEGRADED").upper()
    data_quality_blocked = base_quality in {"BLOCKED", "FROZEN"}
    positions: list[dict[str, Any]] = []
    risk_flags: list[str] = list(state.get("risk_flags") or [])

    for source in state.get("positions") or []:
        row = dict(source)
        flags = list(row.get("flags") or [])
        cap, cap_flags = hard_cap_for_security(row.get("security_type"), row.get("etf_category"))
        flags.extend(flag for flag in cap_flags if flag not in flags)
        weight = row.get("weight")
        headroom = None if cap is None or weight is None else max(0.0, cap - float(weight))
        if cap is not None and weight is not None and float(weight) > cap + 1e-9:
            flags.append("HARD_CAP_BREACH")
            risk_flags.append(f"HARD_CAP_BREACH:{row.get('code')}")
        quote_quality = str(row.get("quote_quality") or "MISSING").upper()
        blocking_reasons: list[str] = []
        if frozen:
            blocking_reasons.append("MARKET_STATE_FROZEN")
        if market_unavailable:
            blocking_reasons.append("MARKET_STATE_UNAVAILABLE")
        if quote_quality not in {"VALID", "DEGRADED"}:
            blocking_reasons.append(f"QUOTE_{quote_quality}")
        if "ETF_CATEGORY_UNKNOWN" in flags or "SECURITY_CLASSIFICATION_UNKNOWN" in flags:
            blocking_reasons.extend(flag for flag in flags if flag in {"ETF_CATEGORY_UNKNOWN", "SECURITY_CLASSIFICATION_UNKNOWN"})
        if not cash_known:
            blocking_reasons.append("INSUFFICIENT_CASH_DATA")
        if headroom is not None and headroom <= 0:
            blocking_reasons.append("STOCK_HARD_CAP" if str(row.get("security_type") or "").upper() == "STOCK" else "HARD_CAP_REACHED")
        max_additional_weight = None
        if cap is None and not cap_flags and cash_ratio is not None:
            # A classified ETF without a V1 hard cap is limited by available
            # cash, not silently converted into an impossible-to-add position.
            max_additional_weight = max(0.0, float(cash_ratio))
        elif headroom is not None and cash_ratio is not None:
            max_additional_weight = max(0.0, min(headroom, float(cash_ratio)))
        elif headroom is not None:
            max_additional_weight = headroom
        available_qty = row.get("available_qty")
        can_reduce = available_qty is not None and float(available_qty) > 0 and quote_quality in {"VALID", "DEGRADED"}
        if available_qty is None:
            flags.append("AVAILABLE_QTY_UNKNOWN")
        row.update({
            "hard_cap": cap,
            "hard_cap_headroom": headroom,
            "max_additional_weight": max_additional_weight,
            "max_sellable_qty": available_qty,
            "add_allowed": not blocking_reasons,
            "reduce_allowed": can_reduce,
            "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
            "flags": list(dict.fromkeys(flags)),
        })
        positions.append(row)

    return {
        "can_increase_risk": not frozen and not market_unavailable and cash_known and not data_quality_blocked,
        "can_reduce_risk": not data_quality_blocked,
        "data_quality_blocked": data_quality_blocked,
        "market_state_frozen": frozen,
        "market_state_available": market_available,
        "cash_known": cash_known,
        "spendable_cash": spendable_cash,
        "cash_ratio": cash_ratio,
        "reserve_ratio": state.get("reserve_ratio"),
        "market_regime": market_state.get("regime"),
        "market_quality_status": market_state.get("quality_status"),
        "positions": positions,
        "risk_flags": list(dict.fromkeys(risk_flags)),
    }


__all__ = ["build_portfolio_constraints", "hard_cap_for_security"]
