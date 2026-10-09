"""Real ORM/service regressions for audit N01-N06 and N20; no copied predicates."""
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.governance.models import ParameterChangeProposal, ParameterSetVersion
from app.governance.service import GovernanceError, bootstrap_parameter_set, create_manual_proposal, create_rollback_proposal
from app.portfolio import ledger
from app.portfolio.account import build_account_state
from app.portfolio.ledger import confirm_ledger_entry, create_ledger_entry, revise_ledger_entry, void_ledger_entry
from app.portfolio.risk import build_portfolio_state
from app.portfolio_models import TradeLedgerEntry, TradeLedgerRevision
from app.portfolio_schemas import TradeLedgerCreate
from app.v2_models import User
from test_portfolio_account import _db, _master, _portfolio, _snapshot

BASE = datetime(2026, 10, 8, 1, tzinfo=UTC)
EVENT = BASE + timedelta(hours=1)


@pytest.fixture
def account(monkeypatch):
    db = _db()
    user, portfolio = _portfolio(db)
    _master(db, "600519")
    snapshot = _snapshot(db, user, portfolio, snapshot_time=BASE, cash=1000,
        holdings=[{"code": "600519", "qty": 100, "available_qty": 100, "cost": 10, "market_value": 1000}])
    monkeypatch.setattr(ledger, "utc_now", lambda: EVENT)
    yield db, user, portfolio, snapshot
    db.close()
    db.get_bind().dispose()


def trade(account, **changes):
    db, user, portfolio, _ = account
    payload = dict(entry_type="TRADE", security_code="600519", side="BUY", quantity=10,
        price=10, fees=0, taxes=0, executed_at=EVENT, available_at=EVENT)
    payload.update(changes)
    return create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=payload)[0]


@pytest.mark.parametrize("side,fees", [("BUY", 0), ("SELL", 0), ("BUY", 5), ("SELL", 5)])
def test_trades_conserve_equity_and_charge_fees_once(account, side, fees):
    db, _, portfolio, snapshot = account
    trade(account, side=side, quantity=40, fees=fees)
    quotes = [{"code": "600519", "price": 10, "quality_status": "VALID"}]
    states = [build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot,
        as_of=moment, quote_rows=quotes) for moment in (EVENT + timedelta(hours=1), EVENT + timedelta(days=1))]
    assert all(state["total_assets"] == pytest.approx(2000 - fees) for state in states)
    if side == "SELL":
        assert states[0]["cash"] == 1000
        assert states[0]["pending_sell_proceeds"] == 400 - fees
        assert states[1]["cash"] == 1400 - fees
        assert states[1]["pending_sell_proceeds"] == 0
    else:
        assert states[0]["cash"] == 600 - fees


@pytest.mark.parametrize("changes,gross,net", [
    ({"price": 11}, 110, 110), ({"quantity": 20}, 200, 200),
    ({"fees": 5}, 100, 105), ({"taxes": 2}, 100, 102), ({"side": "SELL", "fees": 5}, 100, 95),
])
def test_revision_recalculates_amounts_and_cash(account, changes, gross, net):
    db, user, portfolio, snapshot = account
    entry = trade(account)
    revise_ledger_entry(db, entry=entry, user_id=user.id, changes=changes, reason="broker correction")
    assert entry.gross_amount == gross
    assert entry.net_amount == net
    state = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=EVENT + timedelta(days=1))
    assert state["cash"]["available"] == 1000 + (net if entry.side == "SELL" else -net)
    revision = db.scalar(select(TradeLedgerRevision))
    assert revision.changes_json["before"]["net_amount"] == 100
    assert revision.changes_json["after"]["net_amount"] == net


