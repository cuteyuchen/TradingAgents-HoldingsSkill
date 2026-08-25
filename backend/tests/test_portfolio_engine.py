from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.market_engine_models import DailyBarCache, MarketScoreSnapshot
from app.market_models import SecurityMaster
from app.portfolio.constraints import build_portfolio_constraints
from app.portfolio.decision_gate import apply_portfolio_decision_gate
from app.portfolio.ledger import (
    confirmed_ledger_entries_available_at,
    confirm_ledger_entry,
    create_ledger_entry,
    revise_ledger_entry,
)
from app.portfolio.risk import build_portfolio_state, calculate_risk_metrics, latest_confirmed_snapshot
from app.portfolio.service import calculate_portfolio_risk
from app.portfolio.snapshot_diff import (
    calculate_snapshot_diff,
    reconcile_snapshot_diff_with_ledger,
    refresh_affected_snapshot_reconciliations,
    upsert_snapshot_diff,
)
from app.portfolio_models import PortfolioRiskSnapshot, TradeLedgerEntry, TradeLedgerRevision
from app.trigger_models import TriggerEvent  # noqa: F401 - register FK target metadata
from app.v2_models import HoldingItem, Portfolio, PortfolioSnapshot, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _portfolio(db: Session) -> tuple[User, Portfolio]:
    user = User(email="portfolio@example.com", username="portfolio", password_hash="hash")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name="Portfolio")
    db.add(portfolio)
    db.flush()
    return user, portfolio


def _snapshot(
    db: Session,
    user: User,
    portfolio: Portfolio,
    *,
    snapshot_time: datetime,
    status: str = "confirmed",
    total_assets: float = 100_000,
    cash: float | None = 20_000,
    repo_or_standard_bond_value: float | None = None,
    holdings: list[dict] | None = None,
) -> PortfolioSnapshot:
    row = PortfolioSnapshot(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_time=snapshot_time.replace(tzinfo=None),
        status=status,
        total_assets=total_assets,
        total_market_value=total_assets - (cash or 0),
        broker_available_cash=cash,
        repo_or_standard_bond_value=repo_or_standard_bond_value,
    )
    db.add(row)
    db.flush()
    for item in holdings or [{"code": "600519", "qty": 100, "available_qty": 60, "market_value": 18_000, "cost": 180, "name": "Kweichow"}]:
        db.add(HoldingItem(snapshot_id=row.id, **item))
    db.flush()
    return row


def _master(db: Session, code: str, *, security_type: str = "STOCK", etf_category: str | None = None) -> None:
    db.add(SecurityMaster(market="CN", exchange="SSE", code=code, security_type=security_type, etf_category=etf_category, status="ACTIVE"))
    db.flush()


def _quotes(*items: tuple[str, float, str]) -> dict:
    return {"quotes": [{"code": code, "price": price, "quality_status": quality} for code, price, quality in items]}


def test_current_state_uses_latest_confirmed_not_unconfirmed_and_cash_is_a_position():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        confirmed = _snapshot(db, user, portfolio, snapshot_time=moment - timedelta(hours=2), cash=20_000)
        _snapshot(db, user, portfolio, snapshot_time=moment - timedelta(hours=1), status="waiting_confirmation", cash=1_000)
        _master(db, "600519")
        selected = latest_confirmed_snapshot(db, portfolio_id=portfolio.id, as_of=moment)
        assert selected is not None and selected.id == confirmed.id
        state = build_portfolio_state(db, portfolio_id=portfolio.id, as_of=moment, quote_rows=_quotes(("600519", 180, "VALID")))
        assert state["snapshot_id"] == confirmed.id
        assert state["cash"] == 20_000
        assert state["snapshot_total_assets"] == 100_000
        assert state["current_estimated_total_assets"] == 38_000
        assert state["cash_ratio"] == pytest.approx(20_000 / 38_000)
        assert state["positions"][0]["weight"] == pytest.approx(18_000 / 38_000)
    finally:
        db.close()


