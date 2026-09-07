"""Deterministic post-analysis Portfolio Decision Gate."""
from __future__ import annotations

import math
from typing import Any

from ..decision_contract import DEFAULT_PORTFOLIO_ACTION, has_actionable_portfolio_change
from ..market.engine.metrics import is_price_limit
from .config import PORTFOLIO_GATE_VERSION


def _weight(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        text = str(value).strip()
        number = float(text.rstrip("%"))
    except (TypeError, ValueError):
        return None
    if text.endswith("%") or number > 1:
        number /= 100.0
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def _quantity(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", "").strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _append_reason(row: dict[str, Any], code: str) -> None:
    reasons = list(row.get("portfolio_gate_reasons") or [])
    if code not in reasons:
        reasons.append(code)
    row["portfolio_gate_reasons"] = reasons


def _watch(row: dict[str, Any], reason: str) -> None:
    row["action"] = "watch"
    row["quantity"] = None
    row["proposed_qty"] = None
    row["target_weight"] = None
    row["adjustment_weight"] = None
    row["portfolio_gate"] = "BLOCKED"
    _append_reason(row, reason)


def apply_portfolio_decision_gate(
    result: dict[str, Any],
    *,
    portfolio_context: dict[str, Any],
) -> dict[str, Any]:
    """Constrain normalized LLM actions while preserving requested values for audit."""

    constraints = {row.get("code"): row for row in portfolio_context.get("position_constraints") or [] if row.get("code")}
    market_frozen = bool(portfolio_context.get("market_state_frozen"))
    market_available = bool(portfolio_context.get("market_state_available", True))
    market_quality = str(portfolio_context.get("market_quality_status") or "VALID").upper()
    market_unavailable = not market_available or market_quality in {"MISSING", "INVALID"}
    quality = str(portfolio_context.get("portfolio_quality") or "DEGRADED").upper()
    analysis_quality = result.get("quality_gate") or {}
    analysis_blocked = analysis_quality.get("status") == "blocked" or analysis_quality.get("grade") in {"D", "F"}
    no_risk_increase = analysis_quality.get("risk_increase_allowed") is False or analysis_quality.get("grade") == "C"
    risk_rejected = (result.get("risk_revision") or {}).get("decision") == "reject"
    assets = _quantity(portfolio_context.get("current_estimated_total_assets"))
    remaining_cash = _quantity(portfolio_context.get("spendable_cash"))
    target_exposure = _weight(portfolio_context.get("target_exposure"))
    gross_exposure = _weight(portfolio_context.get("gross_exposure"))
    if assets is not None and assets > 0 and target_exposure is not None and gross_exposure is not None and remaining_cash is not None:
        remaining_cash = min(remaining_cash, max(0.0, target_exposure - gross_exposure) * assets)
    action_results: list[dict[str, Any]] = []
    statuses: list[str] = []
    blocked_reasons: list[str] = []
    adjusted_reasons: list[str] = []
    updated_holdings: list[dict[str, Any]] = []

    for raw in result.get("holdings") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        code = str(row.get("code") or "")
        constraint = constraints.get(code)
        action = str(row.get("action") or "watch").lower()
        requested_action = action
        requested_target = _weight(row.get("target_weight"))
        requested_quantity = _quantity(row.get("quantity") if row.get("proposed_qty") is None else row.get("proposed_qty"))
        row["requested_target_weight"] = requested_target
        row["requested_qty"] = requested_quantity
        if requested_quantity is not None:
            row["quantity"] = str(requested_quantity)
            row["proposed_qty"] = requested_quantity
        if constraint is not None:
            for key in ("current_price", "weight", "hard_cap", "max_additional_weight", "max_sellable_qty"):
                row[{"weight": "current_weight"}.get(key, key)] = constraint.get(key)
            row["hard_cap_headroom"] = constraint.get("hard_cap_headroom")
        if action in {"add", "conditional_add", "reduce", "sell"} and (analysis_blocked or risk_rejected):
            reason = "ANALYSIS_DATA_QUALITY" if analysis_blocked else "RISK_MANAGER_REJECTED"
            _watch(row, reason)
            statuses.append("BLOCKED")
            blocked_reasons.append(reason)
        elif action in {"add", "conditional_add"}:
            reasons = list(constraint.get("blocking_reasons") or []) if constraint else ["POSITION_CONSTRAINT_UNAVAILABLE"]
            if market_frozen and "MARKET_STATE_FROZEN" not in reasons:
                reasons.append("MARKET_STATE_FROZEN")
            if market_unavailable and "MARKET_STATE_UNAVAILABLE" not in reasons:
                reasons.append("MARKET_STATE_UNAVAILABLE")
            if quality in {"BLOCKED", "FROZEN"}:
                reasons.append("PORTFOLIO_DATA_QUALITY")
            if no_risk_increase:
                reasons.append("ANALYSIS_DATA_QUALITY")
            if requested_target is None:
                reasons.append("TARGET_WEIGHT_REQUIRED")
            if reasons:
                _watch(row, reasons[0])
                for reason in reasons[1:]:
                    _append_reason(row, reason)
                statuses.append("BLOCKED")
                blocked_reasons.extend(reasons)
            else:
                current_weight = _weight(constraint.get("weight")) if constraint else None
                maximum_additional = _weight(constraint.get("max_additional_weight")) if constraint else None
                maximum_target = current_weight + maximum_additional if current_weight is not None and maximum_additional is not None else None
                if maximum_target is None:
                    _watch(row, "POSITION_CONSTRAINT_UNAVAILABLE")
                    statuses.append("BLOCKED")
                    blocked_reasons.append("POSITION_CONSTRAINT_UNAVAILABLE")
                elif requested_target > maximum_target + 1e-9:
                    row["target_weight"] = maximum_target
                    row["adjustment_weight"] = max(0.0, maximum_target - current_weight)
                    row["portfolio_gate"] = "ADJUSTED"
                    _append_reason(row, "STOCK_HARD_CAP" if constraint.get("hard_cap") is not None else "MAX_ADDITIONAL_WEIGHT")
                    statuses.append("ADJUSTED")
                    adjusted_reasons.extend(row["portfolio_gate_reasons"])
                else:
                    row["target_weight"] = requested_target
                    row["adjustment_weight"] = max(0.0, requested_target - current_weight)
                    row["portfolio_gate"] = "PASS"
                    statuses.append("PASS")
        elif action in {"reduce", "sell"}:
            if constraint is None:
                _watch(row, "POSITION_CONSTRAINT_UNAVAILABLE")
                statuses.append("BLOCKED")
                blocked_reasons.append("POSITION_CONSTRAINT_UNAVAILABLE")
            else:
                available = _quantity(constraint.get("max_sellable_qty"))
                quote_quality = str(constraint.get("quote_quality") or "MISSING").upper()
                if quote_quality not in {"VALID", "DEGRADED"}:
                    _watch(row, f"QUOTE_{quote_quality}")
                    statuses.append("BLOCKED")
                    blocked_reasons.append(f"QUOTE_{quote_quality}")
                elif quality in {"BLOCKED", "FROZEN"}:
                    _watch(row, "PORTFOLIO_DATA_QUALITY")
                    statuses.append("BLOCKED")
                    blocked_reasons.append("PORTFOLIO_DATA_QUALITY")
                elif available is None or available <= 0:
                    _watch(row, "AVAILABLE_QTY_LIMIT")
                    statuses.append("BLOCKED")
                    blocked_reasons.append("AVAILABLE_QTY_LIMIT")
                elif requested_quantity is None or requested_quantity <= 0:
                    _watch(row, "QUANTITY_REQUIRED")
                    statuses.append("BLOCKED")
                    blocked_reasons.append("QUANTITY_REQUIRED")
                elif requested_quantity is not None and requested_quantity > available:
                    row["quantity"] = str(available)
                    row["proposed_qty"] = available
                    row["portfolio_gate"] = "ADJUSTED"
                    _append_reason(row, "AVAILABLE_QTY_LIMIT")
                    statuses.append("ADJUSTED")
                    adjusted_reasons.append("AVAILABLE_QTY_LIMIT")
                else:
                    row["portfolio_gate"] = "PASS"
                    statuses.append("PASS")
        else:
            row["portfolio_gate"] = "PASS"
            statuses.append("PASS")
        if row.get("action") in {"add", "conditional_add", "reduce", "sell"} and constraint is not None:
            price = _quantity(constraint.get("current_price"))
            change = _quantity(constraint.get("pct_change"))
            buy = row["action"] in {"add", "conditional_add"}
            reason = None
            if constraint.get("is_suspended"):
                reason = "SECURITY_SUSPENDED"
            elif price is None or price <= 0:
                reason = "PRICE_INVALID"
            elif change is not None and is_price_limit(
                change, code, board=constraint.get("board"), is_st=bool(constraint.get("is_st")),
                direction="up" if buy else "down",
            ):
                reason = "LIMIT_UP" if buy else "LIMIT_DOWN"
            quantity = _quantity(row.get("quantity"))
            if row.get("quantity") is not None and row.get("quantity") != "" and (quantity is None or quantity <= 0):
                reason = reason or "QUANTITY_INVALID"
            lot = _quantity(constraint.get("lot_size"))
            if lot is None or lot <= 0 or not lot.is_integer():
                reason = reason or "LOT_SIZE_UNAVAILABLE"
            if reason is None and not buy and quantity is not None and lot is not None and lot > 0:
                available = _quantity(constraint.get("max_sellable_qty"))
                # The remaining odd lot may be sold as a single whole balance.
                rounded = quantity if quantity == available else math.floor(quantity / lot) * lot
                if rounded <= 0:
                    reason = "LOT_SIZE"
                elif rounded != quantity:
                    row.update(quantity=str(rounded), proposed_qty=rounded, portfolio_gate="ADJUSTED")
                    _append_reason(row, "LOT_SIZE")
            if reason is None and buy and assets is not None and assets > 0:
                current = _weight(constraint.get("weight"))
                target = _weight(row.get("target_weight"))
                if current is None or target is None or price is None or price <= 0 or remaining_cash is None:
                    reason = "EXECUTION_INPUT_MISSING"
                else:
                    requested_cost = max(0.0, target - current) * assets
                    if quantity is not None:
                        requested_cost = min(requested_cost, quantity * price)
                    allowed_cost = min(requested_cost, max(0.0, remaining_cash))
                    allowed_qty = allowed_cost / price
                    if lot is not None and lot > 0:
                        allowed_qty = math.floor((allowed_qty + 1e-9) / lot) * lot
                    if allowed_qty <= 0:
                        reason = "CASH_OR_LOT_SIZE_LIMIT"
                    else:
                        if allowed_cost < requested_cost:
                            _append_reason(row, "CASH_LIMIT")
                        if quantity is not None and allowed_qty != quantity:
                            _append_reason(row, "QUANTITY_SIZE_LIMIT")
                        if lot is not None and abs(allowed_qty * price - allowed_cost) > 1e-6:
                            _append_reason(row, "LOT_SIZE")
                        row.update(
                            quantity=str(allowed_qty), proposed_qty=allowed_qty,
                            target_weight=current + allowed_qty * price / assets,
                            adjustment_weight=allowed_qty * price / assets,
                        )
                        if row.get("portfolio_gate_reasons"):
                            row["portfolio_gate"] = "ADJUSTED"
                        remaining_cash -= allowed_qty * price
            elif reason is None and buy:
                reason = "EXECUTION_INPUT_MISSING"
            if reason:
                _watch(row, reason)
            statuses.append(row["portfolio_gate"])
            if row["portfolio_gate"] == "BLOCKED":
                blocked_reasons.extend(row.get("portfolio_gate_reasons") or [])
            elif row["portfolio_gate"] == "ADJUSTED":
                adjusted_reasons.extend(row.get("portfolio_gate_reasons") or [])
        action_results.append({
            "code": code,
            "requested_action": requested_action,
            "final_action": row.get("action"),
            "status": row.get("portfolio_gate"),
            "requested_target_weight": requested_target,
            "allowed_target_weight": row.get("target_weight"),
            "requested_qty": requested_quantity,
            "allowed_qty": _quantity(row.get("quantity")),
            "reason_codes": row.get("portfolio_gate_reasons") or [],
        })
        updated_holdings.append(row)

    updated_candidates: list[dict[str, Any]] = []
    for raw in result.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        is_phase_f_action = str(candidate.get("candidate_engine_stage") or candidate.get("stage") or "").upper() == "ACTION"
        candidate["candidate_portfolio_fit_status"] = "RECHECKED_V3" if is_phase_f_action else "NOT_EVALUATED_V3"
        if analysis_blocked or no_risk_increase or risk_rejected or market_frozen or market_unavailable or quality in {"BLOCKED", "FROZEN"} or portfolio_context.get("cash_ratio") is None:
            candidate["buyable"] = False
            candidate["actionable"] = False
            candidate["gate_status"] = "blocked"
            candidate["portfolio_gate"] = "BLOCKED"
            candidate["portfolio_gate_reasons"] = [
                "ANALYSIS_DATA_QUALITY" if analysis_blocked or no_risk_increase else "RISK_MANAGER_REJECTED" if risk_rejected else "MARKET_STATE_FROZEN" if market_frozen else "MARKET_STATE_UNAVAILABLE" if market_unavailable else "INSUFFICIENT_CASH_DATA" if portfolio_context.get("cash_ratio") is None else "PORTFOLIO_DATA_QUALITY"
            ]
            blocked_reasons.extend(candidate["portfolio_gate_reasons"])
            statuses.append("BLOCKED")
        elif is_phase_f_action:
            fit = candidate.get("portfolio_fit") if isinstance(candidate.get("portfolio_fit"), dict) else {}
            reasons: list[str] = []
            if fit.get("hard_cap_violation"):
                reasons.append("HARD_CAP_CONSTRAINT")
            if candidate.get("funding_mode") != "CASH_FUNDED":
                reasons.append("REPLACEMENT_REVIEW_REQUIRED" if candidate.get("funding_mode") == "REPLACEMENT_REVIEW" else "UNFUNDED")
            weight = _weight(candidate.get("probe_weight"))
            cost = weight * assets if weight is not None and assets is not None else None
            if cost is None or remaining_cash is None or assets is None or assets <= 0:
                reasons.append("EXECUTION_INPUT_MISSING")
            elif cost <= 0 or cost > remaining_cash:
                reasons.append("CASH_LIMIT")
            if reasons:
                candidate["buyable"] = False
                candidate["actionable"] = False
                candidate["gate_status"] = "blocked"
                candidate["portfolio_gate"] = "BLOCKED"
                candidate["portfolio_gate_reasons"] = reasons
                blocked_reasons.extend(reasons)
                statuses.append("BLOCKED")
            else:
                candidate["buyable"] = True
                candidate["actionable"] = True
                candidate["portfolio_gate"] = "PASS"
                candidate["gate_status"] = "buyable"
                statuses.append("PASS")
                if cost is not None and remaining_cash is not None:
                    remaining_cash -= cost
        else:
            # Legacy Phase A candidates remain compatible. Phase E marks that
            # Portfolio Fit V3 is not implemented without silently deleting it.
            candidate["portfolio_gate"] = "NOT_EVALUATED_V3"
            candidate["gate_status"] = "not_evaluated_v3"
            statuses.append("REVIEW_ONLY")
        updated_candidates.append(candidate)

    result["holdings"] = updated_holdings
    result["today_actions"] = updated_holdings
    result["candidates"] = updated_candidates
    result["buy_candidates"] = updated_candidates
    # The LLM's original final_rating cannot resurrect an action that this
    # deterministic Gate has already downgraded to watch.
    actionable = has_actionable_portfolio_change(result, include_final_rating=False)
    upstream_watch_only = str(result.get("final_rating") or "").lower() == "watch_only"
    if actionable:
        portfolio_action = "ACTION"
        gate_status = "ADJUSTED" if "ADJUSTED" in statuses else "PASS"
    elif "BLOCKED" in statuses or upstream_watch_only:
        portfolio_action = "WATCH_ONLY"
        gate_status = "BLOCKED" if "BLOCKED" in statuses else "REVIEW_ONLY"
        result["final_rating"] = "watch_only"
        if "BLOCKED" in statuses:
            result["portfolio_conclusion"] = "组合约束或数据质量阻止本次可执行动作，维持观察并在条件恢复后复核。"
    else:
        portfolio_action = "NO_ACTION"
        gate_status = "REVIEW_ONLY" if "REVIEW_ONLY" in statuses else "PASS"
        result["final_rating"] = DEFAULT_PORTFOLIO_ACTION
        result["portfolio_conclusion"] = "当前没有足够证据证明调整组合优于保持现状，维持当前组合。"
    portfolio_final = result.get("portfolio_manager_final") if isinstance(result.get("portfolio_manager_final"), dict) else {}
    portfolio_final["portfolio_rating"] = str(result.get("final_rating") or DEFAULT_PORTFOLIO_ACTION)
    portfolio_final["final_actions"] = updated_holdings
    result["portfolio_manager_final"] = portfolio_final
    result["decision_gate"] = {
        "status": gate_status,
        "portfolio_action": portfolio_action,
        "action_results": action_results,
        "blocked_actions": [row for row in action_results if row["status"] == "BLOCKED"],
        "adjusted_actions": [row for row in action_results if row["status"] == "ADJUSTED"],
        "blocking_reasons": list(dict.fromkeys(blocked_reasons)),
        "warnings": list(dict.fromkeys(adjusted_reasons)),
        "risk_context": {
            "cash_ratio": portfolio_context.get("cash_ratio"),
            "gross_exposure": portfolio_context.get("gross_exposure"),
            "market_regime": portfolio_context.get("market_regime"),
            "market_state_frozen": market_frozen,
            "market_state_available": market_available,
            "portfolio_quality": quality,
        },
        "calculation_version": PORTFOLIO_GATE_VERSION,
    }
    return result


__all__ = ["apply_portfolio_decision_gate"]
