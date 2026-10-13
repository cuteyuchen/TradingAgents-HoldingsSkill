"""Regression coverage for ledger dates and direction-specific account safety."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.market_engine_models import MarketScoreSnapshot
from app.market_models import SecurityMaster, TradingCalendar
from app.portfolio.account import build_account_state
from app.portfolio.constraints import build_portfolio_constraints
from app.portfolio.decision_gate import apply_portfolio_decision_gate
from app.portfolio.ledger import confirm_ledger_entry, create_ledger_entry, revise_ledger_entry
from app.portfolio.risk import build_portfolio_state
from app.portfolio.service import portfolio_context_for_analysis
from app.portfolio_models import TradeLedgerEntry, TradeLedgerRevision
from app.portfolio_schemas import LedgerImportCommit, TradeLedgerCreate
from app.routers.portfolio_v3 import commit_ledger_import, create_ledger
from test_portfolio_account import _db, _ensure_master, _portfolio, _snapshot


MOMENT = datetime(2026, 10, 9, 6, tzinfo=UTC)
TRADE_DAY = date(2026, 10, 9)
DATE_ERROR = "ledger_trade_date_execution_date_mismatch"


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr("app.portfolio.ledger.utc_now", lambda: MOMENT)
    monkeypatch.setattr(settings, "PORTFOLIO_BROKER_COMMISSION_BPS", 0.0)
    monkeypatch.setattr(settings, "PORTFOLIO_MINIMUM_COMMISSION", 0.0)
    session = _db()
    try:
        yield session
    finally:
        session.close()


def _payload(**changes):
    values = {
        "entry_type": "TRADE", "security_code": "600519", "side": "BUY",
        "quantity": 100, "price": 1, "fees": 0, "taxes": 0,
        "executed_at": "2026-10-09T10:00:00+08:00",
        "trade_date": "2026-10-09",
    }
    values.update(changes)
    return values


def _baseline(db, codes=("600519", "600000")):
    user, portfolio = _portfolio(db)
    snapshot = _snapshot(
        db, user, portfolio,
        snapshot_time=datetime(2026, 10, 9, 1, tzinfo=UTC),
        cash=2000 - 100 * len(codes),
        holdings=[{
            "code": code, "name": code, "qty": 100, "available_qty": 100,
            "market_value": 100, "screenshot_price": 1, "cost": 1,
        } for code in codes],
    )
    snapshot.total_assets = 2000
    snapshot.total_market_value = 100 * len(codes)
    for code in codes:
        _ensure_master(db, code)
    db.add_all([
        TradingCalendar(market="CN", trade_date=date(2026, 10, 8), is_open=True),
        TradingCalendar(
            market="CN", trade_date=TRADE_DAY, is_open=True,
            previous_trade_date=date(2026, 10, 8),
        ),
    ])
    db.add(MarketScoreSnapshot(
        snapshot_id="account-boundary", market="CN", trade_date=TRADE_DAY,
        captured_at=MOMENT.replace(tzinfo=None), quality_status="VALID",
        is_frozen=False, regime="NORMAL", display_score=60,
    ))
    db.flush()
    quotes = {"quotes": [
        {
            "code": code, "price": 1, "quality_status": "VALID",
            "pct_change": 0, "is_suspended": False,
        }
        for code in codes
    ]}
    return user, portfolio, snapshot, quotes


def _context(db, snapshot, quotes, monkeypatch):
    # Exercise real risk/context services while fixing only the wall clock.
    monkeypatch.setattr("app.portfolio.service._as_of", lambda value: MOMENT.replace(tzinfo=None))
    return portfolio_context_for_analysis(db, snapshot=snapshot, market=quotes)


def _gate(context, *, code="600000", action="reduce", quantity=100):
    row = {"code": code, "action": action, "quantity": quantity}
    if action == "add":
        row["target_weight"] = 0.10
    return apply_portfolio_decision_gate({"holdings": [row]}, portfolio_context=context, as_of=MOMENT)


@pytest.mark.parametrize("source", ["MANUAL", "CSV_IMPORT", "BROKER_IMPORT", "SNAPSHOT_RECONCILIATION"])
@pytest.mark.parametrize("trade_date", ["2026-10-08", "2026-10-10"])
def test_create_rejects_inconsistent_date_for_every_ledger_source(db, source, trade_date):
    user, portfolio, _, _ = _baseline(db)
    with pytest.raises(ValueError, match=DATE_ERROR):
        create_ledger_entry(
            db, user_id=user.id, portfolio_id=portfolio.id,
            payload=_payload(source=source, trade_date=trade_date),
        )
    assert db.query(TradeLedgerEntry).count() == 0


@pytest.mark.parametrize("executed_at", [
    "2026-10-08T16:30:00Z",
    "2026-10-09T00:30:00+08:00",
    "2026-10-09T01:30:00+09:00",
    "2026-10-09T00:30:00",
])
def test_trade_date_is_compared_with_shanghai_date_not_utc_date(db, executed_at):
    user, portfolio, _, _ = _baseline(db)
    entry, _ = create_ledger_entry(
        db, user_id=user.id, portfolio_id=portfolio.id,
        payload=_payload(executed_at=executed_at),
    )
    assert entry.trade_date == TRADE_DAY
    assert entry.executed_at == datetime(2026, 10, 8, 16, 30)


def test_ledger_api_rejects_date_mismatch_without_creating_fact(db):
    user, portfolio, _, _ = _baseline(db)
    db.commit()
    with pytest.raises(HTTPException) as error:
        create_ledger(
            portfolio.id, TradeLedgerCreate(**_payload(trade_date="2026-10-08")),
            db=db, current_user=user,
        )
    assert error.value.status_code == 422
    assert error.value.detail == DATE_ERROR
    assert db.query(TradeLedgerEntry).count() == 0


def test_import_commit_reports_bad_date_and_accepts_independent_valid_row(db):
    user, portfolio, _, _ = _baseline(db)
    imported = commit_ledger_import(
        portfolio.id,
        LedgerImportCommit(source_ref="date-boundary-import", rows=[
            TradeLedgerCreate(**_payload(trade_date="2026-10-08")),
            TradeLedgerCreate(**_payload()),
        ]),
        db=db, current_user=user,
    )
    assert imported["created"] == 1
    assert imported["errors"] == [{"row": 0, "error": DATE_ERROR}]
    entry = db.query(TradeLedgerEntry).one()
    assert entry.source == "CSV_IMPORT"
    assert entry.trade_date == TRADE_DAY


def test_rejected_date_revision_leaves_fact_and_revision_history_unchanged(db):
    user, portfolio, _, _ = _baseline(db)
    entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=_payload())
    with pytest.raises(ValueError, match=DATE_ERROR):
        revise_ledger_entry(
            db, entry=entry, user_id=user.id,
            changes={"trade_date": "2026-10-08", "quantity": 200}, reason="wrong date",
        )
    assert entry.trade_date == TRADE_DAY
    assert entry.quantity == 100
    assert db.query(TradeLedgerRevision).count() == 0


def test_execution_revision_without_date_rederives_the_shanghai_date(db, monkeypatch):
    user, portfolio, _, _ = _baseline(db)
    entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=_payload())
    monkeypatch.setattr(
        "app.portfolio.ledger.utc_now", lambda: datetime(2026, 10, 10, 6, tzinfo=UTC),
    )
    revise_ledger_entry(
        db, entry=entry, user_id=user.id,
        changes={"executed_at": "2026-10-10T10:00:00+08:00"}, reason="correct execution",
    )
    assert entry.executed_at == datetime(2026, 10, 10, 2)
    assert entry.trade_date == date(2026, 10, 10)
    assert db.query(TradeLedgerRevision).count() == 1


def test_confirmation_cannot_promote_legacy_inconsistent_date(db):
    user, portfolio, _, _ = _baseline(db)
    entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=_payload())
    # Model a pre-validation fact without weakening the service's new inputs.
    entry.trade_date = date(2026, 10, 8)
    entry.status = "PENDING_REVIEW"
    db.flush()
    with pytest.raises(ValueError, match=DATE_ERROR):
        confirm_ledger_entry(db, entry=entry, user_id=user.id, reason="review identity")
    assert entry.status == "PENDING_REVIEW"
    assert db.query(TradeLedgerRevision).count() == 0


@pytest.mark.parametrize("legacy_date", [date(2026, 10, 8), date(2026, 10, 10)])
def test_legacy_date_mismatch_does_not_release_same_day_stock_or_rewrite_fact(
    db, monkeypatch, legacy_date,
):
    user, portfolio, snapshot, quotes = _baseline(db)
    entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=_payload())
    entry.trade_date = legacy_date
    db.flush()
    current = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=MOMENT)
    held = next(row for row in current["positions"] if row["code"] == "600519")
    assert held["qty"] == 200
    assert held["available_qty"] == 100
    assert "TRADE_DATE_MISMATCH" in held["flags"]
    assert f"LEDGER_TRADE_DATE_MISMATCH:{entry.id}" in current["flags"]
    tomorrow = build_account_state(
        db, portfolio_id=portfolio.id, snapshot=snapshot,
        as_of=datetime(2026, 10, 10, 6, tzinfo=UTC),
    )
    assert tomorrow["positions"][0]["available_qty"] == 200
    assert entry.trade_date == legacy_date
    context = _context(db, snapshot, quotes, monkeypatch)
    assert context["portfolio_quality"] == "BLOCKED"
    assert _gate(context, code="600519", quantity=200)["holdings"][0]["action"] == "watch"
    assert _gate(context)["decision_gate"]["action_results"][0]["status"] == "PASS"


@pytest.mark.parametrize("kind,metadata,available", [
    ("STOCK", {}, 100),
    ("ETF", {"settlement_policy": "T_PLUS_0"}, 200),
    ("ETF", {}, 100),
])
def test_valid_buy_preserves_stock_and_explicit_etf_settlement(db, kind, metadata, available):
    code = "600519" if kind == "STOCK" else "510300"
    user, portfolio, snapshot, _ = _baseline(db, codes=(code,))
    master = db.scalar(select(SecurityMaster).where(SecurityMaster.code == code))
    master.security_type = kind
    master.etf_category = "BROAD_ETF" if kind == "ETF" else None
    master.raw_metadata_json = metadata
    create_ledger_entry(
        db, user_id=user.id, portfolio_id=portfolio.id, payload=_payload(security_code=code),
    )
    current = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=MOMENT)
    assert current["positions"][0]["qty"] == 200
    assert current["positions"][0]["available_qty"] == available
    assert not any(flag.startswith("LEDGER_TRADE_DATE_MISMATCH:") for flag in current["flags"])


@pytest.mark.parametrize("sell_qty,base_available", [(150, 100), (100, 50)])
def test_serious_ledger_contradiction_blocks_all_buys_but_preserves_valid_other_reduce(
    db, monkeypatch, sell_qty, base_available,
):
    user, portfolio, snapshot, quotes = _baseline(db)
    snapshot.holdings[0].available_qty = base_available
    entry, _ = create_ledger_entry(
        db, user_id=user.id, portfolio_id=portfolio.id,
        payload=_payload(side="SELL", quantity=sell_qty),
    )
    state = build_portfolio_state(db, portfolio_id=portfolio.id, as_of=MOMENT, quote_rows=quotes)
    assert entry.status == "CONFIRMED"
    broken = next(row for row in state["positions"] if row["code"] == "600519")
    assert broken["qty"] == 100 - sell_qty
    assert broken["available_qty"] == base_available - sell_qty
    assert state["quality_status"] == "BLOCKED"
    constraints = build_portfolio_constraints(state, {"available": True, "quality_status": "VALID"})
    assert constraints["can_increase_risk"] is False
    assert constraints["can_reduce_risk"] is True
    context = _context(db, snapshot, quotes, monkeypatch)
    add = _gate(context, action="add")
    assert add["holdings"][0]["action"] == "watch"
    assert "PORTFOLIO_DATA_QUALITY" in add["holdings"][0]["portfolio_gate_reasons"]
    assert _gate(context, code="600519")["holdings"][0]["action"] == "watch"
    legal = _gate(context)
    assert legal["decision_gate"]["action_results"][0]["status"] == "PASS"
    assert legal["decision_gate"]["action_results"][0]["allowed_qty"] == 100
    candidates = apply_portfolio_decision_gate(
        {"candidates": [{
            "code": "600001", "candidate_engine_stage": "ACTION",
            "funding_mode": "CASH_FUNDED", "probe_weight": 0.05, "buyable": True,
        }]},
        portfolio_context=context, as_of=MOMENT,
    )
    assert candidates["candidates"][0]["buyable"] is False
    assert candidates["candidates"][0]["portfolio_gate"] == "BLOCKED"


@pytest.mark.parametrize("available", [-1, 101, -1e-10, 100 + 1e-10])
def test_impossible_available_quantity_is_preserved_and_never_authorizes_that_security(
    db, monkeypatch, available,
):
    _, portfolio, snapshot, quotes = _baseline(db)
    snapshot.holdings[0].available_qty = available
    current = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=MOMENT)
    assert current["positions"][0]["available_qty"] == available
    assert current["positions"][0]["source"] == "snapshot"
    context = _context(db, snapshot, quotes, monkeypatch)
    assert context["portfolio_quality"] == "BLOCKED"
    assert _gate(context, code="600519")["holdings"][0]["action"] == "watch"
    assert _gate(context, action="add")["holdings"][0]["action"] == "watch"
    assert _gate(context)["decision_gate"]["action_results"][0]["status"] == "PASS"


@pytest.mark.parametrize("other_gap", [
    "unverified_time", "unknown_quantity", "unknown_reserve", "missing_identity",
    "missing_amount", "unsupported_currency",
])
def test_reconciliation_exception_never_relaxes_other_global_evidence_gaps(
    db, monkeypatch, other_gap,
):
    user, portfolio, snapshot, quotes = _baseline(db)
    create_ledger_entry(
        db, user_id=user.id, portfolio_id=portfolio.id,
        payload=_payload(side="SELL", quantity=150),
    )
    if other_gap == "unverified_time":
        snapshot.source = "screenshot"
        snapshot.raw_json = {}
    elif other_gap == "unknown_quantity":
        snapshot.holdings[1].qty = None
    elif other_gap == "unknown_reserve":
        snapshot.broker_available_cash = None
    elif other_gap == "missing_identity":
        snapshot.holdings[0].code = None
    else:
        extra, _ = create_ledger_entry(
            db, user_id=user.id, portfolio_id=portfolio.id,
            payload={
                "entry_type": "CASH_IN", "gross_amount": 500,
                "executed_at": "2026-10-09T11:00:00+08:00",
            },
        )
        # These legacy rows cannot enter through today's validated boundary.
        if other_gap == "missing_amount":
            extra.gross_amount = None
            extra.net_amount = None
        else:
            extra.currency = "USD"
    db.flush()
    context = _context(db, snapshot, quotes, monkeypatch)
    assert context["portfolio_quality"] == "BLOCKED"
    assert _gate(context)["holdings"][0]["action"] == "watch"
    assert "PORTFOLIO_DATA_QUALITY" in _gate(context)["holdings"][0]["portfolio_gate_reasons"]


@pytest.mark.parametrize("quality", ["BLOCKED", "FROZEN"])
def test_generic_blocked_or_frozen_context_cannot_borrow_normal_reduce_permission(db, quality):
    _, portfolio, _, quotes = _baseline(db)
    state = build_portfolio_state(db, portfolio_id=portfolio.id, as_of=MOMENT, quote_rows=quotes)
    normal = build_portfolio_constraints(state, {"available": True, "quality_status": "VALID"})
    context = {
        "portfolio_quality": quality, "position_constraints": normal["positions"],
        "market_state_available": True, "market_quality_status": "VALID",
    }
    assert normal["positions"][1]["reduce_allowed"] is True
    assert _gate(context)["holdings"][0]["action"] == "watch"


def test_frozen_context_cannot_use_even_explicit_reconciliation_reduce_authorization(db, monkeypatch):
    user, portfolio, snapshot, quotes = _baseline(db)
    create_ledger_entry(
        db, user_id=user.id, portfolio_id=portfolio.id,
        payload=_payload(side="SELL", quantity=150),
    )
    context = _context(db, snapshot, quotes, monkeypatch)
    context["portfolio_quality"] = "FROZEN"
    assert _gate(context)["holdings"][0]["action"] == "watch"


@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_reconciliation_permission_requires_backend_boolean_true(db, monkeypatch, value):
    user, portfolio, snapshot, quotes = _baseline(db)
    create_ledger_entry(
        db, user_id=user.id, portfolio_id=portfolio.id,
        payload=_payload(side="SELL", quantity=150),
    )
    context = _context(db, snapshot, quotes, monkeypatch)
    normal = next(row for row in context["position_constraints"] if row["code"] == "600000")
    assert normal["reconciliation_reduce_allowed"] is True
    normal["reconciliation_reduce_allowed"] = value
    assert _gate(context)["holdings"][0]["action"] == "watch"


def test_model_row_cannot_inject_reconciliation_permission(db):
    _, portfolio, _, quotes = _baseline(db)
    state = build_portfolio_state(db, portfolio_id=portfolio.id, as_of=MOMENT, quote_rows=quotes)
    constraints = build_portfolio_constraints(state, {"available": True, "quality_status": "VALID"})
    result = apply_portfolio_decision_gate(
        {"holdings": [{
            "code": "600000", "action": "reduce", "quantity": 100,
            "reconciliation_reduce_allowed": True, "reduce_allowed": True,
        }]},
        portfolio_context={
            "portfolio_quality": "BLOCKED", "position_constraints": constraints["positions"],
            "market_state_available": True, "market_quality_status": "VALID",
        },
        as_of=MOMENT,
    )
    assert result["holdings"][0]["action"] == "watch"


def test_intermediate_oversell_is_not_erased_when_later_buy_restores_positive_quantity(db):
    user, portfolio, snapshot, quotes = _baseline(db)
    create_ledger_entry(
        db, user_id=user.id, portfolio_id=portfolio.id,
        payload=_payload(side="SELL", quantity=150),
    )
    create_ledger_entry(
        db, user_id=user.id, portfolio_id=portfolio.id,
        payload=_payload(quantity=200, executed_at="2026-10-09T11:00:00+08:00"),
    )
    state = build_portfolio_state(
        db, portfolio_id=portfolio.id,
        as_of=datetime(2026, 10, 10, 6, tzinfo=UTC), quote_rows=quotes,
    )
    restored = next(row for row in state["positions"] if row["code"] == "600519")
    assert restored["qty"] == 150
    assert restored["available_qty"] == 150
    assert "NEGATIVE_POSITION" in restored["flags"]
    assert state["quality_status"] == "BLOCKED"
    constraints = build_portfolio_constraints(state, {"available": True, "quality_status": "VALID"})
    restored_constraint = next(row for row in constraints["positions"] if row["code"] == "600519")
    assert restored_constraint["reduce_allowed"] is False
    assert constraints["can_increase_risk"] is False


def test_audited_date_correction_restores_consistent_account_without_rewriting_other_facts(db, monkeypatch):
    user, portfolio, snapshot, quotes = _baseline(db)
    entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=_payload())
    entry.trade_date = date(2026, 10, 8)
    db.flush()
    blocked = _context(db, snapshot, quotes, monkeypatch)
    assert blocked["portfolio_quality"] == "BLOCKED"
    revise_ledger_entry(
        db, entry=entry, user_id=user.id,
        changes={"trade_date": "2026-10-09"}, reason="confirm broker execution day",
    )
    restored = _context(db, snapshot, quotes, monkeypatch)
    assert restored["portfolio_quality"] == "DEGRADED"
    assert entry.quantity == 100
    assert entry.executed_at == datetime(2026, 10, 9, 2)
    assert db.query(TradeLedgerRevision).count() == 1
    assert _gate(restored, action="add")["decision_gate"]["action_results"][0]["status"] == "PASS"