def test_snapshot_diff_tracks_quantity_not_market_value_and_never_creates_trade():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        first = _snapshot(db, user, portfolio, snapshot_time=moment - timedelta(days=1), holdings=[{"code": "600519", "qty": 100, "available_qty": 100, "market_value": 10_000, "name": "Kweichow"}])
        second = _snapshot(db, user, portfolio, snapshot_time=moment, holdings=[{"code": "600519", "qty": 200, "available_qty": 100, "market_value": 20_000, "name": "Kweichow"}])
        diff = calculate_snapshot_diff(first, second)
        row = diff["positions"][0]
        assert row["qty_delta"] == 100
        assert row["possible_direction"] == "BUY"
        assert row["confirmed_trade"] is False
        third = _snapshot(db, user, portfolio, snapshot_time=moment + timedelta(minutes=1), holdings=[{"code": "600519", "qty": 200, "available_qty": 100, "market_value": 24_000, "name": "Kweichow"}])
        no_trade = calculate_snapshot_diff(second, third)
        assert no_trade["positions"][0]["qty_delta"] == 0
        assert no_trade["positions"][0]["possible_direction"] is None
    finally:
        db.close()


def test_ledger_is_idempotent_revisioned_and_does_not_override_current_snapshot():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        snapshot = _snapshot(db, user, portfolio, snapshot_time=moment)
        _master(db, "600519")
        payload = {
            "entry_type": "TRADE", "security_code": "600519", "side": "SELL", "quantity": 100,
            "price": 180, "executed_at": moment, "idempotency_key": "broker:1",
        }
        entry, created = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=payload)
        duplicate, duplicate_created = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=payload)
        assert created is True and duplicate_created is False and duplicate.id == entry.id
        revise_ledger_entry(db, entry=entry, user_id=user.id, changes={"quantity": 200}, reason="correct broker quantity")
        assert entry.quantity == 200
        assert db.query(TradeLedgerRevision).count() == 1
        state = build_portfolio_state(db, portfolio_id=portfolio.id, as_of=moment + timedelta(minutes=1), quote_rows=_quotes(("600519", 180, "VALID")))
        assert state["positions"][0]["qty"] == 100
        assert db.query(TradeLedgerEntry).count() == 1
        assert snapshot.id == state["snapshot_id"]
    finally:
        db.close()


def test_ledger_aware_datetime_is_normalized_to_utc_naive():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload={
            "entry_type": "CASH_IN",
            "gross_amount": 1000,
            "executed_at": "2026-08-25T10:00:00+08:00",
            "available_at": "2026-08-25T10:00:00+08:00",
        })
        assert entry.executed_at == datetime(2026, 8, 25, 2, 0)
        assert entry.available_at == datetime(2026, 8, 25, 2, 0)
    finally:
        db.close()


def test_reconciliation_uses_executed_at_while_available_facts_respect_no_lookahead():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        _master(db, "600519")
        before_time = datetime(2026, 8, 20, 0, 0)
        after_time = datetime(2026, 8, 21, 0, 0)
        before = _snapshot(db, user, portfolio, snapshot_time=before_time, holdings=[
            {"code": "600519", "qty": 100, "available_qty": 100, "market_value": 10_000, "name": "A"},
        ])
        after = _snapshot(db, user, portfolio, snapshot_time=after_time, holdings=[
            {"code": "600519", "qty": 200, "available_qty": 200, "market_value": 20_000, "name": "A"},
        ])
        entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload={
            "entry_type": "TRADE", "security_code": "600519", "side": "BUY", "quantity": 100,
            "price": 100, "executed_at": "2026-08-20T10:00:00+00:00",
            "available_at": "2026-08-25T10:00:00+08:00",
        })
        assert reconcile_snapshot_diff_with_ledger(db, before=before, after=after)["status"] == "MATCHED"
        assert confirmed_ledger_entries_available_at(
            db, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 21, 0, 0)
        ) == []
        assert confirmed_ledger_entries_available_at(
            db, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 25, 3, 0, tzinfo=UTC)
        ) == [entry]
    finally:
        db.close()


def test_ledger_revision_parses_iso_datetime_and_date_values():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        _master(db, "600519")
        entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload={
            "entry_type": "TRADE", "security_code": "600519", "side": "BUY", "quantity": 1,
            "price": 100, "executed_at": "2026-08-20T10:00:00+08:00",
            "available_at": "2026-08-20T11:00:00+08:00",
        })
        revise_ledger_entry(
            db,
            entry=entry,
            user_id=user.id,
            changes={
                "executed_at": "2026-08-21T10:00:00+08:00",
                "available_at": "2026-08-25T10:00:00+08:00",
                "trade_date": "2026-08-21",
            },
            reason="correct broker timestamps",
        )
        assert entry.executed_at == datetime(2026, 8, 21, 2, 0)
        assert entry.available_at == datetime(2026, 8, 25, 2, 0)
        assert entry.trade_date == date(2026, 8, 21)
    finally:
        db.close()


