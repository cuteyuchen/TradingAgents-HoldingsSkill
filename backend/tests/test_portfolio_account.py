from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.market_models import SecurityMaster
from app.operations.dashboard import _decision_validity
from app.portfolio.account import build_account_state
from app.portfolio.imports import import_idempotency_key, preview_import
from app.portfolio.ledger import create_ledger_entry, revise_ledger_entry, void_ledger_entry
from app.portfolio.risk import build_portfolio_state
from app.portfolio_models import TradeLedgerEntry
from app.trigger_models import TriggerEvent  # noqa: F401 - register FK target metadata
from app.v2_models import HoldingItem, Portfolio, PortfolioSnapshot, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _portfolio(db: Session) -> tuple[User, Portfolio]:
    user = User(email="account@example.com", username="account", password_hash="hash")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name="Account")
    db.add(portfolio)
    db.flush()
    return user, portfolio


def _snapshot(
    db: Session,
    user: User,
    portfolio: Portfolio,
    *,
    snapshot_time: datetime,
    cash: float | None = 20_000,
    holdings: list[dict] | None = None,
) -> PortfolioSnapshot:
    row = PortfolioSnapshot(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_time=snapshot_time.replace(tzinfo=None),
        status="confirmed",
        total_assets=100_000,
        total_market_value=80_000,
        broker_available_cash=cash,
    )
    db.add(row)
    db.flush()
    for item in holdings or [{
        "code": "600519", "name": "贵州茅台", "qty": 1000, "available_qty": 1000,
        "market_value": 80_000, "cost": 70_000,
    }]:
        db.add(HoldingItem(snapshot_id=row.id, **item))
    db.flush()
    return row


def _master(db: Session, code: str) -> None:
    db.add(SecurityMaster(market="CN", exchange="SSE", code=code, security_type="STOCK", status="ACTIVE"))
    db.flush()


def _ensure_master(db: Session, code: str) -> None:
    exists = db.execute(
        select(SecurityMaster.id).where(SecurityMaster.market == "CN", SecurityMaster.code == code)
    ).scalar_one_or_none()
    if exists is None:
        _master(db, code)


def _trade(
    db: Session,
    user: User,
    portfolio: Portfolio,
    *,
    side: str,
    quantity: float,
    price: float,
    executed_at: datetime,
    available_at: datetime,
    code: str = "600519",
    fees: float | None = 5.0,
) -> int:
    _ensure_master(db, code)
    entry, _created = create_ledger_entry(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        payload={
            "entry_type": "TRADE",
            "security_code": code,
            "security_name": "贵州茅台",
            "side": side,
            "quantity": quantity,
            "price": price,
            "fees": fees,
            "taxes": 0.0,
            "executed_at": executed_at.isoformat(),
            "available_at": available_at.isoformat(),
            "source": "MANUAL",
        },
    )
    db.flush()
    entry.created_at = available_at.replace(tzinfo=None)
    db.flush()
    return entry.id


def test_sell_after_snapshot_reduces_position_and_settles_cash_next_day():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        baseline = datetime(2026, 10, 8, 1, 30, tzinfo=UTC)  # 09:30 Shanghai
        snapshot = _snapshot(db, user, portfolio, snapshot_time=baseline)
        _trade(
            db, user, portfolio,
            side="SELL", quantity=300, price=10.0,
            executed_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            available_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        )
        same_day = build_account_state(
            db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
        )
        position = same_day["positions"][0]
        assert position["qty"] == 700
        assert position["available_qty"] == 700
        assert same_day["cash"]["available"] == 20_000
        assert same_day["cash"]["pending_sell_proceeds"] == pytest.approx(2_995)

        next_day = build_account_state(
            db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
        )
        assert next_day["cash"]["available"] == pytest.approx(22_995)
        assert next_day["cash"]["pending_sell_proceeds"] == 0
    finally:
        db.close()


