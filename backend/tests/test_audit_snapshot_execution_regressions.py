"""Audit N07-N12 regressions against real snapshot, account and execution services."""
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.market_models import SecurityMaster, TradingCalendar
from app.market_engine_models import MarketScoreSnapshot
from app.memory.performance import account_performance
from app.operations.dashboard import _decision_validity, _portfolio_section
from app.portfolio import ledger
from app.portfolio.account import build_account_state
from app.portfolio.constraints import build_portfolio_constraints
from app.portfolio.decision_gate import apply_portfolio_decision_gate
from app.portfolio.risk import build_portfolio_state, latest_confirmed_snapshot
from app.portfolio.service import latest_market_state
from app.portfolio.sizing import affordable_buy_quantity
from app.portfolio.snapshot_time import observation_time_status
from app.portfolio_models import PortfolioRiskSnapshot, TradeLedgerEntry
from app.routers import portfolios_v2
from app.services.holdings_service import parse_payload_dict
from app.v2_models import HoldingUpload, PortfolioSnapshot
from app.v2_schemas import HoldingInput, ParsedHoldingsPayload
from test_portfolio_account import _db, _portfolio, _snapshot

OBSERVED = datetime(2026, 10, 8, 1, tzinfo=UTC)
CONFIRMED = datetime(2026, 10, 8, 8, tzinfo=UTC)


@pytest.fixture
def account(monkeypatch):
    db = _db()
    user, portfolio = _portfolio(db)
    master = SecurityMaster(market="CN", exchange="SSE", code="600519", symbol="600519.SH",
        name="held", security_type="STOCK", status="ACTIVE", lot_size=100)
    db.add(master)
    db.flush()
    snapshot = _snapshot(db, user, portfolio, snapshot_time=OBSERVED, cash=2000,
        holdings=[{"code": "600519", "name": "held", "qty": 100, "available_qty": 20, "cost": 10, "market_value": 1000}])
    monkeypatch.setattr(ledger, "utc_now", lambda: CONFIRMED)
    monkeypatch.setattr(portfolios_v2, "utc_now", lambda: CONFIRMED)
    yield db, user, portfolio, snapshot, master
    db.close()
    db.get_bind().dispose()


def upload(account, observed=OBSERVED):
    db, user, portfolio, _, _ = account
    raw = {"holdings": [{"code": "600519", "name": "held", "qty": 100, "available_qty": 20, "cost": 10,
                        "price": 10, "market_value": 1000}],
           "total_assets": 3000, "total_market_value": 1000, "broker_available_cash": 2000,
           "observed_at": observed.isoformat() if observed is not None else None}
    row = HoldingUpload(user_id=user.id, portfolio_id=portfolio.id, original_filename="held.png",
        storage_path="unused", mime_type="image/png", sha256="audit", parsing_status="waiting_confirmation",
        parsed_json=raw, created_at=CONFIRMED.replace(tzinfo=None))
    db.add(row)
    db.flush()
    return row


def trade(account, *, side="BUY", quantity=10, price=10, fees=0, at=None):
    db, user, portfolio, _, _ = account
    at = at or OBSERVED + timedelta(hours=1)
    return ledger.create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload={
        "entry_type": "TRADE", "security_code": "600519", "side": side,
        "quantity": quantity, "price": price, "fees": fees, "taxes": 0,
        "executed_at": at, "available_at": at,
    })[0]


