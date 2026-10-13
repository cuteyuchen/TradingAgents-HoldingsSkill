"""Keep snapshot evidence and known quantities separate from missing costs."""
from datetime import date, datetime

import pytest

from app.market_models import TradingCalendar
from app.memory.performance import account_performance
from app.portfolio.account import build_account_state
from app.portfolio_models import TradeLedgerEntry
from test_portfolio_account import _db, _portfolio, _snapshot


DAY = date(2026, 10, 9)
CUTOFF = datetime(2026, 10, 9, 8)


@pytest.fixture
def performance_db():
    db = _db()
    try:
        yield db
    finally:
        db.close()
        db.get_bind().dispose()


def _holding(quantity=100, cost=1):
    return {"code": "600519", "name": "held", "qty": quantity,
            "available_qty": quantity, "market_value": quantity,
            "screenshot_price": 1, "cost": cost}


def _close_pair(db, *, quantity=100, cost=1):
    user, portfolio = _portfolio(db)
    db.add_all([
        TradingCalendar(market="CN", trade_date=date(2026, 10, 8), is_open=True),
        TradingCalendar(market="CN", trade_date=DAY, is_open=True),
    ])
    opening = _snapshot(db, user, portfolio, snapshot_time=datetime(2026, 10, 8, 7),
                        cash=900, holdings=[_holding(cost=cost)])
    closing = _snapshot(db, user, portfolio, snapshot_time=datetime(2026, 10, 9, 7),
                        cash=910, holdings=[_holding(quantity, cost)])
    opening.total_assets = 1000
    closing.total_assets = 1010
    db.flush()
    return user, portfolio, opening, closing


def _report(db, user, portfolio):
    return account_performance(db, user_id=user.id, portfolio_id=portfolio.id,
                               trade_date=DAY, as_of=CUTOFF)


def _entry(db, user, portfolio, **changes):
    values = dict(
        user_id=user.id, portfolio_id=portfolio.id, entry_type="TRADE",
        security_code="600519", side="BUY", quantity=100, price=1,
        fees=0, taxes=0, currency="CNY", status="CONFIRMED",
        executed_at=datetime(2026, 10, 9, 2), trade_date=DAY,
        available_at=datetime(2026, 10, 9, 2), created_at=datetime(2026, 10, 9, 2),
    )
    values.update(changes)
    row = TradeLedgerEntry(**values)
    db.add(row)
    db.flush()
    return row


def test_unused_old_screenshot_cannot_degrade_reliable_performance(performance_db):
    db = performance_db
    user, portfolio, opening, closing = _close_pair(db)
    before = _report(db, user, portfolio)
    assert before["status"] == "VALID"
    old = _snapshot(db, user, portfolio, snapshot_time=datetime(2026, 10, 7, 7),
                    cash=900, holdings=[_holding()])
    old.source, old.raw_json = "screenshot", {}
    db.flush()
    after = _report(db, user, portfolio)
    assert after == before
    assert after["source_refs"]["anchor_snapshot_id"] == opening.id
    assert after["source_refs"]["closing_snapshot_id"] == closing.id


@pytest.mark.parametrize("missing", ["opening", "closing", "both"])
def test_missing_required_trusted_close_cannot_fall_back_to_old_anchor(performance_db, missing):
    db = performance_db
    user, portfolio, opening, closing = _close_pair(db)
    older = _snapshot(db, user, portfolio, snapshot_time=datetime(2026, 10, 7, 7),
                      cash=900, holdings=[_holding()])
    for snapshot in (opening, closing) if missing == "both" else (
            opening if missing == "opening" else closing,):
        snapshot.source, snapshot.raw_json = "screenshot", {}
    db.flush()
    report = _report(db, user, portfolio)
    assert report["status"] == "INCOMPLETE"
    assert report["net_pnl"] is None
    assert report["source_refs"]["anchor_snapshot_id"] == older.id
    assert report["source_refs"]["opening_snapshot_id"] == (
        opening.id if missing == "closing" else None)
    assert report["source_refs"]["closing_snapshot_id"] == (
        closing.id if missing == "opening" else None)
    assert "SNAPSHOT_OBSERVATION_TIME_UNVERIFIED" in report["reason_codes"]
    assert "CLOSE_SNAPSHOT_PAIR_MISSING" in report["reason_codes"]


def test_all_unverified_snapshots_leave_anchor_missing(performance_db):
    db = performance_db
    user, portfolio, opening, closing = _close_pair(db)
    for snapshot in (opening, closing):
        snapshot.source, snapshot.raw_json = "screenshot", {}
    db.flush()
    report = _report(db, user, portfolio)
    assert report["status"] == "INCOMPLETE"
    assert report["source_refs"]["anchor_snapshot_id"] is None
    assert "INITIAL_ACCOUNT_MISSING" in report["reason_codes"]
    assert "SNAPSHOT_OBSERVATION_TIME_UNVERIFIED" in report["reason_codes"]


