"""Final candidate sizing uses refreshed server quotes and deterministic facts."""
from __future__ import annotations

import copy
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_candidate_execution_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)

NOW = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)


@pytest.fixture
def context(monkeypatch):
    from app.portfolio import decision_gate

    monkeypatch.setattr(decision_gate, "utc_now", lambda: NOW)
    return {
        "cash_ratio": 0.2,
        "current_estimated_total_assets": 100_000,
        "spendable_cash": 20_000,
        "portfolio_quality": "VALID",
        "market_state_available": True,
        "execution_quotes_required": True,
        "execution_candidates": [{
            "code": "600001", "stage": "ACTION", "funding_mode": "CASH_FUNDED",
            "probe_weight": 0.05, "portfolio_fit": {"hard_cap_violation": False},
        }],
        "execution_quotes": {"600001": {
            "code": "600001", "security_type": "STOCK", "instrument_status": "ACTIVE",
            "price": 12.0, "pct_change": 1.0, "lot_size": 100,
            "board": "MAIN", "is_st": False, "is_suspended": False,
            "quality_status": "VALID", "stale": False, "source": "verified-provider",
            "source_timestamp": NOW.isoformat(), "fetched_at": NOW.isoformat(), "data_basis": "live",
        }},
    }


def _run(context, candidates=None):
    from app.portfolio.decision_gate import apply_portfolio_decision_gate

    return apply_portfolio_decision_gate({
        "holdings": [], "quality_gate": {"grade": "A", "status": "pass", "risk_increase_allowed": True},
        "candidates": candidates if candidates is not None else [{"code": "600001", "candidate_type": "new_position"}],
    }, portfolio_context=context)


def test_refreshed_price_and_server_weight_override_model_execution_fields(context):
    result = _run(context, [{
        "code": "600001", "candidate_type": "new_position", "probe_weight": 0.9,
        "price": 1.0, "lot_size": 1, "quantity": "90000", "proposed_qty": 90000,
        "funding_mode": "UNFUNDED", "portfolio_fit": {"hard_cap_violation": True},
    }])
    candidate = result["candidates"][0]

    assert candidate["portfolio_gate"] == "ADJUSTED"
    assert candidate["portfolio_gate_reasons"] == ["LOT_SIZE"]
    assert candidate["quantity"] == "400"
    assert candidate["proposed_qty"] == 400
    assert candidate["price"] == 12
    assert candidate["target_weight"] == pytest.approx(0.048)
    assert candidate["probe_weight"] == 0.05
    assert candidate["lot_size"] == 100
    assert candidate["quote_source"] == "verified-provider"
    assert result["decision_gate"]["portfolio_action"] == "ACTION"


@pytest.mark.parametrize("change,reason", [
    ({"price": None}, "PRICE_INVALID"),
    ({"price": float("nan")}, "PRICE_INVALID"),
    ({"price": float("inf")}, "PRICE_INVALID"),
    ({"price": True}, "PRICE_INVALID"),
    ({"price": -1}, "PRICE_INVALID"),
    ({"quality_status": "CONFLICT"}, "QUOTE_CONFLICT"),
    ({"quality_status": "MISSING"}, "QUOTE_MISSING"),
    ({"stale": True}, "QUOTE_STALE"),
    ({"source_timestamp": (NOW - timedelta(minutes=3)).isoformat()}, "QUOTE_STALE"),
    ({"fetched_at": (NOW - timedelta(minutes=3)).isoformat()}, "QUOTE_STALE"),
    ({"source_timestamp": (NOW + timedelta(minutes=1)).isoformat()}, "QUOTE_TIME_INVALID"),
    ({"source_timestamp": None}, "QUOTE_TIME_MISSING"),
    ({"source": None}, "QUOTE_SOURCE_MISSING"),
    ({"code": "600002"}, "INSTRUMENT_IDENTITY_MISMATCH"),
    ({"security_type": "INDEX"}, "INSTRUMENT_NOT_TRADABLE"),
    ({"instrument_status": "DELISTED"}, "INSTRUMENT_NOT_TRADABLE"),
    ({"is_suspended": True}, "SECURITY_SUSPENDED"),
    ({"is_suspended": None}, "TRADING_STATUS_UNAVAILABLE"),
    ({"is_st": None}, "TRADING_STATUS_UNAVAILABLE"),
    ({"pct_change": 9.95}, "LIMIT_UP"),
    ({"is_st": True, "pct_change": 4.95}, "LIMIT_UP"),
    ({"pct_change": None}, "PRICE_LIMIT_INPUT_MISSING"),
    ({"lot_size": None}, "LOT_SIZE_UNAVAILABLE"),
    ({"lot_size": 0}, "LOT_SIZE_UNAVAILABLE"),
    ({"lot_size": 1.5}, "LOT_SIZE_UNAVAILABLE"),
])
def test_invalid_final_quote_fails_closed_and_clears_model_quantity(context, change, reason):
    context["execution_quotes"]["600001"].update(change)
    candidate = _run(context, [{
        "code": "600001", "candidate_type": "new_position", "quantity": "99999", "proposed_qty": 99999,
    }])["candidates"][0]

    assert candidate["portfolio_gate"] == "BLOCKED"
    assert reason in candidate["portfolio_gate_reasons"]
    assert candidate["buyable"] is False
    assert candidate["quantity"] is None
    assert candidate["proposed_qty"] is None