def test_delayed_confirmation_preserves_event_time_and_intervening_trade(account):
    db, user, portfolio, _, _ = account
    entry = trade(account)
    row = upload(account)
    result = portfolios_v2.confirm_upload(row.id, db=db, current_user=user)
    snapshot = db.get(PortfolioSnapshot, result.id)
    assert snapshot.snapshot_time == OBSERVED.replace(tzinfo=None)
    assert snapshot.created_at == CONFIRMED.replace(tzinfo=None)
    assert result.observation_time_status == "USER_CONFIRMED"
    assert result.confirmed_at == CONFIRMED
    state = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED)
    assert state["positions"][0]["qty"] == 110 and state["applied_entry_ids"] == [entry.id]
    # A later confirmation cannot make the screenshot known at its earlier event time.
    selected = latest_confirmed_snapshot(db, portfolio_id=portfolio.id, as_of=OBSERVED + timedelta(hours=2))
    assert selected.id != snapshot.id
    with pytest.raises(ValueError, match="snapshot_not_available"):
        build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=OBSERVED)
    snapshot.upload_id = None
    db.flush()
    assert observation_time_status(snapshot) == "USER_CONFIRMED"
    assert latest_confirmed_snapshot(db, portfolio_id=portfolio.id, as_of=OBSERVED + timedelta(hours=2)).id != snapshot.id
    with pytest.raises(ValueError, match="snapshot_not_available"):
        build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=OBSERVED)


@pytest.mark.parametrize("observed,code", [
    (None, "screenshot_observed_at_required"),
    (OBSERVED.replace(tzinfo=None), "screenshot_observed_at_required"),
    (CONFIRMED + timedelta(seconds=1), "screenshot_observed_at_in_future"),
])
def test_confirmation_requires_explicit_nonfuture_zoned_fact_time(account, observed, code):
    db, user, _, _, _ = account
    row = upload(account, observed)
    with pytest.raises(HTTPException) as error:
        portfolios_v2.confirm_upload(row.id, db=db, current_user=user)
    assert error.value.status_code == 422 and error.value.detail["code"] == code
    assert db.query(PortfolioSnapshot).filter_by(upload_id=row.id).count() == 0


def test_intraday_screenshot_confirmed_after_close_is_not_close_equity(account):
    db, user, portfolio, _, _ = account
    row = upload(account)
    portfolios_v2.confirm_upload(row.id, db=db, current_user=user)
    result = account_performance(db, user_id=user.id, portfolio_id=portfolio.id,
        trade_date=OBSERVED.date(), as_of=CONFIRMED)
    assert result["source_refs"]["closing_snapshot_id"] is None
    assert result["net_pnl"] is None


@pytest.mark.parametrize("upload_linked", [True, False])
def test_legacy_unknown_observation_time_blocks_current_risk_and_close_performance(account, upload_linked):
    db, user, portfolio, snapshot, _ = account
    row = upload(account, None)
    snapshot.upload_id = row.id if upload_linked else None
    snapshot.source = "screenshot"
    snapshot.snapshot_time = snapshot.created_at = CONFIRMED.replace(tzinfo=None)
    db.flush()
    state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED,
        quote_rows=[{"code": "600519", "price": 10, "quality_status": "VALID"}])
    assert state["quality_status"] == "BLOCKED"
    assert "SNAPSHOT_OBSERVATION_TIME_UNVERIFIED" in state["risk_flags"]
    performance = account_performance(db, user_id=user.id, portfolio_id=portfolio.id,
        trade_date=OBSERVED.date(), as_of=CONFIRMED)
    assert performance["source_refs"]["closing_snapshot_id"] is None


@pytest.mark.parametrize("observed", [None, "invalid", "2026-10-08T01:00:00", "2026-10-08T02:00:00+00:00"])
def test_confirmation_marker_requires_matching_zoned_observation_time(account, observed):
    _, _, _, snapshot, _ = account
    snapshot.source = "screenshot"
    snapshot.raw_json = {"observation_time_status": "USER_CONFIRMED", "observed_at": observed}
    assert observation_time_status(snapshot) == "UNVERIFIED"


def test_manual_anchor_cannot_bypass_knowledge_time_cutoff(account):
    db, _, portfolio, snapshot, _ = account
    snapshot.created_at = CONFIRMED.replace(tzinfo=None)
    db.flush()
    assert latest_confirmed_snapshot(db, portfolio_id=portfolio.id, as_of=OBSERVED) is None
    with pytest.raises(ValueError, match="snapshot_not_available"):
        build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=OBSERVED)


