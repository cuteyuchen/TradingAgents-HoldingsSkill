from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.market_models import SecurityMaster
from app.portfolio.account import build_account_state
from app.portfolio_models import TradeLedgerEntry
from app.routers.triggers_v3 import router
from app.triggers import daily_actions as daily
from app.triggers.daily_actions import (
    evaluate_daily_actions, evidence_version, link_plan_fill,
    recheck_daily_plan, refresh_daily_action_plans,
)
from app.v2_dependencies import get_current_user
from app.v2_models import AnalysisJob, AnalysisRun, HoldingItem, Portfolio, PortfolioSnapshot, User

NOW = datetime(2026, 10, 2, 2, 0, tzinfo=UTC)


@pytest.fixture
def setup():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="daily@example.test", username="daily", password_hash="hash")
        db.add(user)
        db.flush()
        portfolio = Portfolio(user_id=user.id, name="daily")
        db.add(portfolio)
        db.flush()
        snapshot = PortfolioSnapshot(user_id=user.id, portfolio_id=portfolio.id, status="confirmed",
            snapshot_time=(NOW - timedelta(hours=1)).replace(tzinfo=None), total_assets=10000,
            total_market_value=6000, broker_available_cash=4000)
        db.add(snapshot)
        db.flush()
        db.add(HoldingItem(snapshot_id=snapshot.id, code="600000", name="held", qty=600, available_qty=600, market_value=6000, cost=10))
        for code in ("600000", "600001", "600002"):
            db.add(SecurityMaster(market="CN", exchange="SSE", code=code, name=code, security_type="STOCK", status="ACTIVE", lot_size=100))
        db.flush()
        job = AnalysisJob(user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, status="succeeded", mode="standard")
        db.add(job)
        db.flush()
        account = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=NOW)
        result = {"holdings": [{"code": "600000", "name": "held", "action": "sell", "quantity": 300}],
            "account_version": account["account_version"], "quality_gate": {"status": "pass", "grade": "A"}}
        run = AnalysisRun(job_id=job.id, user_id=user.id, portfolio_snapshot_id=snapshot.id, markdown_text="daily",
            status="completed", completed_at=NOW.replace(tzinfo=None), created_at=NOW.replace(tzinfo=None),
            structured_result_json={"result": result, "market_snapshot": {"evidence_version": "ev1"}}, parameter_set_hash="gov1")
        db.add(run)
        db.flush()
        yield db, user, portfolio, snapshot, run, account
    engine.dispose()


def _quote(code="600000", *, price=10, now=NOW):
    return {"code": code, "canonical_code": code, "price": price, "pct_change": 0,
        "source": "fixture", "quality_status": "VALID", "source_timestamp": now.isoformat(), "fetched_at": now.isoformat(),
        "data_basis": "live", "instrument_status": "ACTIVE", "is_suspended": False, "is_st": False, "lot_size": 100}


def _context(*, cash=4000, available=600):
    return {"current_estimated_total_assets": 10000, "spendable_cash": cash, "cash_ratio": cash / 10000,
        "market_state_available": True, "market_quality_status": "VALID", "portfolio_quality": "VALID",
        "position_constraints": [{"code": code, "current_price": 10, "weight": 0.0 if code != "600000" else .6,
            "max_sellable_qty": available if code == "600000" else 0, "max_additional_weight": .2,
            "blocking_reasons": [], "quote_quality": "VALID", "pct_change": 0, "is_suspended": False, "is_st": False, "lot_size": 100}
            for code in ("600000", "600001", "600002")]}


def _evaluate(plans, run, account, *, entries=None, quotes=None, context=None, now=NOW, **kwargs):
    return evaluate_daily_actions(plans, run=run, account=account, entries=entries or {},
        quotes=quotes if quotes is not None else {code: _quote(code, now=now) for code in ("600000", "600001", "600002")},
        portfolio_context=context or _context(), parameter_set_hash=kwargs.pop("parameter_set_hash", "gov1"), now=now, **kwargs)


def _set_result(run, **fields):
    payload = deepcopy(run.structured_result_json)
    payload["result"].update(fields)
    run.structured_result_json = payload