@pytest.mark.parametrize("side,quantity", [("BUY", 200), ("SELL", 50)])
def test_unknown_initial_cost_keeps_known_trade_quantity(performance_db, side, quantity):
    db = performance_db
    user, portfolio, opening, _ = _close_pair(db, quantity=quantity, cost=None)
    entry = _entry(db, user, portfolio, side=side, quantity=abs(quantity - 100))
    account = build_account_state(db, portfolio_id=portfolio.id, snapshot=opening, as_of=CUTOFF)
    report = _report(db, user, portfolio)
    position = report["positions"][0]
    assert position["quantity"] == account["positions"][0]["qty"] == quantity
    assert position["average_cost"] is None
    assert report["source_refs"]["ledger_entry_ids"] == [entry.id]
    assert report["status"] == "INCOMPLETE"
    assert report["net_pnl"] == 10
    assert report["realized_pnl"] is report["unrealized_pnl"] is None
    assert not any("QUANTITY_MISMATCH" in reason for reason in report["reason_codes"])
    if side == "SELL":
        assert position["realized_pnl"] is None


def test_liquidation_restores_new_position_cost_but_not_unknown_realized_pnl(performance_db):
    db = performance_db
    user, portfolio, opening, closing = _close_pair(db, cost=None)
    _entry(db, user, portfolio, side="SELL", quantity=100)
    _entry(db, user, portfolio, quantity=100, price=2, fees=1,
           executed_at=datetime(2026, 10, 9, 3), available_at=datetime(2026, 10, 9, 3))
    closing.holdings[0].screenshot_price = 3
    db.flush()
    account = build_account_state(db, portfolio_id=portfolio.id, snapshot=opening, as_of=CUTOFF)
    report = _report(db, user, portfolio)
    position = report["positions"][0]
    assert position["quantity"] == account["positions"][0]["qty"] == 100
    assert position["average_cost"] == account["positions"][0]["current_cost"] == pytest.approx(2.01)
    assert position["unrealized_pnl"] == pytest.approx(99)
    assert position["realized_pnl"] is None
    assert report["realized_pnl"] is report["total_pnl_since_anchor"] is None
    assert not any("QUANTITY_MISMATCH" in reason for reason in report["reason_codes"])


def test_missing_trade_price_does_not_discard_known_quantity(performance_db):
    db = performance_db
    user, portfolio, _, _ = _close_pair(db, quantity=200)
    entry = _entry(db, user, portfolio, price=None)
    report = _report(db, user, portfolio)
    assert report["positions"][0]["quantity"] == 200
    assert report["positions"][0]["average_cost"] is None
    assert f"TRADE_FACT_MISSING:{entry.id}" in report["reason_codes"]
    assert not any("QUANTITY_MISMATCH" in reason for reason in report["reason_codes"])


def test_unknown_baseline_quantity_is_not_inferred_from_new_buy(performance_db):
    db = performance_db
    user, portfolio, opening, _ = _close_pair(db)
    opening.holdings[0].qty = None
    _entry(db, user, portfolio)
    report = _report(db, user, portfolio)
    assert report["positions"][0]["quantity"] is None
    assert report["positions"][0]["average_cost"] is None
    assert report["status"] == "INCOMPLETE"
    assert report["total_pnl_since_anchor"] is None


@pytest.mark.parametrize("kind,quantity", [("TRANSFER_IN", 150), ("TRANSFER_OUT", 50)])
def test_security_transfer_replays_quantity_without_inventing_cost_or_flow(performance_db, kind, quantity):
    db = performance_db
    user, portfolio, opening, _ = _close_pair(db, quantity=quantity)
    entry = _entry(db, user, portfolio, entry_type=kind, side=None, quantity=50, price=None)
    account = build_account_state(db, portfolio_id=portfolio.id, snapshot=opening, as_of=CUTOFF)
    report = _report(db, user, portfolio)
    assert report["positions"][0]["quantity"] == account["positions"][0]["qty"] == quantity
    assert report["positions"][0]["average_cost"] is None
    assert report["status"] == "INCOMPLETE"
    assert report["net_pnl"] is report["total_pnl_since_anchor"] is None
    assert report["external_flow"] is None
    assert f"SECURITY_TRANSFER_COST_MISSING:{entry.id}" in report["reason_codes"]
    assert not any("QUANTITY_MISMATCH" in reason for reason in report["reason_codes"])


def test_transfer_before_opening_does_not_hide_independent_daily_net_pnl(performance_db):
    db = performance_db
    user, portfolio, _, _ = _close_pair(db)
    anchor = _snapshot(db, user, portfolio, snapshot_time=datetime(2026, 10, 7, 7),
                       cash=900, holdings=[_holding(50)])
    _entry(db, user, portfolio, entry_type="TRANSFER_IN", side=None, quantity=50, price=None,
           executed_at=datetime(2026, 10, 8, 2), available_at=datetime(2026, 10, 8, 2))
    report = _report(db, user, portfolio)
    assert report["source_refs"]["anchor_snapshot_id"] == anchor.id
    assert report["status"] == "INCOMPLETE"
    assert report["positions"][0]["quantity"] == 100
    assert report["net_pnl"] == 10
    assert report["external_flow"] == 0
    assert report["total_pnl_since_anchor"] is None