def test_dashboard_skips_delayed_confirmation_and_preserves_prior_lineage(account):
    db, user, portfolio, prior, _ = account
    portfolios_v2.confirm_upload(upload(account).id, db=db, current_user=user)
    cutoff = (OBSERVED + timedelta(minutes=30)).replace(tzinfo=None)
    section = _portfolio_section(db, user_id=user.id, portfolio_id=portfolio.id, cutoff=cutoff)
    assert section["snapshot_id"] == prior.id
    validity = _decision_validity(db, user_id=user.id, portfolio_id=portfolio.id,
        source_snapshot_id=prior.id, payload={"decision_validity": {"valid_until": CONFIRMED.isoformat()}},
        cutoff=cutoff)
    assert validity["validity_status"] == "VALID"


def test_dashboard_does_not_authorize_unverified_screenshot_time(account):
    db, user, portfolio, snapshot, _ = account
    snapshot.source = "screenshot"
    db.flush()
    cutoff = (OBSERVED + timedelta(minutes=10)).replace(tzinfo=None)
    section = _portfolio_section(db, user_id=user.id, portfolio_id=portfolio.id, cutoff=cutoff)
    assert section["quality_status"] == "BLOCKED"
    assert "SNAPSHOT_OBSERVATION_TIME_UNVERIFIED" in section["risk_flags"]
    validity = _decision_validity(db, user_id=user.id, portfolio_id=portfolio.id,
        source_snapshot_id=snapshot.id, payload={"decision_validity": {"valid_until": CONFIRMED.isoformat()}},
        cutoff=cutoff)
    assert validity["validity_status"] == "UNVERIFIED"


@pytest.mark.parametrize("weight,valid", [(0.35613, True), (35.613, False), (-0.1, False)])
def test_weight_is_ratio_not_an_unlabelled_percent_point(weight, valid):
    parsed, errors = parse_payload_dict({"holdings": [{"code": "600519", "qty": 100, "weight": weight}]})
    assert bool(errors) is not valid
    assert parsed.holdings[0].weight == weight  # No guessed division by 100.
    assert parsed.holdings[0].extra["submitted_weight"] == weight


def test_weight_total_and_same_time_valuation_consistency():
    _, errors = parse_payload_dict({"holdings": [{"code": "600519", "weight": .8}, {"code": "600000", "weight": .8}]})
    assert any("之和" in error for error in errors)
    _, errors = parse_payload_dict({"total_assets": 1000,
        "holdings": [{"code": "600519", "market_value": 200, "weight": .5}]})
    assert any("不一致" in error for error in errors)


@pytest.mark.parametrize("value", ["nan", "inf", True, 10**1000])
def test_holding_numbers_reject_nonfinite_or_boolean(value):
    with pytest.raises(ValidationError):
        HoldingInput(code="600519", weight=value)


def test_oversized_account_number_is_a_validation_error():
    with pytest.raises(ValidationError):
        ParsedHoldingsPayload(total_assets=10**1000)
    with pytest.raises(ValueError):
        parse_payload_dict({"holdings": [{"code": "600519", "qty": 10**1000}]})


def test_round_trip_updates_cost_even_when_quantity_delta_is_zero(account):
    db, _, portfolio, snapshot, _ = account
    trade(account, side="SELL", quantity=50)
    trade(account, quantity=50, price=14, fees=2, at=OBSERVED + timedelta(hours=2))
    state = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED)
    row = state["positions"][0]
    assert row["qty_delta"] == 0 and row["qty"] == 100
    assert row["current_cost"] == pytest.approx(12.02) and row["cost_status"] == "KNOWN"
    assert row["position_changed"] is True and row["source"] == "snapshot+ledger"
    assert len(row["position_entry_ids"]) == 2
    risk = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED,
        quote_rows=[{"code": "600519", "price": 14, "quality_status": "VALID"}])
    assert risk["positions"][0]["cost"] == pytest.approx(12.02)


def test_revision_changes_position_version_and_cost(account):
    db, user, portfolio, snapshot, _ = account
    entry = trade(account, quantity=50, price=14)
    before = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED)["positions"][0]
    ledger.revise_ledger_entry(db, entry=entry, user_id=user.id, changes={"fees": 5}, reason="fee correction")
    after = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED)["positions"][0]
    assert after["current_cost"] == pytest.approx((1000 + 700 + 5) / 150)
    assert after["position_version"] != before["position_version"]