def test_live_estimated_total_assets_includes_repo_without_double_counting_corrected_cash():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 25, 2, 0, tzinfo=UTC)
        _master(db, "600519")
        snapshot = _snapshot(
            db, user, portfolio, snapshot_time=moment, total_assets=1_000_000, cash=200_000,
            repo_or_standard_bond_value=300_000,
            holdings=[{"code": "600519", "qty": 100, "available_qty": 100, "market_value": 500_000, "name": "Stock"}],
        )
        state = build_portfolio_state(
            db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment,
            quote_rows=_quotes(("600519", 5_000, "VALID")),
        )
        assert state["reserve_assets"] == 500_000
        assert state["current_estimated_total_assets"] == 1_000_000
        assert state["positions"][0]["weight"] == pytest.approx(0.50)
        assert state["cash_ratio"] == pytest.approx(0.50)
        assert state["cash_only_ratio"] == pytest.approx(0.20)
    finally:
        db.close()


def test_reconciliation_without_ledger_is_unexplained_not_auto_buy():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        first = _snapshot(db, user, portfolio, snapshot_time=moment - timedelta(days=1), holdings=[{"code": "600519", "qty": 100, "available_qty": 100, "market_value": 10_000, "name": "Kweichow"}])
        second = _snapshot(db, user, portfolio, snapshot_time=moment, holdings=[{"code": "600519", "qty": 200, "available_qty": 200, "market_value": 20_000, "name": "Kweichow"}])
        assert reconcile_snapshot_diff_with_ledger(db, before=first, after=second)["status"] == "UNEXPLAINED"
    finally:
        db.close()


def test_hard_caps_and_unknown_etf_classification_are_deterministic():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        _master(db, "600519")
        _master(db, "510300", security_type="ETF", etf_category=None)
        _master(db, "512000", security_type="ETF", etf_category="SECTOR_ETF")
        snapshot = _snapshot(db, user, portfolio, snapshot_time=moment, total_assets=100_000, cash=20_000, holdings=[
            {"code": "600519", "qty": 100, "available_qty": 60, "market_value": 18_000, "name": "Stock"},
            {"code": "510300", "qty": 100, "available_qty": 100, "market_value": 31_000, "name": "Unknown ETF"},
            {"code": "512000", "qty": 100, "available_qty": 100, "market_value": 31_000, "name": "Sector ETF"},
        ])
        state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment, quote_rows=_quotes(
            ("600519", 180, "VALID"), ("510300", 310, "VALID"), ("512000", 310, "VALID")
        ))
        positions = {row["code"]: row for row in state["positions"]}
        assert positions["600519"]["hard_cap"] == pytest.approx(0.20)
        assert positions["510300"]["hard_cap"] is None and "ETF_CATEGORY_UNKNOWN" in positions["510300"]["flags"]
        assert positions["512000"]["hard_cap"] == pytest.approx(0.30)
        constraints = build_portfolio_constraints(state, {"is_frozen": False, "quality_status": "VALID"})
        stock = next(row for row in constraints["positions"] if row["code"] == "600519")
        assert stock["max_additional_weight"] == pytest.approx(0.02)
    finally:
        db.close()


def test_historical_risk_never_loads_current_quotes_and_uses_snapshot_valuation():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        historical = datetime(2026, 8, 20, 2, 0, tzinfo=UTC)
        _master(db, "600519")
        _snapshot(
            db, user, portfolio, snapshot_time=historical, total_assets=100_000, cash=20_000,
            holdings=[{"code": "600519", "qty": 100, "available_qty": 100, "market_value": 80_000, "name": "Historical"}],
        )

        def current_quote_loader(_: list[str]):
            raise AssertionError("historical calculation must not call current quote provider")

        result = calculate_portfolio_risk(
            db, portfolio_id=portfolio.id, user_id=user.id, as_of=historical + timedelta(minutes=30),
            quote_loader=current_quote_loader,
        )
        state = result["state"]
        assert state["positions"][0]["market_value"] == 80_000
        assert state["positions"][0]["current_price"] is None
        assert "HISTORICAL_SNAPSHOT_VALUATION" in state["risk_flags"]
        assert state["quality_status"] == "DEGRADED"
    finally:
        db.close()