def _trade(db, user, portfolio, *, quantity=100, code="600000", side="SELL", time=NOW + timedelta(minutes=1)):
    entry = TradeLedgerEntry(user_id=user.id, portfolio_id=portfolio.id, entry_type="TRADE", security_code=code,
        side=side, quantity=quantity, price=10, gross_amount=quantity * 10, net_amount=quantity * 10,
        executed_at=time.replace(tzinfo=None), available_at=time.replace(tzinfo=None), trade_date=time.date(),
        source="MANUAL", status="CONFIRMED", created_at=time.replace(tzinfo=None), updated_at=time.replace(tzinfo=None))
    db.add(entry)
    db.flush()
    return entry


def test_materialization_is_idempotent_and_covers_missing_holdings(setup):
    db, _, _, _, run, account = setup
    _set_result(run, holdings=[])
    plans = refresh_daily_action_plans(db, run, now=NOW)
    assert len(plans) == 1
    assert plans[0].target_key == "600000"
    assert _evaluate(plans, run, account)[0]["decision_status"] == "INCOMPLETE"
    plans[0].enabled = False
    assert refresh_daily_action_plans(db, run, now=NOW)[0].enabled is False
    assert evidence_version(run) == "ev1"


@pytest.mark.parametrize("change,expected", [
    ({"action": "hold", "quantity": None}, "NO_ACTION"),
    ({"action": "watch", "quantity": None}, "WAITING"),
    ({"action": "sell", "quantity": 300}, "ACTION"),
    ({"action": "sell", "quantity": "20%"}, "INCOMPLETE"),
    ({"action": "conditional_add", "quantity": 100, "target_weight": .8}, "INCOMPLETE"),
])
def test_distinct_decision_states(setup, change, expected):
    db, _, _, _, run, account = setup
    _set_result(run, holdings=[{"code": "600000", **change}])
    plans = refresh_daily_action_plans(db, run, now=NOW)
    action = _evaluate(plans, run, account)[0]
    assert action["decision_status"] == expected
    assert action["executable_quantity"] == (300 if expected == "ACTION" else 0)


@pytest.mark.parametrize("mutation,reason", [
    ("account", "PORTFOLIO_ACCOUNT_CHANGED"), ("snapshot", "PORTFOLIO_SNAPSHOT_CHANGED"),
    ("evidence", "EVIDENCE_CHANGED"), ("governance", "GOVERNANCE_CHANGED"),
    ("expiry", "PLAN_VALIDITY_EXPIRED"), ("superseded", "PLAN_SUPERSEDED"),
])
def test_changes_invalidate_plans(setup, mutation, reason):
    db, _, _, _, run, account = setup
    plans = refresh_daily_action_plans(db, run, now=NOW)
    kwargs = {}
    if mutation == "account":
        account["account_version"] = "different"
    elif mutation == "snapshot":
        account["snapshot_id"] += 1
    elif mutation == "evidence":
        _set_result(run, evidence_version="ev2")
    elif mutation == "governance":
        kwargs["parameter_set_hash"] = "gov2"
    elif mutation == "expiry":
        kwargs["now"] = NOW + timedelta(days=1)
    else:
        kwargs["latest_run_id"] = run.id + 1
    action = _evaluate(plans, run, account, **kwargs)[0]
    assert action["decision_status"] == "EXPIRED"
    assert reason in action["reason_codes"]
    assert action["executable_quantity"] == 0


def test_condition_requires_current_valid_quote_and_threshold(setup):
    db, _, _, _, run, account = setup
    _set_result(run, holdings=[{"code": "600000", "action": "sell", "quantity": 100,
        "trigger_plan": {"condition": "price_below", "threshold": 10}}])
    plans = refresh_daily_action_plans(db, run, now=NOW)
    assert _evaluate(plans, run, account)[0]["decision_status"] == "WAITING"
    assert _evaluate(plans, run, account, quotes={"600000": _quote(price=9)})[0]["decision_status"] == "ACTION"
    stale = _quote(price=9, now=NOW - timedelta(hours=1))
    assert _evaluate(plans, run, account, quotes={"600000": stale})[0]["decision_status"] == "DATA_GAP"
    assert _evaluate(plans, run, account, quotes={})[0]["decision_status"] == "DATA_GAP"