def legacy_entry(account, *, kind="TRADE", side="BUY", gross=None, fees=None, taxes=None):
    db, user, portfolio, _, _ = account
    moment = OBSERVED.replace(tzinfo=None) + timedelta(hours=1)
    row = TradeLedgerEntry(user_id=user.id, portfolio_id=portfolio.id, entry_type=kind,
        security_code="600519" if kind == "TRADE" else None, side=side if kind == "TRADE" else None,
        quantity=10 if kind == "TRADE" else None, gross_amount=gross, fees=fees, taxes=taxes,
        net_amount=None, currency="CNY", executed_at=moment, trade_date=moment.date(),
        available_at=moment, created_at=moment, status="CONFIRMED")
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize("kind,side", [("TRADE", "BUY"), ("TRADE", "SELL"), ("CASH_IN", None), ("FEE", None)])
def test_legacy_missing_cash_amount_blocks_execution_without_hiding_quantity(account, kind, side):
    db, _, portfolio, snapshot, _ = account
    entry = legacy_entry(account, kind=kind, side=side)
    state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED,
        quote_rows=[{"code": "600519", "price": 10, "quality_status": "VALID"}])
    assert state["cash"] is state["reserve_assets"] is state["current_estimated_total_assets"] is None
    assert state["quality_status"] == "BLOCKED"
    assert f"LEDGER_AMOUNT_MISSING:{entry.id}" in state["risk_flags"]
    expected_qty = 110 if side == "BUY" else 90 if side == "SELL" else 100
    assert state["positions"][0]["qty"] == expected_qty
    constraints = build_portfolio_constraints(state, {"available": True, "quality_status": "VALID"})
    assert constraints["can_increase_risk"] is False


def test_dashboard_does_not_restore_unknown_cash_from_old_snapshot(account):
    db, user, portfolio, _, _ = account
    legacy_entry(account)
    section = _portfolio_section(db, user_id=user.id, portfolio_id=portfolio.id,
        cutoff=CONFIRMED.replace(tzinfo=None))
    assert section["quality_status"] == "BLOCKED"
    assert section["spendable_cash"] is section["reserve_assets"] is section["cash_ratio"] is None


def test_unknown_spendable_cash_does_not_reuse_old_ratio_or_block_known_sell(account):
    db, user, portfolio, snapshot, _ = account
    snapshot.broker_available_cash = None
    snapshot.corrected_unused_funds = 9000
    snapshot.total_assets, snapshot.total_market_value = 10000, 1000
    snapshot.holdings[0].available_qty = 100
    db.add(PortfolioRiskSnapshot(
        calculation_key="legacy-cash-ratio", user_id=user.id, portfolio_id=portfolio.id,
        portfolio_snapshot_id=snapshot.id, as_of=OBSERVED.replace(tzinfo=None),
        cash_ratio=.9, quality_status="VALID",
    ))
    db.flush()

    state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED,
        quote_rows=[{"code": "600519", "price": 10, "quality_status": "VALID"}])
    constraints = build_portfolio_constraints(state, {"available": True, "quality_status": "VALID"})
    assert constraints["can_increase_risk"] is False
    assert constraints["can_reduce_risk"] is True
    gated = apply_portfolio_decision_gate({"holdings": [
        {"code": "600519", "action": "sell", "quantity": 100},
    ]}, portfolio_context={
        "portfolio_quality": state["quality_status"], "cash_ratio": state["cash_ratio"],
        "spendable_cash": state["cash"], "current_estimated_total_assets": state["total_assets"],
        "market_state_available": True, "position_constraints": constraints["positions"],
    })
    assert gated["holdings"][0]["portfolio_gate"] == "PASS"

    section = _portfolio_section(db, user_id=user.id, portfolio_id=portfolio.id,
        cutoff=CONFIRMED.replace(tzinfo=None))
    assert section["spendable_cash"] is section["cash_ratio"] is None
    assert section["reserve_assets"] == 9000
    assert section["quality_status"] == "VALID"
    assert "LEDGER_BASE_CASH_UNKNOWN" in section["risk_flags"]
    # Analysis lineage/freshness is separate from permission to spend cash.
    validity = _decision_validity(db, user_id=user.id, portfolio_id=portfolio.id,
        source_snapshot_id=snapshot.id,
        payload={"decision_validity": {"valid_until": (CONFIRMED + timedelta(hours=1)).isoformat()}},
        cutoff=CONFIRMED.replace(tzinfo=None))
    assert validity["validity_status"] == "VALID"