def test_explicit_broker_amount_requires_explanation_and_is_preserved(account):
    db, user, _, _ = account
    entry = trade(account)
    with pytest.raises(ValueError, match="mismatch_requires_broker_explanation"):
        revise_ledger_entry(db, entry=entry, user_id=user.id, changes={"price": 11, "gross_amount": 109}, reason="correction")
    assert entry.price == 10 and db.query(TradeLedgerRevision).count() == 0
    revise_ledger_entry(db, entry=entry, user_id=user.id,
        changes={"price": 11, "gross_amount": 109, "notes": "broker statement rounding adjustment"}, reason="correction")
    assert entry.gross_amount == entry.net_amount == 109


@pytest.mark.parametrize("field,value", [
    ("quantity", "nan"), ("price", "inf"), ("price", 0), ("quantity", 0.5),
    ("fees", -1), ("taxes", -1), ("gross_amount", -1), ("net_amount", -1),
    ("fees", "inf"), ("taxes", "nan"), ("quantity", True), ("price", False),
    pytest.param("price", 10**400, id="oversized-integer"),
])
def test_create_and_revise_reject_invalid_numbers_without_writing(account, field, value):
    db, user, _, _ = account
    with pytest.raises(ValueError):
        trade(account, **{field: value})
    assert db.query(TradeLedgerEntry).count() == 0
    entry = trade(account)
    with pytest.raises(ValueError):
        revise_ledger_entry(db, entry=entry, user_id=user.id, changes={field: value}, reason="invalid input")
    assert entry.price == entry.quantity == 10
    assert db.query(TradeLedgerRevision).count() == 0


def test_derived_overflow_is_rejected(account):
    db, _, _, _ = account
    with pytest.raises(ValueError, match="finite"):
        trade(account, price=1e308, quantity=1e308)
    assert db.query(TradeLedgerEntry).count() == 0


@pytest.mark.parametrize("value", [True, False, "nan", "inf", -1])
def test_request_schema_rejects_invalid_numeric_input_before_coercion(value):
    with pytest.raises(ValidationError):
        TradeLedgerCreate(entry_type="TRADE", security_code="600519", side="BUY",
            quantity=10, price=10, fees=value, executed_at=EVENT)


def test_future_event_rejected_on_write_and_excluded_from_existing_facts(account):
    db, user, portfolio, snapshot = account
    with pytest.raises(ValueError, match="ledger_executed_at_in_future"):
        trade(account, executed_at=EVENT + timedelta(days=1))
    entry = trade(account)
    # Simulate a legacy row that predates the write-time validation.
    entry.executed_at = (EVENT + timedelta(days=1)).replace(tzinfo=None)
    db.flush()
    today = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=EVENT + timedelta(hours=1))
    assert today["entry_count"] == 0 and today["cash"]["available"] == 1000
    tomorrow = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=EVENT + timedelta(days=1, hours=1))
    assert tomorrow["entry_count"] == 1 and tomorrow["cash"]["available"] == 900


def test_revision_and_void_replay_do_not_change_past_facts_or_version(account, monkeypatch):
    db, user, portfolio, snapshot = account
    entry = trade(account)
    t1, t2, t3 = [EVENT + timedelta(minutes=n) for n in (1, 2, 3)]
    initial = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=t1)
    monkeypatch.setattr(ledger, "utc_now", lambda: t2)
    revise_ledger_entry(db, entry=entry, user_id=user.id, changes={"price": 11}, reason="correction")
    monkeypatch.setattr(ledger, "utc_now", lambda: t3)
    void_ledger_entry(db, entry=entry, user_id=user.id, reason="duplicate")
    old, corrected, voided = [build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=t) for t in (t1, t2, t3)]
    assert old == initial
    assert [state["cash"]["available"] for state in (old, corrected, voided)] == [900, 890, 1000]
    assert len({state["account_version"] for state in (old, corrected, voided)}) == 3
    assert entry.price == 11 and entry.status == "VOIDED"


def test_backdated_availability_cannot_leak_a_later_ingestion_into_past(account, monkeypatch):
    db, _, portfolio, snapshot = account
    later = EVENT + timedelta(hours=1)
    monkeypatch.setattr(ledger, "utc_now", lambda: later)
    trade(account)
    past = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=EVENT)
    current = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=later)
    assert past["entry_count"] == 0 and past["cash"]["available"] == 1000
    assert current["entry_count"] == 1 and current["cash"]["available"] == 900