def test_live_valuation_uses_current_estimated_total_not_screenshot_total():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        _master(db, "600519")
        snapshot = _snapshot(
            db, user, portfolio, snapshot_time=moment - timedelta(minutes=1), total_assets=100_000, cash=20_000,
            holdings=[{"code": "600519", "qty": 100, "available_qty": 100, "market_value": 80_000, "name": "Live"}],
        )
        state = build_portfolio_state(
            db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment,
            quote_rows=_quotes(("600519", 960, "VALID")),
        )
        assert state["snapshot_total_assets"] == 100_000
        assert state["current_estimated_total_assets"] == 116_000
        assert state["positions"][0]["weight"] == pytest.approx(96_000 / 116_000)
        assert state["cash_ratio"] == pytest.approx(20_000 / 116_000)
        assert state["gross_exposure"] + state["cash_ratio"] == pytest.approx(1.0)
        assert "VALUATION_DRIFT" in state["risk_flags"]
    finally:
        db.close()


def test_keep_score_does_not_turn_execution_availability_into_high_confidence_quality():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        _master(db, "600519")
        snapshot = _snapshot(
            db, user, portfolio, snapshot_time=moment, total_assets=100_000, cash=20_000,
            holdings=[{"code": "600519", "qty": 100, "available_qty": 100, "market_value": 80_000, "name": "New"}],
        )
        state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment, quote_rows=_quotes(("600519", 800, "VALID")))
        risk = calculate_risk_metrics(db, state=state, as_of=moment)
        position = risk["positions"][0]
        assert position["execution_availability"] == 100.0
        assert position["keep_score"] is None
        assert position["keep_score_available_weight"] == 0.0
        assert position["keep_score_confidence"] == 0.0
    finally:
        db.close()


def test_classified_broad_etf_without_v1_cap_can_add_up_to_cash_weight():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        _master(db, "510300", security_type="ETF", etf_category="BROAD_ETF")
        snapshot = _snapshot(
            db, user, portfolio, snapshot_time=moment, total_assets=100_000, cash=20_000,
            holdings=[{"code": "510300", "qty": 100, "available_qty": 100, "market_value": 80_000, "name": "Broad"}],
        )
        state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment, quote_rows=_quotes(("510300", 800, "VALID")))
        constraints = build_portfolio_constraints(state, {"available": True, "is_frozen": False, "quality_status": "VALID"})
        position = constraints["positions"][0]
        assert position["hard_cap"] is None
        assert position["max_additional_weight"] == pytest.approx(0.20)
        assert position["add_allowed"] is True
        result = apply_portfolio_decision_gate(
            {"final_rating": "add", "holdings": [{"code": "510300", "action": "add", "target_weight": 0.95}], "candidates": []},
            portfolio_context={
                "cash_ratio": state["cash_ratio"], "portfolio_quality": "VALID", "market_state_frozen": False,
                "market_state_available": True, "market_quality_status": "VALID", "position_constraints": constraints["positions"],
            },
        )
        assert result["holdings"][0]["portfolio_gate"] == "PASS"
    finally:
        db.close()


def test_later_ledger_confirmation_refreshes_snapshot_diff_to_matched():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        _master(db, "600519")
        before = _snapshot(db, user, portfolio, snapshot_time=moment - timedelta(days=1), holdings=[{"code": "600519", "qty": 100, "available_qty": 100, "market_value": 10_000, "name": "A"}])
        after = _snapshot(db, user, portfolio, snapshot_time=moment, holdings=[{"code": "600519", "qty": 200, "available_qty": 200, "market_value": 20_000, "name": "A"}])
        assert upsert_snapshot_diff(db, before=before, after=after).reconciliation_status == "UNEXPLAINED"
        entry, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload={
            "entry_type": "TRADE", "security_code": "600519", "side": "BUY", "quantity": 100,
            "price": 100, "executed_at": moment - timedelta(hours=1), "available_at": moment - timedelta(hours=1),
        })
        refresh_affected_snapshot_reconciliations(db, portfolio_id=portfolio.id, available_at_values=[entry.available_at])
        assert upsert_snapshot_diff(db, before=before, after=after).reconciliation_status == "MATCHED"
    finally:
        db.close()