@pytest.mark.parametrize("side,expected_cash,expected_pending", [("BUY", 1894, 0), ("SELL", 2000, 94)])
def test_legacy_gross_only_trade_reconstructs_net_including_known_costs(account, side, expected_cash, expected_pending):
    db, _, portfolio, snapshot, _ = account
    legacy_entry(account, side=side, gross=100, fees=5, taxes=1)
    state = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED)
    assert state["cash"]["available"] == expected_cash
    assert state["cash"]["pending_sell_proceeds"] == expected_pending
    if side == "BUY":
        assert state["positions"][0]["current_cost"] == pytest.approx(1106 / 110)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_legacy_gross_only_trade_with_unknown_costs_keeps_cash_unknown(account, side):
    db, _, portfolio, snapshot, _ = account
    legacy_entry(account, side=side, gross=100, fees=None, taxes=0)
    state = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED)
    assert state["cash"]["available"] is None


@pytest.mark.parametrize("kind,side", [
    ("TRADE", "BUY"), ("TRADE", "SELL"), ("TRANSFER_IN", None),
    ("TRANSFER_OUT", None), ("CORPORATE_ACTION", None),
])
def test_known_position_delta_does_not_turn_unknown_baseline_into_zero(account, kind, side):
    db, _, portfolio, snapshot, _ = account
    snapshot.holdings[0].qty = snapshot.holdings[0].available_qty = None
    entry = legacy_entry(account, kind=kind, side=side, gross=100, fees=0, taxes=0)
    entry.security_code = "600519"
    entry.quantity = 10
    db.flush()
    state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED,
        quote_rows=[{"code": "600519", "price": 10, "quality_status": "VALID"}])
    position = state["positions"][0]
    assert position["qty"] is position["available_qty"] is position["market_value"] is None
    assert position["position_entry_ids"] == [entry.id]
    assert "POSITION_QUANTITY_UNKNOWN:600519" in state["risk_flags"]
    assert state["quality_status"] == "BLOCKED"
    assert build_portfolio_constraints(state, {"available": True, "quality_status": "VALID"})["can_increase_risk"] is False


@pytest.mark.parametrize("kind,rule,available", [
    ("STOCK", None, 20), ("ETF", "T+0", 70), ("ETF", "T+1", 20), ("ETF", None, 20),
])
def test_only_verified_t0_metadata_makes_new_buy_sellable_without_unfreezing_anchor(account, kind, rule, available):
    db, _, portfolio, snapshot, master = account
    master.security_type = kind
    master.raw_metadata_json = {"settlement": rule} if rule else {}
    trade(account, quantity=50)
    state = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=CONFIRMED)
    assert state["positions"][0]["available_qty"] == available
    assert state["positions"][0]["qty"] == 150


def seed_market(db, *, trade_day=date(2026, 10, 8), captured=OBSERVED + timedelta(hours=1), frozen=False):
    db.add(MarketScoreSnapshot(snapshot_id="audit-score", market="CN", trade_date=trade_day,
        captured_at=captured.replace(tzinfo=None), quality_status="VALID", regime="RISK_ON", is_frozen=frozen))
    db.add_all([TradingCalendar(market="CN", trade_date=date(2026, 10, 8), is_open=True),
                TradingCalendar(market="CN", trade_date=date(2026, 10, 9), is_open=True)])
    db.flush()