def test_confirmation_does_not_make_a_pending_fact_confirmed_in_the_past(account, monkeypatch):
    db, user, portfolio, snapshot = account
    entry = trade(account, status="PENDING_REVIEW")
    later = EVENT + timedelta(hours=1)
    monkeypatch.setattr(ledger, "utc_now", lambda: later)
    confirm_ledger_entry(db, entry=entry, user_id=user.id, reason="broker verified")
    past = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=EVENT)
    current = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=later)
    assert past["entry_count"] == 0 and current["entry_count"] == 1


def test_amount_revision_preserves_audited_manual_identity_confirmation(account):
    db, user, _, _ = account
    entry = trade(account, security_code="600000")
    assert entry.status == "PENDING_REVIEW"
    confirm_ledger_entry(db, entry=entry, user_id=user.id, reason="manual identity verification")
    revise_ledger_entry(db, entry=entry, user_id=user.id, changes={"price": 11}, reason="price correction")
    assert entry.status == "CONFIRMED"
    revise_ledger_entry(db, entry=entry, user_id=user.id, changes={"security_code": "600001"}, reason="identity correction")
    assert entry.status == "PENDING_REVIEW"


def test_non_cny_rejected_and_legacy_currency_never_converted_one_to_one(account):
    db, _, portfolio, snapshot = account
    with pytest.raises(ValueError, match="currency_conversion_unavailable"):
        trade(account, currency="USD")
    entry = trade(account)
    entry.currency = "USD"
    db.flush()
    state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=EVENT + timedelta(hours=1),
        quote_rows=[{"code": "600519", "price": 10, "quality_status": "VALID"}])
    assert state["cash"] is None and state["total_assets"] is None
    assert state["quality_status"] == "BLOCKED"
    assert state["account_derivation"]["unapplied_entries"][0]["reason"] == "currency_conversion_unavailable"


def test_manual_proposal_deduplication_is_scoped_to_owner_and_content(account):
    db, user, _, _ = account
    other = User(email="other@example.com", password_hash="unused")
    db.add(other)
    db.flush()
    bootstrap_parameter_set(db)
    kwargs = dict(target_parameter_key="candidate.min_decision_edge", proposed_value=6,
        reason="evidence", risk_acknowledged=True)
    a = create_manual_proposal(db, user_id=user.id, **kwargs)
    b = create_manual_proposal(db, user_id=other.id, **kwargs)
    assert a.id != b.id and b.user_id == other.id
    assert create_manual_proposal(db, user_id=user.id, **kwargs).id == a.id
    with pytest.raises(GovernanceError, match="CONTENT_CONFLICT"):
        create_manual_proposal(db, user_id=user.id, **{**kwargs, "proposed_value": 7})


def test_rollback_deduplication_is_scoped_to_owner_and_target(account):
    db, user, _, _ = account
    bootstrap_parameter_set(db)
    base = db.scalar(select(ParameterSetVersion).where(ParameterSetVersion.status == "ACTIVE"))
    other = User(email="rollback-other@example.com", password_hash="unused")
    db.add(other)
    versions = [ParameterSetVersion(version=n, status="RETIRED", config_hash=str(n), snapshot_json=base.snapshot_json) for n in (2, 3)]
    db.add_all(versions)
    db.flush()
    a = create_rollback_proposal(db, user_id=user.id, target_version_id=versions[0].id, reason="rollback")
    b = create_rollback_proposal(db, user_id=other.id, target_version_id=versions[0].id, reason="rollback")
    assert a.id != b.id
    with pytest.raises(GovernanceError, match="CONTENT_CONFLICT"):
        create_rollback_proposal(db, user_id=user.id, target_version_id=versions[1].id, reason="rollback")