def test_sell_dependency_waits_for_complete_fill_and_real_cash(setup):
    db, user, portfolio, _, run, account = setup
    _set_result(run, candidates=[{"code": "600001", "candidate_type": "new_position", "quantity": 100,
        "target_weight": .1, "depends_on": ["600000"], "edge_vs_no_action": 10, "edge_vs_current_holdings": 10}])
    plans = refresh_daily_action_plans(db, run, now=NOW)
    buy = plans[1]
    assert buy.metadata_json["depends_on_plan_ids"] == [plans[0].id]
    action = _evaluate(plans, run, account)[1]
    assert action["decision_status"] == "WAITING"
    fill = _trade(db, user, portfolio, quantity=100)
    link_plan_fill(db, plans[0], ledger_entry_id=fill.id, now=NOW + timedelta(minutes=2))
    assert _evaluate(plans, run, account, entries={fill.id: fill}, now=NOW + timedelta(minutes=2))[1]["decision_status"] == "WAITING"
    fill.quantity = 300
    account["cash"] = {"available": 0, "pending_sell_proceeds": 3000}
    evaluated = _evaluate(plans, run, account, entries={fill.id: fill}, now=NOW + timedelta(minutes=2), context=_context(cash=0))
    assert evaluated[0]["decision_status"] == "NO_ACTION"
    assert evaluated[1]["decision_status"] == "WAITING"
    assert evaluated[1]["executable_quantity"] == 0
    assert "CASH_OR_LOT_SIZE_LIMIT" in evaluated[1]["reason_codes"]


def test_multiple_buys_share_cash_budget(setup):
    db, _, _, _, run, account = setup
    _set_result(run, holdings=[{"code": "600000", "action": "hold"}], candidates=[
        {"code": code, "candidate_type": "new_position", "quantity": 200, "target_weight": .2, "edge_vs_no_action": 10, "edge_vs_current_holdings": 10}
        for code in ("600001", "600002")])
    plans = refresh_daily_action_plans(db, run, now=NOW)
    actions = _evaluate(plans, run, account, context=_context(cash=3000))
    assert [row["executable_quantity"] for row in actions] == [0, 200, 100]
    assert sum(row["executable_quantity"] * 10 for row in actions) <= 3000


def test_partial_fill_revision_and_void_are_derived_from_ledger(setup):
    db, user, portfolio, _, run, account = setup
    plans = refresh_daily_action_plans(db, run, now=NOW)
    fill = _trade(db, user, portfolio)
    link_plan_fill(db, plans[0], ledger_entry_id=fill.id, now=NOW + timedelta(minutes=2))
    action = _evaluate(plans, run, account, entries={fill.id: fill}, now=NOW + timedelta(minutes=2))[0]
    assert (action["filled_quantity"], action["remaining_quantity"], action["executable_quantity"]) == (100, 200, 200)
    fill.quantity = 200
    assert _evaluate(plans, run, account, entries={fill.id: fill}, now=NOW + timedelta(minutes=2))[0]["remaining_quantity"] == 100
    fill.status = "VOIDED"
    assert _evaluate(plans, run, account, entries={fill.id: fill}, now=NOW + timedelta(minutes=2))[0]["remaining_quantity"] == 300
    assert len(plans[0].metadata_json["fill_entry_ids"]) == 1


def test_fill_link_checks_identity_scope_and_deduplicates(setup):
    db, user, portfolio, _, run, _ = setup
    plans = refresh_daily_action_plans(db, run, now=NOW)
    wrong = _trade(db, user, portfolio, code="600001")
    with pytest.raises(ValueError, match="does_not_match"):
        link_plan_fill(db, plans[0], ledger_entry_id=wrong.id, now=NOW + timedelta(minutes=2))
    right = _trade(db, user, portfolio)
    for _ in range(2):
        link_plan_fill(db, plans[0], ledger_entry_id=right.id, now=NOW + timedelta(minutes=2))
    assert plans[0].metadata_json["fill_entry_ids"] == [right.id]
    with pytest.raises(ValueError, match="not_found"):
        link_plan_fill(db, plans[0], ledger_entry_id=999, now=NOW)