def test_old_valid_market_score_does_not_allow_risk_increase(account):
    db, _, _, _, _ = account
    seed_market(db)
    market = latest_market_state(db, as_of=OBSERVED + timedelta(hours=2))
    assert market["available"] is False and "MARKET_SCORE_STALE" in market["reason_codes"]
    constraints = build_portfolio_constraints({"quality_status": "VALID", "cash": 1000, "cash_ratio": .5, "positions": []}, market)
    assert constraints["can_increase_risk"] is False


def test_preopen_accepts_last_confirmed_close_but_not_an_old_intraday_score(account):
    db, _, _, _, _ = account
    seed_market(db, captured=datetime(2026, 10, 8, 7, tzinfo=UTC))
    market = latest_market_state(db, as_of=datetime(2026, 10, 9, 0, tzinfo=UTC))
    assert market["available"] is True and market["data_basis"] == "previous_session_close"
    row = db.query(MarketScoreSnapshot).one()
    row.captured_at -= timedelta(minutes=1)
    db.flush()
    market = latest_market_state(db, as_of=datetime(2026, 10, 9, 0, tzinfo=UTC))
    assert market["available"] is False and "MARKET_SCORE_CLOSE_NOT_CONFIRMED" in market["reason_codes"]


@pytest.mark.parametrize("frozen", [False, True])
def test_current_market_score_and_frozen_state(account, frozen):
    db, _, _, _, _ = account
    seed_market(db, frozen=frozen)
    market = latest_market_state(db, as_of=OBSERVED + timedelta(hours=1, minutes=1))
    assert market["available"] is not frozen and market["is_frozen"] is frozen


def test_lunch_pauses_freshness_age_and_unknown_calendar_fails_closed(account):
    db, _, _, _, _ = account
    seed_market(db, captured=datetime(2026, 10, 8, 3, 29, tzinfo=UTC))
    assert latest_market_state(db, as_of=datetime(2026, 10, 8, 4, 30, tzinfo=UTC))["available"] is True
    assert latest_market_state(db, as_of=datetime(2026, 10, 8, 5, 11, tzinfo=UTC))["available"] is False
    market = latest_market_state(db, as_of=datetime(2026, 10, 10, 2, tzinfo=UTC))
    assert market["available"] is False and "MARKET_CALENDAR_UNAVAILABLE" in market["reason_codes"]


@pytest.fixture
def costs(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "PORTFOLIO_BROKER_COMMISSION_BPS", 3.0)
    monkeypatch.setattr(settings, "PORTFOLIO_MINIMUM_COMMISSION", 5.0)
    return settings


@pytest.mark.parametrize("cash,quantity", [(1000, 0), (1005, 100), (2000, 100), (2005, 200)])
def test_buy_cash_budget_reserves_minimum_commission(costs, cash, quantity):
    qty, fee = affordable_buy_quantity(price=10, lot=100, gross_limit=2000, cash=cash)
    assert qty == quantity
    assert qty * 10 + fee <= cash


def test_unknown_fees_cannot_claim_executable_quantity(costs):
    costs.PORTFOLIO_MINIMUM_COMMISSION = None
    assert affordable_buy_quantity(price=10, lot=100, gross_limit=2000, cash=2000) == (0, None)


def test_multiple_buys_share_fee_inclusive_cash_budget(costs):
    context = {"current_estimated_total_assets": 10000, "spendable_cash": 3000, "cash_ratio": .3,
        "portfolio_quality": "VALID", "market_state_available": True,
        "position_constraints": [{"code": code, "current_price": 10, "weight": 0,
            "max_additional_weight": .2, "blocking_reasons": [], "lot_size": 100, "quote_quality": "VALID"}
            for code in ("600000", "600001")]}
    result = apply_portfolio_decision_gate({"holdings": [
        {"code": code, "action": "add", "quantity": 200, "target_weight": .2}
        for code in ("600000", "600001")]}, portfolio_context=context)
    first, second = result["holdings"]
    assert first["proposed_qty"] == 200 and first["estimated_cash_required"] == 2005
    assert second["portfolio_gate"] == "BLOCKED"
    assert "CASH_OR_LOT_SIZE_LIMIT" in second["portfolio_gate_reasons"]