def test_market_state_missing_blocks_new_risk_and_pending_ledger_can_be_audited_confirmed():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 0, tzinfo=UTC)
        _master(db, "600519")
        snapshot = _snapshot(db, user, portfolio, snapshot_time=moment, total_assets=100_000, cash=20_000)
        state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment, quote_rows=_quotes(("600519", 800, "VALID")))
        constraints = build_portfolio_constraints(state, {"available": False, "quality_status": "MISSING", "is_frozen": False})
        assert constraints["can_increase_risk"] is False
        assert "MARKET_STATE_UNAVAILABLE" in constraints["positions"][0]["blocking_reasons"]
        result = apply_portfolio_decision_gate(
            {"final_rating": "add", "holdings": [], "candidates": [{"code": "510300", "action": "new_position", "buyable": True}]},
            portfolio_context={"cash_ratio": 0.2, "portfolio_quality": "VALID", "market_state_frozen": False, "market_state_available": False, "market_quality_status": "MISSING"},
        )
        assert result["final_rating"] == "watch_only"
        assert result["candidates"][0]["buyable"] is False

        pending, _ = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload={
            "entry_type": "TRADE", "security_code": "600001", "side": "BUY", "quantity": 1,
            "price": 10, "executed_at": moment,
        })
        assert pending.status == "PENDING_REVIEW"
        confirm_ledger_entry(db, entry=pending, user_id=user.id, reason="security identity manually confirmed")
        assert pending.status == "CONFIRMED"
        assert db.query(TradeLedgerRevision).filter(TradeLedgerRevision.ledger_entry_id == pending.id).count() == 1
    finally:
        db.close()


def test_concentration_correlation_and_risk_contribution_use_local_daily_bar_cache():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
        codes = ["600001", "600002", "600003", "600004"]
        for code in codes:
            _master(db, code)
        snapshot = _snapshot(db, user, portfolio, snapshot_time=moment, total_assets=100_000, cash=0, holdings=[
            {"code": code, "qty": 1, "available_qty": 1, "market_value": weight * 100_000, "name": code}
            for code, weight in zip(codes, [0.4, 0.3, 0.2, 0.1])
        ])
        prices = [100.0, 100.0, 100.0, 100.0]
        for offset in range(65):
            day = date(2026, 5, 1) + timedelta(days=offset)
            for index, code in enumerate(codes):
                prices[index] *= 1.0 + (0.01 if offset % 2 else -0.004) + index * 0.0001
                db.add(DailyBarCache(
                    market="CN", code=code, trade_date=day, close=prices[index], adjustment="QFQ",
                    available_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=7), quality_status="VALID",
                ))
        db.flush()
        state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment, quote_rows=_quotes(*[(code, weight * 100_000, "VALID") for code, weight in zip(codes, [0.4, 0.3, 0.2, 0.1])]))
        risk = calculate_risk_metrics(db, state=state, as_of=moment)
        assert risk["top1_weight"] == pytest.approx(0.4)
        assert risk["top3_weight"] == pytest.approx(0.9)
        assert risk["hhi"] == pytest.approx(0.30)
        assert risk["high_correlation_pairs"]
        contributions = [row["risk_contribution_ratio"] for row in risk["positions"]]
        assert all(value is not None for value in contributions)
        assert sum(contributions) == pytest.approx(1.0)
    finally:
        db.close()


def test_correlation_with_insufficient_overlap_is_unavailable_not_zero():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)
        for code in ("600001", "600002"):
            _master(db, code)
        snapshot = _snapshot(db, user, portfolio, snapshot_time=moment, total_assets=100_000, cash=0, holdings=[
            {"code": "600001", "qty": 1, "available_qty": 1, "market_value": 50_000, "name": "A"},
            {"code": "600002", "qty": 1, "available_qty": 1, "market_value": 50_000, "name": "B"},
        ])
        for offset in range(11):
            day = date(2026, 8, 1) + timedelta(days=offset)
            for code in ("600001", "600002"):
                db.add(DailyBarCache(market="CN", code=code, trade_date=day, close=100 + offset, adjustment="QFQ", available_at=moment - timedelta(days=1), quality_status="VALID"))
        db.flush()
        state = build_portfolio_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment, quote_rows=_quotes(("600001", 50_000, "VALID"), ("600002", 50_000, "VALID")))
        risk = calculate_risk_metrics(db, state=state, as_of=moment)
        assert risk["correlation_pairs"] == []
        assert risk["max_pairwise_correlation"] is None
    finally:
        db.close()