def test_recheck_accepts_linked_fills_but_rejects_unrelated_account_changes(setup, monkeypatch):
    db, user, portfolio, snapshot, run, _ = setup
    plans = refresh_daily_action_plans(db, run, now=NOW)
    fill = _trade(db, user, portfolio)
    moment = NOW + timedelta(minutes=2)
    link_plan_fill(db, plans[0], ledger_entry_id=fill.id, now=moment)
    def runtime(*args, **kwargs):
        account = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment)
        return account, {"600000": _quote(now=moment)}, _context(available=500), {"config_hash": "gov1"}
    monkeypatch.setattr(daily, "_runtime_context", runtime)
    action = recheck_daily_plan(db, plans[0], now=moment)
    assert action["decision_status"] == "ACTION"
    assert action["remaining_quantity"] == 200
    assert len(plans[0].metadata_json["reviews"]) == 1
    _trade(db, user, portfolio, quantity=100)
    with pytest.raises(ValueError, match="account_change_requires_new_analysis"):
        recheck_daily_plan(db, plans[0], now=moment)


def test_source_quality_gate_cannot_be_bypassed(setup):
    db, _, _, _, run, account = setup
    _set_result(run, quality_gate={"status": "blocked", "grade": "D"})
    plans = refresh_daily_action_plans(db, run, now=NOW)
    action = _evaluate(plans, run, account)[0]
    assert action["decision_status"] == "DATA_GAP"
    assert action["executable_quantity"] == 0


def test_daily_plan_routes_are_scoped_and_missing_plan_is_explicit(setup, monkeypatch):
    db, user, portfolio, _, run, account = setup
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(daily, "_runtime_context", lambda *args, **kwargs: (
        account, {"600000": _quote(now=daily._moment())}, _context(), {"config_hash": "gov1"}))
    with TestClient(api) as client:
        result = client.get(f"/api/v3/triggers/daily-plan?portfolio_id={portfolio.id}")
        assert result.status_code == 200
        assert result.json()["reason_codes"] == ["DAILY_PLAN_NOT_MATERIALIZED"]
        assert client.get("/api/v3/triggers/daily-plan?portfolio_id=99999").status_code == 404
        assert client.post("/api/v3/triggers/daily-plan/refresh", json={"portfolio_id": portfolio.id, "analysis_run_id": 9999}).status_code == 404
        assert client.post("/api/v3/triggers/daily-plan/refresh", json={"portfolio_id": portfolio.id}).status_code == 200
        plan = refresh_daily_action_plans(db, run, now=NOW)[0]
        assert client.post(f"/api/v3/triggers/plans/{plan.id}/fills", json={"ledger_entry_id": 99999}).status_code == 409
        assert client.post("/api/v3/triggers/plans/99999/recheck").status_code == 404
        assert client.patch(f"/api/v3/triggers/plans/{plan.id}", json={"expires_at": (NOW + timedelta(days=10)).isoformat()}).status_code == 409
        assert client.patch(f"/api/v3/triggers/plans/{plan.id}", json={"enabled": False}).status_code == 200


@pytest.mark.parametrize("edge,expected", [(None, "DATA_GAP"), (-1, "NO_ACTION"), (10, "ACTION")])
def test_candidate_compares_current_holdings_and_cash_before_action(setup, edge, expected):
    db, _, _, _, run, account = setup
    _set_result(run, candidates=[{"code": "600001", "candidate_type": "new_position", "quantity": 100,
        "target_weight": .1, "edge_vs_no_action": 10, "edge_vs_current_holdings": edge}])
    plans = refresh_daily_action_plans(db, run, now=NOW)
    assert _evaluate(plans, run, account)[1]["decision_status"] == expected


def test_crossing_review_records_only_valid_observations(setup, monkeypatch):
    db, _, _, _, run, account = setup
    _set_result(run, holdings=[{"code": "600000", "action": "sell", "quantity": 100,
        "trigger_plan": {"metric": "price", "operator": "CROSS_BELOW", "threshold": 10}}])
    plans = refresh_daily_action_plans(db, run, now=NOW)
    quote = _quote(price=11)
    monkeypatch.setattr(daily, "_runtime_context", lambda *args, **kwargs: (account, {"600000": quote}, _context(), {"config_hash": "gov1"}))
    first = recheck_daily_plan(db, plans[0], now=NOW)
    assert first["decision_status"] == "WAITING"
    assert "condition_observation" not in first["last_review"]
    quote["price"] = 9
    second = recheck_daily_plan(db, plans[0], now=NOW)
    assert second["decision_status"] == "ACTION"
    assert second["last_review"]["condition_observation"]["met"] is True
    quote["stale"] = True
    third = recheck_daily_plan(db, plans[0], now=NOW)
    assert third["decision_status"] == "DATA_GAP"
    assert "condition_observation" not in third["last_review"]