def test_buy_after_snapshot_keeps_t1_availability_and_spends_cash():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        baseline = datetime(2026, 10, 8, 1, 30, tzinfo=UTC)
        snapshot = _snapshot(db, user, portfolio, snapshot_time=baseline)
        _trade(
            db, user, portfolio,
            side="BUY", quantity=200, price=10.0,
            executed_at=datetime(2026, 10, 8, 3, 0, tzinfo=UTC),
            available_at=datetime(2026, 10, 8, 3, 0, tzinfo=UTC),
        )
        same_day = build_account_state(
            db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
        )
        position = same_day["positions"][0]
        assert position["qty"] == 1200
        assert position["available_qty"] == 1000
        assert position["open_buy_qty"] == 200
        assert "T1_BUY_SETTLEMENT_PENDING" in position["flags"]
        assert same_day["cash"]["available"] == pytest.approx(20_000 - 2_005)

        next_day = build_account_state(
            db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
        )
        assert next_day["positions"][0]["available_qty"] == 1200
    finally:
        db.close()


def test_new_snapshot_is_the_baseline_and_never_double_counts_older_trades():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        first = datetime(2026, 10, 8, 1, 30, tzinfo=UTC)
        _snapshot(db, user, portfolio, snapshot_time=first)
        _trade(
            db, user, portfolio,
            side="SELL", quantity=300, price=10.0,
            executed_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            available_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        )
        later = _snapshot(
            db, user, portfolio,
            snapshot_time=datetime(2026, 10, 8, 7, 0, tzinfo=UTC),
            holdings=[{
                "code": "600519", "name": "贵州茅台", "qty": 700, "available_qty": 700,
                "market_value": 77_000, "cost": 70_000,
            }],
        )
        state = build_account_state(
            db, portfolio_id=portfolio.id, snapshot=later, as_of=datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
        )
        assert state["positions"][0]["qty"] == 700
        assert state["applied_entry_ids"] == []
        assert state["entry_count"] == 0
    finally:
        db.close()


def test_revise_and_void_change_derived_state_and_account_version():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        baseline = datetime(2026, 10, 8, 1, 30, tzinfo=UTC)
        snapshot = _snapshot(db, user, portfolio, snapshot_time=baseline)
        entry_id = _trade(
            db, user, portfolio,
            side="SELL", quantity=300, price=10.0,
            executed_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            available_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        )
        moment = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
        before = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment)
        assert before["positions"][0]["qty"] == 700

        entry = db.execute(
            select(TradeLedgerEntry).where(TradeLedgerEntry.id == entry_id)
        ).scalar_one()
        revise_ledger_entry(db, entry=entry, user_id=user.id, changes={"quantity": 200}, reason="修正数量")
        revised = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=datetime.now(UTC))
        assert revised["positions"][0]["qty"] == 800
        assert revised["account_version"] != before["account_version"]

        void_ledger_entry(db, entry=entry, user_id=user.id, reason="重复录入")
        voided = build_account_state(db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=datetime.now(UTC))
        assert voided["positions"][0]["qty"] == 1000
        assert voided["account_version"] != revised["account_version"]
        assert voided["entry_count"] == 0
    finally:
        db.close()


def test_sell_beyond_available_is_flagged_not_silently_accepted():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        baseline = datetime(2026, 10, 8, 1, 30, tzinfo=UTC)
        snapshot = _snapshot(db, user, portfolio, snapshot_time=baseline)
        _trade(
            db, user, portfolio,
            side="SELL", quantity=1200, price=10.0,
            executed_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            available_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        )
        state = build_account_state(
            db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
        )
        assert "LEDGER_SELL_EXCEEDS_AVAILABLE:600519" in state["flags"]
        assert "LEDGER_NEGATIVE_POSITION:600519" in state["flags"]
        assert "SELL_EXCEEDS_AVAILABLE" in state["positions"][0]["flags"]
    finally:
        db.close()