def test_gate_adjusts_hard_cap_and_sellable_quantity_and_blocks_frozen_add():
    context = {
        "cash_ratio": 0.20, "gross_exposure": 0.80, "portfolio_quality": "VALID", "market_state_frozen": False,
        "position_constraints": [{
            "code": "600519", "weight": 0.18, "current_price": 180, "hard_cap": 0.20,
            "max_additional_weight": 0.02, "max_sellable_qty": 60, "quote_quality": "VALID", "blocking_reasons": [],
        }],
    }
    add_result = apply_portfolio_decision_gate({"final_rating": "add", "holdings": [{"code": "600519", "action": "add", "target_weight": 0.25}], "candidates": []}, portfolio_context=context)
    add = add_result["holdings"][0]
    assert add["target_weight"] == pytest.approx(0.20)
    assert add["portfolio_gate"] == "ADJUSTED"
    assert add_result["decision_gate"]["portfolio_action"] == "ACTION"
    sell_result = apply_portfolio_decision_gate({"final_rating": "sell", "holdings": [{"code": "600519", "action": "sell", "quantity": "100"}], "candidates": []}, portfolio_context=context)
    assert sell_result["holdings"][0]["quantity"] == "60.0"
    frozen = apply_portfolio_decision_gate({"final_rating": "add", "holdings": [{"code": "600519", "action": "add", "target_weight": 0.19}], "candidates": []}, portfolio_context={**context, "market_state_frozen": True})
    assert frozen["final_rating"] == "watch_only"
    assert frozen["holdings"][0]["action"] == "watch"


def test_gate_preserves_no_action_and_blocks_quote_conflict_reduce():
    context = {
        "cash_ratio": 0.2, "gross_exposure": 0.8, "portfolio_quality": "DEGRADED", "market_state_frozen": False,
        "position_constraints": [{"code": "600519", "weight": 0.1, "max_sellable_qty": 100, "quote_quality": "CONFLICT", "blocking_reasons": ["QUOTE_CONFLICT"]}],
    }
    no_action = apply_portfolio_decision_gate({"final_rating": "hold", "holdings": [{"code": "600519", "action": "hold"}], "candidates": []}, portfolio_context=context)
    assert no_action["final_rating"] == "no_action"
    blocked = apply_portfolio_decision_gate({"final_rating": "reduce", "holdings": [{"code": "600519", "action": "reduce", "quantity": "50"}], "candidates": []}, portfolio_context=context)
    assert blocked["final_rating"] == "watch_only"
    assert "QUOTE_CONFLICT" in blocked["decision_gate"]["blocking_reasons"]


def test_risk_persistence_selects_market_state_at_or_before_as_of_without_future_leakage():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        moment = datetime(2026, 8, 24, 2, 30, tzinfo=UTC)
        _master(db, "600519")
        earlier = _snapshot(db, user, portfolio, snapshot_time=moment - timedelta(hours=1))
        _snapshot(db, user, portfolio, snapshot_time=moment + timedelta(hours=1), status="confirmed")
        db.add_all([
            MarketScoreSnapshot(snapshot_id="past", market="CN", trade_date=moment.date(), captured_at=(moment - timedelta(minutes=30)).replace(tzinfo=None), quality_status="VALID", regime="RISK_ON", is_frozen=False),
            MarketScoreSnapshot(snapshot_id="future", market="CN", trade_date=moment.date(), captured_at=(moment + timedelta(minutes=30)).replace(tzinfo=None), quality_status="VALID", regime="RISK_OFF", is_frozen=False),
        ])
        db.flush()
        result = calculate_portfolio_risk(db, portfolio_id=portfolio.id, user_id=user.id, as_of=moment, persist=True, quote_rows=_quotes(("600519", 180, "VALID")))
        assert result["state"]["snapshot_id"] == earlier.id
        assert result["market_state"]["snapshot_id"] == "past"
        assert db.query(PortfolioRiskSnapshot).one().market_score_snapshot_id == "past"
    finally:
        db.close()