def test_refreshing_older_analysis_never_replaces_newer_plan(setup):
    db, user, portfolio, snapshot, run, account = setup
    old_plans = refresh_daily_action_plans(db, run, now=NOW)
    job = AnalysisJob(user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, status="succeeded")
    db.add(job)
    db.flush()
    newer = AnalysisRun(job_id=job.id, user_id=user.id, portfolio_snapshot_id=snapshot.id, status="completed", markdown_text="new",
        structured_result_json=deepcopy(run.structured_result_json), created_at=(NOW + timedelta(minutes=1)).replace(tzinfo=None))
    db.add(newer)
    db.flush()
    new_plans = refresh_daily_action_plans(db, newer, now=NOW + timedelta(minutes=1))
    assert all(plan.enabled for plan in new_plans)
    assert all(not plan.enabled for plan in old_plans)


def test_live_runtime_projects_current_account_and_canonical_quotes(setup, monkeypatch):
    from app.market.instrument_schemas import BatchQuoteItem, BatchQuoteResponse, InstrumentIdentity
    from app.market.instruments import InstrumentMarketService

    db, _, _, _, run, account = setup
    plans = refresh_daily_action_plans(db, run, now=NOW)
    item = BatchQuoteItem(code="600000", instrument=InstrumentIdentity(instrument_id="CN:SSE:600000", code="600000.SH",
        symbol="600000", exchange="SSE", name="held", instrument_type="STOCK", currency="CNY", lot_size=100,
        is_st=False, is_suspended=False, status="ACTIVE"), last=10, change_pct=0,
        source="fixture", provider="fixture", observed_at=NOW, fetched_at=NOW, quality="A", status="available")
    monkeypatch.setattr(InstrumentMarketService, "batch_quotes", lambda *args, **kwargs: BatchQuoteResponse(items=[item], as_of=NOW))
    actual, quotes, context, governance = daily._runtime_context(db, plans, run, now=NOW, force_refresh=False)
    assert actual["account_version"] == account["account_version"]
    assert quotes["600000"]["price"] == 10
    assert context["spendable_cash"] == 4000
    assert context["position_constraints"][0]["max_sellable_qty"] == 600
    assert governance["config_hash"]


def test_research_expiry_and_price_invalidation_close_plan(setup):
    db, _, _, _, run, account = setup
    _set_result(run, holdings=[{"code": "600000", "action": "sell", "quantity": 100,
        "invalidation_plan": {"condition": "price_above", "threshold": 11}}])
    plans = refresh_daily_action_plans(db, run, now=NOW)
    action = _evaluate(plans, run, account, quotes={"600000": _quote(price=12)})[0]
    assert action["decision_status"] == "EXPIRED"
    assert action["reason_codes"] == ["PLAN_INVALIDATION_MET"]
    payload = deepcopy(run.structured_result_json)
    payload["market_snapshot"]["unified_evidence"] = {"records": {"r": {"kind": "news", "code": "600000",
        "status": "available", "valid_until": (NOW - timedelta(minutes=1)).isoformat()}}}
    run.structured_result_json = payload
    assert _evaluate(plans, run, account)[0]["reason_codes"] == ["EVIDENCE_EXPIRED"]


def test_daily_advice_never_becomes_a_synthetic_monitor_condition(setup):
    from app.triggers.engine import evaluate_holding_plan

    db, _, _, _, run, _ = setup
    plans = refresh_daily_action_plans(db, run, now=NOW)
    assert evaluate_holding_plan(plans[0], SimpleNamespace(quality_status="MISSING")) is None
    again = refresh_daily_action_plans(db, run, now=NOW + timedelta(minutes=2))
    assert [plan.id for plan in again] == [plan.id for plan in plans]
    assert all(plan.enabled for plan in again)