def test_portfolio_state_exposes_derived_quantities_and_account_version():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        baseline = datetime(2026, 10, 8, 1, 30, tzinfo=UTC)
        _snapshot(db, user, portfolio, snapshot_time=baseline)
        _master(db, "600519")
        _trade(
            db, user, portfolio,
            side="SELL", quantity=300, price=10.0,
            executed_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            available_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        )
        moment = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
        state = build_portfolio_state(
            db,
            portfolio_id=portfolio.id,
            as_of=moment,
            quote_rows={"quotes": [{"code": "600519", "price": 100, "quality_status": "VALID"}]},
        )
        position = state["positions"][0]
        assert position["qty"] == 700
        assert position["available_qty"] == 700
        assert position["account_source"] == "snapshot+ledger"
        assert state["account_version"].startswith("portfolio-account-v2:")
        assert state["account_derivation"]["applied_entry_ids"]
        assert state["cash"] == 20_000
        assert state["pending_sell_proceeds"] == pytest.approx(2_995)
    finally:
        db.close()


def test_preview_import_maps_headers_detects_duplicates_and_invalid_rows():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        first = preview_import(
            db,
            portfolio_id=portfolio.id,
            content=(
                "成交日期,证券代码,证券名称,买卖方向,成交数量,成交价格,手续费,印花税,合同编号\n"
                "2026-10-02,600519,贵州茅台,买入,100,180.5,5.02,0,HT-001\n"
                "2026-10-02,600519,贵州茅台,买入,100,180.5,5.02,0,HT-001\n"
                "2026-10-02,600519,贵州茅台,买入,100,,5.02,0,HT-002\n"
            ).encode("utf-8-sig"),
        )
        assert first["encoding"] == "utf-8-sig"
        assert first["mapping"]["security_code"] == "证券代码"
        assert first["mapping"]["side"] == "买卖方向"
        statuses = [row["status"] for row in first["rows"]]
        assert statuses == ["READY", "DUPLICATE", "INVALID"]
        duplicate = first["rows"][1]
        assert duplicate["duplicate_kind"] == "IN_FILE"
        assert "missing_price" in [issue["code"] for issue in first["rows"][2]["issues"]]

        payload = dict(first["rows"][0]["normalized"])
        payload["source"] = "CSV_IMPORT"
        payload["source_ref"] = first["source_ref"]
        payload["idempotency_key"] = import_idempotency_key(first["source_ref"], payload)
        entry, created = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=payload)
        db.flush()
        assert created is True
        again, created_again = create_ledger_entry(db, user_id=user.id, portfolio_id=portfolio.id, payload=payload)
        assert created_again is False
        assert again.id == entry.id

        second = preview_import(
            db,
            portfolio_id=portfolio.id,
            content=(
                "成交日期,证券代码,证券名称,买卖方向,成交数量,成交价格,手续费,印花税,合同编号\n"
                "2026-10-02,600519,贵州茅台,买入,100,180.5,5.02,0,HT-001\n"
            ).encode("utf-8"),
        )
        assert second["rows"][0]["status"] == "DUPLICATE"
    finally:
        db.close()


def test_dashboard_validity_expires_when_account_changes_after_advice():
    db = _db()
    try:
        user, portfolio = _portfolio(db)
        baseline = datetime(2026, 10, 8, 1, 30, tzinfo=UTC)
        snapshot = _snapshot(db, user, portfolio, snapshot_time=baseline)
        moment = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
        advice_version = build_account_state(
            db, portfolio_id=portfolio.id, snapshot=snapshot, as_of=moment
        )["account_version"]
        _trade(
            db, user, portfolio,
            side="SELL", quantity=300, price=10.0,
            executed_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
            available_at=datetime(2026, 10, 8, 2, 0, tzinfo=UTC),
        )
        validity = _decision_validity(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            source_snapshot_id=snapshot.id,
            payload={"result": {"account_version": advice_version}},
            cutoff=moment.replace(tzinfo=None),
        )
        assert validity["validity_status"] == "EXPIRED"
        assert validity["validity_reason"] == "PORTFOLIO_ACCOUNT_CHANGED"
        assert validity["account_version"] == advice_version
        assert validity["current_account_version"] != advice_version
    finally:
        db.close()
