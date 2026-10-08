"""Decision repair coverage across models, final quotes and published actions."""
from __future__ import annotations

from datetime import timedelta

import pytest

from test_true_multi_agent_workflow import environment, _run
from test_decision_contract import _candidate
from app.analysis_workflow.dag import build_workflow_plan
from app.clock import utc_now
from app.decision_contract import apply_decision_status
from app.services import analysis_engine


@pytest.mark.parametrize("mode", ["fast", "standard", "deep"])
def test_quote_refresh_runs_after_every_model_node(mode):
    nodes = [node for phase in build_workflow_plan(mode).phases for node in phase.nodes]
    refresh_index = next(index for index, node in enumerate(nodes) if node.node_key == "final_quote_refresh")
    assert all(index < refresh_index for index, node in enumerate(nodes) if node.llm)
    assert nodes[refresh_index + 1].node_key == "portfolio_decision_gate"


def test_slow_portfolio_model_finishes_before_candidate_quote_refresh(environment, monkeypatch):
    harness, make_job, *_ = environment
    candidate = {**_candidate("000001"), "stage": "ACTION", "probe_weight": 0.02, "funding_mode": "CASH_FUNDED"}
    candidate_context = {"status": "ready", "quality_status": "VALID", "action": [candidate]}
    monkeypatch.setattr(analysis_engine, "_candidate_context_for_analysis", lambda *args, **kwargs: candidate_context)
    original_output = harness.output
    current_time = [utc_now()]
    refresh_calls = []

    def model_output(key, payload):
        if key == "candidate_llm_review":
            return {"accepted_codes": ["000001"], "veto_codes": []}
        result = original_output(key, payload)
        if key == "portfolio_manager":
            current_time[0] += timedelta(minutes=3)
            result["candidates"] = [candidate]
        return result

    def refresh(market, codes):
        assert harness.counts()["portfolio_manager"] == 1
        assert harness.counts()["candidate_llm_review"] == 1
        refresh_calls.append(codes)
        return {**market, "final_quote_refresh_status": "ok", "final_quote_refresh_at": current_time[0].isoformat()}

    monkeypatch.setattr(harness, "output", model_output)
    monkeypatch.setattr(analysis_engine, "utc_now", lambda: current_time[0])
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", refresh)
    run = _run(make_job("standard"))
    assert run.status == "completed", run.error_message
    assert refresh_calls == [["600519", "000001"]]
    result = run.structured_result_json["result"]
    assert "portfolio_decision_gate_unavailable" not in result.get("phase_errors", [])
    assert result["quote_verified_at"] == current_time[0].isoformat()
    assert result["portfolio_snapshot_id"] == run.structured_result_json["input_snapshot"]["id"]


@pytest.mark.parametrize("returned_action", [None, "invented_action"])
def test_partial_or_invalid_holding_analysis_is_incomplete(returned_action):
    result = analysis_engine._normalize_final(
        {"holdings": [{"code": "600519", "action": returned_action}]},
        [{"code": "600519", "available_qty": 100}, {"code": "000001", "available_qty": 100}],
        "A", {"quality_gate": {"grade": "A", "status": "pass"}},
    )
    apply_decision_status(result)
    assert result["decision_status"] == "INCOMPLETE"
    assert all(row["decision_status"] == "INCOMPLETE" for row in result["holdings"])


def test_quantity_aliases_stay_consistent_when_sell_is_capped():
    result = analysis_engine._normalize_final(
        {"holdings": [{"code": "600519", "action": "sell", "quantity": "1,000股", "proposed_qty": 1000}]},
        [{"code": "600519", "available_qty": 300}], "A",
    )
    assert result["holdings"][0]["quantity"] == 300
    assert result["holdings"][0]["proposed_qty"] == 300


@pytest.mark.parametrize("action,state", [(None, "INCOMPLETE"), ("watch", "WAITING")])
def test_published_non_decisions_do_not_claim_no_action(environment, monkeypatch, action, state):
    harness, make_job, *_ = environment
    original_output = harness.output

    def model_output(key, payload):
        result = original_output(key, payload)
        if key == "portfolio_manager":
            result["holdings"] = [] if action is None else [{"code": "600519", "action": action}]
        return result

    monkeypatch.setattr(harness, "output", model_output)
    run = _run(make_job("standard"))
    assert run.status == "completed", run.error_message
    result = run.structured_result_json["result"]
    assert result["decision_status"] == state
    assert result["final_rating"] == "watch_only"
    assert result["outcome"] == "WATCH_ONLY"
    assert result["decision_gate"]["portfolio_action"] == "WATCH_ONLY"
    assert result["portfolio_manager_final"]["portfolio_rating"] == "watch_only"


def test_optional_evidence_gaps_allow_reduction_review_but_block_new_risk():
    gate = analysis_engine._quality_gate(
        {"holdings": [{"code": "600519", "available_qty": 100}]},
        {"quotes": {"600519": {"price": 10}}, "quality_grade": "A"},
        {
            "quality_grade": "D", "content_quality_grade": "D", "action_quality_grade": "A",
            "risk_increase_allowed": False,
            "agent_statuses": {"market_analyst": "SUCCEEDED", "fundamentals_analyst": "FAILED", "news_analyst": "FAILED"},
        },
    )
    assert gate["grade"] == "C"
    assert gate["status"] == "pass"
    assert gate["evidence_grade"] == "D"
    assert gate["risk_increase_allowed"] is False


@pytest.mark.parametrize("quote", [{}, {"price": 0}, {"price": -1}, {"price": float("nan")}, {"price": True}, {"price": 10, "stale": True}])
def test_quote_self_rating_cannot_replace_required_usable_price(quote):
    gate = analysis_engine._quality_gate(
        {"holdings": [{"code": "600519", "available_qty": 100}]},
        {"quotes": {"600519": quote}, "quality_grade": "A"},
    )
    assert gate["status"] == "blocked"
    assert gate["mandatory_checks"]["quote_coverage"] is False
