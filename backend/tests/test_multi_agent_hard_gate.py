"""Malicious model drafts cannot acquire deterministic execution authority."""
from __future__ import annotations

import copy

import pytest

from app.portfolio.decision_gate import apply_portfolio_decision_gate


def _context(**overrides):
    position = {
        "code": "600519", "current_price": 10.0, "weight": 0.18, "hard_cap": 0.20,
        "max_additional_weight": 0.02, "max_sellable_qty": 350, "lot_size": 100,
        "quote_quality": "VALID", "blocking_reasons": [], "pct_change": 0.0,
    }
    return {
        "current_estimated_total_assets": 100_000, "spendable_cash": 20_000,
        "cash_ratio": 0.2, "gross_exposure": 0.8, "portfolio_quality": "VALID",
        "market_state_available": True, "market_state_frozen": False,
        "position_constraints": [position], **overrides,
    }


def _gate(holdings, context=None, **extra):
    return apply_portfolio_decision_gate(
        {"final_rating": "add", "holdings": holdings, "candidates": [], **extra},
        portfolio_context=context or _context(),
    )


def test_hard_cap_and_quantities_are_reconciled_in_both_fields():
    result = _gate([{"code": "600519", "action": "add", "target_weight": 0.9, "quantity": "999999", "proposed_qty": 999999}])
    row = result["holdings"][0]
    assert row["requested_qty"] == 999999
    assert row["proposed_qty"] == float(row["quantity"]) == 200
    assert row["target_weight"] == pytest.approx(0.20)
    assert row["portfolio_gate"] == "ADJUSTED"


@pytest.mark.parametrize("quantity,available,allowed", [
    (999999, 350, 350), (249, 350, 200), (99, 350, None), (100, 0, None),
    (None, 350, None), (-10, 350, None),
])
def test_t_plus_one_and_odd_lot_limits(quantity, available, allowed):
    context = _context()
    context["position_constraints"][0]["max_sellable_qty"] = available
    result = _gate([{"code": "600519", "action": "sell", "quantity": quantity, "proposed_qty": quantity}], context)
    row = result["holdings"][0]
    if allowed is None:
        assert row["action"] == "watch"
        assert row["quantity"] is row["proposed_qty"] is None
        assert result["decision_gate"]["portfolio_action"] == "WATCH_ONLY"
    else:
        assert float(row["quantity"]) == row["proposed_qty"] == allowed


def test_shared_cash_budget_and_whole_lots_cover_multiple_adds():
    context = _context(spendable_cash=3_500)
    second = {**context["position_constraints"][0], "code": "600001"}
    context["position_constraints"].append(second)
    result = _gate([
        {"code": code, "action": "add", "target_weight": 0.2, "quantity": 250}
        for code in ("600519", "600001")
    ], context)
    quantities = [float(row["quantity"]) for row in result["holdings"]]
    assert quantities == [200, 100]
    assert sum(quantity * 10 for quantity in quantities) <= 3500
    assert all(quantity % 100 == 0 for quantity in quantities)


def test_supplied_exposure_limit_cannot_be_raised_by_model():
    result = _gate(
        [{"code": "600519", "action": "add", "target_weight": 0.9, "quantity": 999999}],
        _context(target_exposure=0.81),
        target_exposure=1.0,
    )
    assert result["holdings"][0]["proposed_qty"] == 100


@pytest.mark.parametrize("action,overrides,reason", [
    ("add", {"pct_change": 10.0}, "LIMIT_UP"),
    ("sell", {"pct_change": -10.0}, "LIMIT_DOWN"),
    ("add", {"current_price": 0}, "PRICE_INVALID"),
    ("sell", {"current_price": float("nan")}, "PRICE_INVALID"),
    ("sell", {"is_suspended": True}, "SECURITY_SUSPENDED"),
])
def test_price_validity_suspension_and_limits(action, overrides, reason):
    context = _context()
    context["position_constraints"][0].update(overrides)
    result = _gate([{"code": "600519", "action": action, "target_weight": 0.2, "quantity": 100}], context)
    assert result["holdings"][0]["action"] == "watch"
    assert reason in result["holdings"][0]["portfolio_gate_reasons"]


@pytest.mark.parametrize("extra", [
    {"quality_gate": {"grade": "D", "status": "blocked"}},
    {"risk_revision": {"decision": "reject"}},
])
def test_data_or_risk_block_clears_executable_values(extra):
    result = _gate([{"code": "600519", "action": "sell", "quantity": 100, "proposed_qty": 100}], **copy.deepcopy(extra))
    row = result["holdings"][0]
    assert row["action"] == "watch"
    assert row["quantity"] is row["proposed_qty"] is row["target_weight"] is None


def test_missing_assets_cannot_authorize_a_weight_only_buy():
    context = _context(current_estimated_total_assets=None)
    result = _gate([{"code": "600519", "action": "add", "target_weight": 0.2}], context)
    assert result["holdings"][0]["action"] == "watch"
    assert "EXECUTION_INPUT_MISSING" in result["decision_gate"]["blocking_reasons"]


@pytest.mark.parametrize("action", ["add", "sell"])
@pytest.mark.parametrize("field,reason", [
    ("current_price", "PRICE_INVALID"),
    ("lot_size", "LOT_SIZE_UNAVAILABLE"),
])
def test_absent_execution_constraints_fail_closed(action, field, reason):
    context = _context()
    context["position_constraints"][0].pop(field)
    result = _gate([{"code": "600519", "action": action, "target_weight": 0.2, "quantity": 150}], context)
    row = result["holdings"][0]
    assert row["action"] == "watch"
    assert row["quantity"] is row["proposed_qty"] is None
    assert reason in result["decision_gate"]["blocking_reasons"]


@pytest.mark.parametrize("action", ["add", "sell"])
@pytest.mark.parametrize("lot_size", [None, 0, -100, 0.5, float("nan")])
def test_invalid_lot_size_cannot_authorize_a_trade(action, lot_size):
    context = _context()
    context["position_constraints"][0]["lot_size"] = lot_size
    result = _gate([{"code": "600519", "action": action, "target_weight": 0.2, "quantity": 150}], context)
    assert result["holdings"][0]["action"] == "watch"
    assert "LOT_SIZE_UNAVAILABLE" in result["decision_gate"]["blocking_reasons"]