@pytest.mark.parametrize("missing_field,reason", [
    ("execution_candidates", "CANDIDATE_CONTEXT_UNAVAILABLE"),
    ("execution_quotes", "QUOTE_MISSING"),
])
def test_model_cannot_replace_missing_server_input(context, missing_field, reason):
    context.pop(missing_field)
    candidate = _run(context, [{
        "code": "600001", "stage": "ACTION", "funding_mode": "CASH_FUNDED", "probe_weight": 0.05,
        "portfolio_fit": {"hard_cap_violation": False}, "price": 10, "lot_size": 100,
    }])["candidates"][0]

    assert candidate["portfolio_gate"] == "BLOCKED"
    assert reason in candidate["portfolio_gate_reasons"]


def test_candidates_share_remaining_cash_after_lot_rounding(context):
    context["spendable_cash"] = 7_000
    context["execution_candidates"].append({**context["execution_candidates"][0], "code": "600002"})
    context["execution_quotes"]["600002"] = {**context["execution_quotes"]["600001"], "code": "600002"}
    result = _run(context, [{"code": code, "candidate_type": "new_position"} for code in ("600001", "600002")])
    first, second = result["candidates"]

    assert first["proposed_qty"] == 400
    assert second["proposed_qty"] == 100
    assert second["portfolio_gate"] == "ADJUSTED"
    assert "CASH_LIMIT" in second["portfolio_gate_reasons"]
    assert sum(row["proposed_qty"] * row["price"] for row in result["candidates"]) <= 7_000


def test_budget_below_one_lot_is_blocked(context):
    context["spendable_cash"] = 1_000
    candidate = _run(context)["candidates"][0]

    assert candidate["portfolio_gate"] == "BLOCKED"
    assert "CASH_OR_LOT_SIZE_LIMIT" in candidate["portfolio_gate_reasons"]
    assert candidate["proposed_qty"] is None


def test_limit_check_can_use_verified_previous_close(context):
    context["execution_quotes"]["600001"].update(price=11, pct_change=None, prev_close=10)
    candidate = _run(context)["candidates"][0]

    assert candidate["portfolio_gate"] == "BLOCKED"
    assert "LIMIT_UP" in candidate["portfolio_gate_reasons"]


def test_growth_board_uses_server_price_limit_rule(context):
    context["execution_candidates"][0]["code"] = "300001"
    context["execution_quotes"]["300001"] = {
        **context["execution_quotes"].pop("600001"), "code": "300001", "board": "CHINEXT", "pct_change": 12,
    }
    candidate = _run(context, [{"code": "300001", "candidate_type": "new_position", "is_st": True}])["candidates"][0]

    assert candidate["buyable"] is True
    assert candidate["proposed_qty"] == 400
    assert candidate["is_st"] is False


def test_legacy_call_without_required_flag_preserves_existing_contract(context):
    legacy = copy.deepcopy(context)
    legacy.pop("execution_quotes_required")
    legacy.pop("execution_quotes")
    legacy.pop("execution_candidates")
    candidate = _run(legacy, [{
        "code": "600001", "candidate_type": "new_position", "stage": "ACTION",
        "funding_mode": "CASH_FUNDED", "probe_weight": 0.05,
    }])["candidates"][0]

    assert candidate["portfolio_gate"] == "PASS"
    assert candidate["buyable"] is True
    assert "proposed_qty" not in candidate
