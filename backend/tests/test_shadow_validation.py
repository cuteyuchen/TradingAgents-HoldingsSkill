"""Phase N live observation and paper-only shadow execution contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.candidates.models  # noqa: F401
import app.governance.models  # noqa: F401
import app.history.models  # noqa: F401
import app.market_engine_models  # noqa: F401
import app.market_models  # noqa: F401
import app.market_runtime_models  # noqa: F401
import app.memory.models  # noqa: F401
import app.operations.models  # noqa: F401
import app.portfolio_models  # noqa: F401
import app.research.models  # noqa: F401
import app.shadow_models  # noqa: F401
import app.trigger_models  # noqa: F401
import app.v2_models  # noqa: F401

from app.config import settings
from app.database import Base, get_db
from app.market_engine_models import AllAMedianIndexDaily, DailyBarCache
from app.market_models import SecurityMaster, TradingCalendar
from app.portfolio_models import TradeLedgerEntry
from app.shadow.service import (
    capture_live_decision_observation,
    create_shadow_account,
    create_shadow_daily_snapshot,
    ensure_shadow_order_intents,
    evaluate_live_outcomes,
    maintain_shadow,
    persist_live_quote_observation,
    process_pending_shadow_intents,
    refresh_shadow_materialized_state,
    rebase_shadow_account,
    rebuild_shadow_state,
    shadow_account_performance,
    validation_summary,
    _benchmark_return,
    _shadow_equity_at,
)
from app.system.health import shadow_status
from app.shadow_models import (
    LiveDecisionObservation,
    LiveDecisionOutcome,
    LiveQuoteObservation,
    ShadowFill,
    ShadowDailySnapshot,
    ShadowLedgerEntry,
    ShadowOrderIntent,
    ShadowAccount,
    ShadowPosition,
)
from app.v2_dependencies import get_current_user
from app.v2_models import AnalysisJob, AnalysisRun, HoldingItem, Portfolio, PortfolioSnapshot, User


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _calendar(db: Session, days: list[date]) -> None:
    for index, day in enumerate(days):
        db.add(TradingCalendar(
            market="CN",
            trade_date=day,
            is_open=True,
            previous_trade_date=days[index - 1] if index else None,
            next_trade_date=days[index + 1] if index + 1 < len(days) else None,
        ))
    db.flush()


def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _portfolio(
    db: Session,
    *,
    name: str = "测试组合",
    cash: float = 10_000.0,
    holdings: list[dict] | None = None,
    user_email: str = "shadow@example.com",
) -> tuple[User, Portfolio, PortfolioSnapshot]:
    user = User(email=user_email, username=user_email.split("@")[0], password_hash="hash")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name=name, market="A_SHARE", currency="CNY", is_default=True)
    db.add(portfolio)
    db.flush()
    holding_rows = holdings or []
    market_value = sum(float(row.get("market_value") or ((row.get("qty") or 0) * (row.get("price") or row.get("cost") or 0))) for row in holding_rows)
    snapshot = PortfolioSnapshot(
        user_id=user.id,
        portfolio_id=portfolio.id,
        source="manual",
        snapshot_time=datetime(2026, 8, 20, 1, 0),
        created_at=datetime(2026, 8, 20, 1, 1),
        total_assets=cash + market_value,
        total_market_value=market_value,
        broker_available_cash=cash,
        corrected_unused_funds=cash,
        status="confirmed",
        raw_json={"test": True},
    )
    db.add(snapshot)
    db.flush()
    for item in holding_rows:
        db.add(HoldingItem(
            snapshot_id=snapshot.id,
            code=item["code"],
            name=item.get("name") or item["code"],
            market="CN",
            qty=item.get("qty"),
            available_qty=item.get("available_qty", item.get("qty")),
            cost=item.get("cost", item.get("price", 0.0)),
            screenshot_price=item.get("price", item.get("cost", 0.0)),
            market_value=item.get("market_value"),
            extra_json=item.get("extra_json") or {"security_type": item.get("security_type", "STOCK")},
        ))
    db.flush()
    return user, portfolio, snapshot


def _snapshot(
    db: Session,
    *,
    user: User,
    portfolio: Portfolio,
    cash: float,
    snapshot_time: datetime,
    holdings: list[dict] | None = None,
) -> PortfolioSnapshot:
    holding_rows = holdings or []
    market_value = sum(float(row.get("market_value") or ((row.get("qty") or 0) * (row.get("price") or row.get("cost") or 0))) for row in holding_rows)
    row = PortfolioSnapshot(
        user_id=user.id,
        portfolio_id=portfolio.id,
        source="manual",
        snapshot_time=snapshot_time,
        created_at=snapshot_time,
        total_assets=cash + market_value,
        total_market_value=market_value,
        broker_available_cash=cash,
        corrected_unused_funds=cash,
        status="confirmed",
        raw_json={"test": True},
    )
    db.add(row)
    db.flush()
    for item in holding_rows:
        db.add(HoldingItem(
            snapshot_id=row.id,
            code=item["code"],
            name=item.get("name") or item["code"],
            market="CN",
            qty=item.get("qty"),
            available_qty=item.get("available_qty", item.get("qty")),
            cost=item.get("cost", item.get("price", 0.0)),
            screenshot_price=item.get("price", item.get("cost", 0.0)),
            market_value=item.get("market_value"),
            extra_json=item.get("extra_json") or {"security_type": item.get("security_type", "STOCK")},
        ))
    db.flush()
    return row


def test_shadow_health_reports_aggregate_state(db: Session):
    user, portfolio, snapshot = _portfolio(db)
    account = create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
    )
    db.commit()

    result = shadow_status(db)

    assert result["status"] == "OK"
    assert result["schema_installed"] is True
    assert result["active_shadow_accounts"] == 1
    assert result["active_generation_ids"] == [account.shadow_generation]
    assert result["pending_intents"] == 0
    assert result["blocked_intents"] == 0


def _run(
    db: Session,
    *,
    user: User,
    portfolio: Portfolio,
    snapshot: PortfolioSnapshot,
    finished_at: datetime,
    final_action: str = "ACTION",
    actions: list[dict] | None = None,
    workflow: dict | None = None,
    checkpoint: str = "15:10",
    job_id_hint: str = "run",
    market_regime: str = "RISK_ON",
) -> AnalysisRun:
    job = AnalysisJob(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        trigger_type="manual",
        checkpoint=checkpoint,
        mode="deep",
        status="succeeded",
        progress_percent=100,
        current_stage="completed",
        started_at=finished_at - timedelta(minutes=5),
        finished_at=finished_at,
        context_json={"test_job": job_id_hint},
    )
    db.add(job)
    db.flush()
    result = {
        "decision_gate": {"portfolio_action": final_action},
        "today_actions": actions or [],
        "market_regime": market_regime,
        "market_score": 65,
        "market_quality": "VALID",
        "confidence": 0.8,
        "data_quality_grade": "A",
    }
    run = AnalysisRun(
        job_id=job.id,
        user_id=user.id,
        portfolio_snapshot_id=snapshot.id,
        summary="test",
        final_rating=final_action,
        confidence="high",
        structured_result_json={"result": result, "workflow": workflow or {}},
        markdown_text="test",
        parameter_set_version="v1",
        parameter_set_hash="parameter-hash",
        created_at=finished_at,
    )
    db.add(run)
    db.flush()
    return run


def _action(code: str = "600001", *, price: float = 10.0, qty: float = 100.0, side: str = "BUY", security_type: str = "STOCK", action: str | None = None) -> dict:
    return {
        "code": code,
        "name": code,
        "action": action or ("buy" if side == "BUY" else "sell"),
        "side": side,
        "target_qty": qty,
        "reference_price": price,
        "reference_price_basis": "RAW_QUOTE",
        "security_type": security_type,
    }


def _quote(
    db: Session,
    *,
    code: str,
    captured_at: datetime,
    price: float = 10.0,
    precision: str = "EXACT",
    **flags,
) -> LiveQuoteObservation:
    row, _ = persist_live_quote_observation(
        db,
        {
            "code": code,
            "price": price,
            "price_basis": "RAW_QUOTE",
            "quality_status": "VALID",
            "provider": "test",
            "source_ref": f"quote-{code}-{captured_at.isoformat()}-{price}",
            "trade_date": captured_at.date(),
            **flags,
        },
        captured_at=captured_at,
        captured_at_precision=precision,
    )
    db.flush()
    return row


def test_observation_idempotency_and_immutability(db: Session) -> None:
    user, portfolio, snapshot = _portfolio(db)
    finished_at = datetime(2026, 8, 20, 7, 10)
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=finished_at, actions=[_action()])

    first = capture_live_decision_observation(db, run, captured_at=datetime(2026, 8, 20, 7, 11), create_outcomes=False)
    second = capture_live_decision_observation(db, run, captured_at=datetime(2026, 8, 20, 7, 12), create_outcomes=False)
    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert db.scalar(select(LiveDecisionObservation.id).where(LiveDecisionObservation.source_analysis_run_id == run.id)) == first.id

    first.final_action = "NO_ACTION"
    with pytest.raises(RuntimeError, match="live_decision_observations_is_immutable"):
        db.flush()
    db.rollback()


def test_1510_uses_no_same_day_close_and_requires_future_quote(db: Session) -> None:
    day = date(2026, 8, 20)
    next_day = date(2026, 8, 21)
    _calendar(db, [day, next_day])
    user, portfolio, snapshot = _portfolio(db, cash=10_000.0)
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5, 0))
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 7, 10),
        actions=[_action(qty=100)],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    assert observation is not None
    intents = ensure_shadow_order_intents(db, observation, account=account)
    assert len(intents) == 1
    assert intents[0].earliest_executable_at == datetime(2026, 8, 21, 1, 30)

    db.add(DailyBarCache(
        market="CN",
        exchange="SSE",
        code="600001",
        trade_date=day,
        open=10.0,
        high=12.0,
        low=9.0,
        close=12.0,
        adjustment="QFQ",
        provider="test",
        available_at=datetime(2026, 8, 20, 7, 5),
        quality_status="VALID",
    ))
    db.flush()
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 7, 11), price=10.5)
    waiting = process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 7, 20), account_id=account.id)
    assert waiting["fills"] == []
    assert db.get(ShadowOrderIntent, intents[0].id).status == "PENDING"
    assert "WAITING_FOR_FUTURE_QUOTE" in db.get(ShadowOrderIntent, intents[0].id).reason_codes_json

    future = _quote(db, code="600001", captured_at=datetime(2026, 8, 21, 1, 31), price=11.0)
    filled = process_pending_shadow_intents(db, now=datetime(2026, 8, 21, 2, 0), account_id=account.id)
    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intents[0].id))
    assert filled["fills"] == [fill.id]
    assert fill.quote_observation_id == future.id
    assert fill.price == 11.0
    assert fill.quote_captured_at > observation.decision_finalized_at


def test_conditional_add_is_observation_only_until_v1_condition_execution_exists(db: Session) -> None:
    day = date(2026, 8, 20)
    _calendar(db, [day, date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db, cash=10_000.0)
    account = create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 5, 0),
    )
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 6, 0),
        actions=[_action(price=10.0, qty=100.0, action="conditional_add") | {"trigger": {"operator": "lte", "value": 9.5}}],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=True)
    assert observation is not None
    assert "CONDITIONAL_ACTION_EXECUTION_UNSUPPORTED" in observation.final_reason_codes_json
    assert ensure_shadow_order_intents(db, observation, account=account) == []
    assert db.scalar(select(ShadowOrderIntent.id).where(ShadowOrderIntent.decision_observation_id == observation.id)) is None
    assert db.scalar(select(LiveDecisionOutcome.id).where(
        LiveDecisionOutcome.decision_observation_id == observation.id,
        LiveDecisionOutcome.target_type == "SECURITY",
    )) is not None

    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 1), price=9.0)
    result = process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 6, 2), account_id=account.id)
    assert result["fills"] == []
    assert db.scalar(select(ShadowFill.id).where(ShadowFill.shadow_account_id == account.id)) is None


def test_sell_quantity_above_shadow_sellable_is_blocked_without_partial_fill(db: Session) -> None:
    day = date(2026, 8, 20)
    _calendar(db, [day, date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(
        db,
        cash=0.0,
        holdings=[{"code": "600001", "qty": 1000, "available_qty": 600, "price": 10.0, "cost": 10.0}],
    )
    account = create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 5, 0),
    )
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 6, 0),
        actions=[_action(side="SELL", qty=1000.0)],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    assert observation is not None
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 1), price=10.0)

    result = process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 6, 2), account_id=account.id)
    db.refresh(intent)
    assert result["fills"] == []
    assert result["partial"] == 0
    assert result["blocked"] == 1
    assert intent.status == "BLOCKED"
    assert "BLOCKED_BY_SHADOW_SELLABLE_QTY" in intent.reason_codes_json
    assert db.scalar(select(ShadowFill.id).where(ShadowFill.order_intent_id == intent.id)) is None


def test_quote_guards_handle_stale_inexact_suspension_and_limit(db: Session) -> None:
    day = date(2026, 8, 20)
    _calendar(db, [day, date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db, cash=100_000.0)
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5, 0))
    decision_at = datetime(2026, 8, 20, 6, 0)
    specs = [("600001", _action("600001")), ("600002", _action("600002")), ("600003", _action("600003"))]
    observations: dict[str, LiveDecisionObservation] = {}
    for code, action in specs:
        run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=decision_at, actions=[action], job_id_hint=code)
        observation = capture_live_decision_observation(db, run, create_outcomes=False)
        assert observation is not None
        ensure_shadow_order_intents(db, observation, account=account)
        observations[code] = observation

    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 5), price=10.0, suspended=True)
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 6), price=10.2)
    _quote(db, code="600002", captured_at=decision_at, price=10.0)
    _quote(db, code="600002", captured_at=datetime(2026, 8, 20, 6, 7), price=10.0, precision="MINUTE")
    _quote(db, code="600003", captured_at=datetime(2026, 8, 20, 6, 8), price=10.0, limit_up=True)

    result = process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 6, 20), account_id=account.id)
    intent_rows = db.scalars(select(ShadowOrderIntent).where(ShadowOrderIntent.shadow_account_id == account.id)).all()
    by_code = {row.code: row for row in intent_rows}
    assert by_code["600001"].status == "FILLED"
    assert by_code["600002"].status == "PENDING"
    assert "QUOTE_TIME_NOT_EXACT" in by_code["600002"].reason_codes_json
    assert by_code["600003"].status == "BLOCKED"
    assert "BLOCKED_BY_LIMIT_UP" in by_code["600003"].reason_codes_json
    assert result["filled"] == 1


def test_cash_isolation_lot_size_t1_and_phase_e_cost_model(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 8, 20)
    next_day = date(2026, 8, 21)
    _calendar(db, [day, next_day])
    user, portfolio, snapshot = _portfolio(db, cash=5_000.0)
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5, 0))
    monkeypatch.setattr(settings, "PORTFOLIO_BROKER_COMMISSION_BPS", 1.0)
    monkeypatch.setattr(settings, "PORTFOLIO_MINIMUM_COMMISSION", 1.0)
    monkeypatch.setattr(settings, "PORTFOLIO_SELL_TAX_BPS", 5.0)
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 20, 6, 0), actions=[_action(qty=137)])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    assert observation is not None
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 1), price=10.0)
    process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 6, 2), account_id=account.id)

    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    assert fill is not None
    assert fill.quantity == 100
    assert fill.gross_amount == 1_000
    assert fill.commission == 1.0
    assert fill.tax == 0.0
    assert fill.total_cost == 1.0
    assert snapshot.broker_available_cash == 5_000.0
    assert account.current_cash == 3_999.0
    assert rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 20, 6, 2))["positions"]["600001"]["sellable_quantity"] == 0.0
    assert rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 21, 1, 31))["positions"]["600001"]["sellable_quantity"] == 100.0
    assert db.scalar(select(TradeLedgerEntry.id).where(TradeLedgerEntry.portfolio_id == portfolio.id)) is None


def test_unknown_etf_uses_conservative_t1(db: Session) -> None:
    day = date(2026, 8, 20)
    next_day = date(2026, 8, 21)
    _calendar(db, [day, next_day])
    user, portfolio, snapshot = _portfolio(db, cash=10_000.0)
    db.add(SecurityMaster(
        market="CN",
        exchange="SSE",
        code="510300",
        name="测试 ETF",
        security_type="ETF",
        etf_category=None,
        lot_size=100,
        status="ACTIVE",
    ))
    db.flush()
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5, 0))
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 6, 0),
        actions=[_action("510300", qty=100, security_type="ETF")],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    assert observation is not None
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="510300", captured_at=datetime(2026, 8, 20, 6, 1), price=10.0)
    process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 6, 2), account_id=account.id)
    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    assert fill is not None
    entry = db.scalar(select(ShadowLedgerEntry).where(ShadowLedgerEntry.fill_id == fill.id))
    assert entry.payload_json["settlement_policy"] == "T_PLUS_1_CONSERVATIVE"
    assert rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 20, 6, 2))["positions"]["510300"]["sellable_quantity"] == 0.0


def test_no_action_outcome_and_candidate_veto_are_first_class(db: Session) -> None:
    days = _business_days(date(2026, 8, 20), 61)
    _calendar(db, days)
    user, portfolio, snapshot = _portfolio(db, cash=10_000.0)
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5, 0))
    for index, current in enumerate(days):
        db.add(AllAMedianIndexDaily(
            market="CN",
            trade_date=current,
            median_return=-0.05 if index == 1 else 0.0,
            index_value=100.0 - (5.0 if index >= 1 else 0.0),
            eligible_count=1,
            quality_status="VALID",
            available_at=datetime.combine(current, datetime.min.time()).replace(hour=7),
        ))
        db.add(DailyBarCache(
            market="CN",
            exchange="SSE",
            code="600001",
            trade_date=current,
            open=10.0,
            high=12.0,
            low=9.0,
            close=10.0 if index == 0 else 12.0,
            adjustment="QFQ",
            provider="test",
            available_at=datetime.combine(current, datetime.min.time()).replace(hour=6, minute=55),
            quality_status="VALID",
        ))
    db.flush()
    veto = {
        "code": "600001",
        "name": "Veto candidate",
        "stage": "ACTION",
        "reference_price": 10.0,
        "reference_price_basis": "QFQ",
        "security_type": "STOCK",
    }
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 6, 0),
        final_action="NO_ACTION",
        workflow={"candidate_context": {"action": [veto]}},
        market_regime="RISK_OFF",
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=True)
    assert observation is not None
    assert observation.final_action == "NO_ACTION"
    assert db.scalar(select(ShadowOrderIntent.id).where(ShadowOrderIntent.decision_observation_id == observation.id)) is None
    outcomes = db.scalars(select(LiveDecisionOutcome).where(LiveDecisionOutcome.decision_observation_id == observation.id)).all()
    assert {item.target_type for item in outcomes} == {"PORTFOLIO", "CANDIDATE_VETO"}

    create_shadow_daily_snapshot(db, account, trade_date=days[0], as_of=datetime(2026, 8, 20, 7, 0))
    create_shadow_daily_snapshot(db, account, trade_date=days[1], as_of=datetime(2026, 8, 21, 7, 0))

    result = evaluate_live_outcomes(db, as_of=datetime(2026, 8, 21, 8, 0), observation_id=observation.id)
    assert result["completed"] == 2
    portfolio_outcome = next(item for item in outcomes if item.target_type == "PORTFOLIO" and item.horizon_trading_days == 1)
    veto_outcome = next(item for item in outcomes if item.target_type == "CANDIDATE_VETO" and item.horizon_trading_days == 1)
    assert portfolio_outcome.forward_return == pytest.approx(0.0)
    assert portfolio_outcome.excess_return == pytest.approx(0.05)
    assert portfolio_outcome.drawdown_avoided == pytest.approx(0.05)
    assert portfolio_outcome.risk_off_correct is True
    assert veto_outcome.candidate_opportunity_cost == pytest.approx(0.2)
    assert veto_outcome.shadow_filled is False
    assert account.shadow_generation == 1


def test_portfolio_outcome_uses_shadow_equity_and_independent_benchmark(db: Session) -> None:
    day = date(2026, 8, 20)
    target_day = date(2026, 8, 21)
    _calendar(db, [day, target_day])
    user, portfolio, snapshot = _portfolio(db, cash=10_000.0)
    account = create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 5, 0),
    )
    db.add_all([
        AllAMedianIndexDaily(
            market="CN",
            trade_date=day,
            median_return=0.0,
            index_value=100.0,
            eligible_count=1,
            quality_status="VALID",
            available_at=datetime(2026, 8, 20, 7, 5),
        ),
        AllAMedianIndexDaily(
            market="CN",
            trade_date=target_day,
            median_return=0.01,
            index_value=101.0,
            eligible_count=1,
            quality_status="VALID",
            available_at=datetime(2026, 8, 21, 7, 5),
        ),
        ShadowDailySnapshot(
            shadow_account_id=account.id,
            shadow_generation=1,
            trade_date=day,
            cash=10_000.0,
            market_value=0.0,
            total_equity=10_000.0,
        ),
        ShadowDailySnapshot(
            shadow_account_id=account.id,
            shadow_generation=1,
            trade_date=target_day,
            cash=10_400.0,
            market_value=0.0,
            total_equity=10_400.0,
        ),
    ])
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 6, 0),
        actions=[],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=True)
    assert observation is not None

    result = evaluate_live_outcomes(db, as_of=datetime(2026, 8, 21, 8, 0), observation_id=observation.id)
    assert result["completed"] == 1
    outcome = db.scalar(select(LiveDecisionOutcome).where(
        LiveDecisionOutcome.decision_observation_id == observation.id,
        LiveDecisionOutcome.target_type == "PORTFOLIO",
        LiveDecisionOutcome.horizon_trading_days == 1,
    ))
    assert outcome is not None
    assert outcome.forward_return == pytest.approx(0.04)
    assert outcome.benchmark_return == pytest.approx(0.01)
    assert outcome.excess_return == pytest.approx(0.03)


def test_shadow_reference_equity_prefers_as_of_quote_over_unknown_same_day_bar(db: Session) -> None:
    day = date(2026, 8, 20)
    decision_at = datetime(2026, 8, 20, 2, 30)
    _calendar(db, [day])
    user, portfolio, snapshot = _portfolio(
        db,
        cash=20_000.0,
        holdings=[{"code": "600001", "qty": 1000, "available_qty": 1000, "price": 10.0, "cost": 10.0}],
    )
    account = create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 1, 1),
    )
    db.add(DailyBarCache(
        market="CN",
        exchange="SSE",
        code="600001",
        trade_date=day,
        close=12.0,
        adjustment="QFQ",
        provider="test",
        available_at=None,
        quality_status="VALID",
    ))
    db.flush()
    quote = _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 29), price=10.0)

    equity, evidence = _shadow_equity_at(db, account, generation=1, as_of=decision_at)

    assert equity == pytest.approx(30_000.0)
    assert evidence["status"] == "VALID"
    assert evidence["marks"]["600001"]["quote_observation_id"] == quote.id
    assert evidence["marks"]["600001"].get("daily_bar_id") is None


def test_shadow_reference_equity_without_visible_mark_keeps_portfolio_outcome_pending(db: Session) -> None:
    day = date(2026, 8, 20)
    target_day = date(2026, 8, 21)
    decision_at = datetime(2026, 8, 20, 2, 30)
    _calendar(db, [day, target_day])
    user, portfolio, snapshot = _portfolio(
        db,
        cash=20_000.0,
        holdings=[{"code": "600001", "qty": 1000, "available_qty": 1000, "price": 10.0, "cost": 10.0}],
    )
    account = create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 1, 1),
    )
    db.add_all([
        DailyBarCache(
            market="CN",
            exchange="SSE",
            code="600001",
            trade_date=day,
            close=12.0,
            adjustment="QFQ",
            provider="test",
            available_at=None,
            quality_status="VALID",
        ),
        AllAMedianIndexDaily(
            market="CN",
            trade_date=day,
            median_return=0.0,
            index_value=100.0,
            eligible_count=1,
            quality_status="VALID",
            available_at=datetime(2026, 8, 20, 7, 0),
        ),
        AllAMedianIndexDaily(
            market="CN",
            trade_date=target_day,
            median_return=0.01,
            index_value=101.0,
            eligible_count=1,
            quality_status="VALID",
            available_at=datetime(2026, 8, 21, 7, 0),
        ),
        ShadowDailySnapshot(
            shadow_account_id=account.id,
            shadow_generation=1,
            trade_date=target_day,
            cash=30_000.0,
            market_value=0.0,
            total_equity=30_000.0,
        ),
    ])
    db.flush()
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=decision_at,
        actions=[],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=True)
    assert observation is not None

    result = evaluate_live_outcomes(db, as_of=datetime(2026, 8, 21, 8, 0), observation_id=observation.id)
    outcome = db.scalar(select(LiveDecisionOutcome).where(
        LiveDecisionOutcome.decision_observation_id == observation.id,
        LiveDecisionOutcome.target_type == "PORTFOLIO",
        LiveDecisionOutcome.horizon_trading_days == 1,
    ))

    assert result["completed"] == 0
    assert outcome is not None
    assert outcome.status == "PENDING"
    assert outcome.quality_status == "DATA_GAP"
    assert outcome.forward_return is None
    assert outcome.excess_return is None
    assert outcome.source_refs_json["portfolio_equity"]["reference"]["status"] == "SHADOW_MARK_DATA_GAP"


def test_benchmark_as_of_requires_known_available_at(db: Session) -> None:
    day = date(2026, 8, 20)
    target_day = date(2026, 8, 21)
    db.add_all([
        AllAMedianIndexDaily(
            market="CN",
            trade_date=day,
            median_return=0.0,
            index_value=100.0,
            eligible_count=1,
            quality_status="VALID",
            available_at=None,
        ),
        AllAMedianIndexDaily(
            market="CN",
            trade_date=target_day,
            median_return=0.01,
            index_value=101.0,
            eligible_count=1,
            quality_status="VALID",
            available_at=None,
        ),
    ])
    db.flush()

    assert _benchmark_return(db, day, target_day, as_of=datetime(2026, 8, 21, 8, 0)) is None


def test_execution_eligibility_requires_intent_and_future_quote(db: Session) -> None:
    day = date(2026, 8, 20)
    target_day = date(2026, 8, 21)
    _calendar(db, [day, target_day])
    user, portfolio, snapshot = _portfolio(db, cash=10_000.0)
    create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 5, 0),
    )
    db.add_all([
        AllAMedianIndexDaily(
            market="CN",
            trade_date=day,
            median_return=0.0,
            index_value=100.0,
            eligible_count=1,
            quality_status="VALID",
            available_at=datetime(2026, 8, 20, 7, 5),
        ),
        AllAMedianIndexDaily(
            market="CN",
            trade_date=target_day,
            median_return=0.1,
            index_value=110.0,
            eligible_count=1,
            quality_status="VALID",
            available_at=datetime(2026, 8, 21, 7, 5),
        ),
        DailyBarCache(
            market="CN",
            exchange="SSE",
            code="600001",
            trade_date=target_day,
            open=11.0,
            high=11.0,
            low=11.0,
            close=11.0,
            adjustment="QFQ",
            provider="test",
            available_at=datetime(2026, 8, 21, 7, 0),
            quality_status="VALID",
        ),
    ])
    db.flush()
    action = _action(price=10.0, qty=100.0)
    action["reference_price_basis"] = "QFQ"
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 6, 0),
        actions=[action],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=True)
    assert observation is not None
    intent = db.scalar(select(ShadowOrderIntent).where(ShadowOrderIntent.decision_observation_id == observation.id))
    assert intent is not None

    evaluate_live_outcomes(db, as_of=datetime(2026, 8, 21, 8, 0), observation_id=observation.id)
    outcome = db.scalar(select(LiveDecisionOutcome).where(
        LiveDecisionOutcome.decision_observation_id == observation.id,
        LiveDecisionOutcome.target_type == "SECURITY",
        LiveDecisionOutcome.horizon_trading_days == 1,
    ))
    assert outcome is not None
    assert outcome.execution_eligible is False
    assert outcome.source_refs_json["execution"]["status"] == "WAITING_FOR_FUTURE_QUOTE"


def test_shadow_snapshot_does_not_publish_equity_when_position_mark_is_missing(db: Session) -> None:
    day = date(2026, 8, 20)
    _calendar(db, [day])
    user, portfolio, snapshot = _portfolio(
        db,
        cash=20_000.0,
        holdings=[{"code": "600001", "qty": 1000, "available_qty": 1000, "price": 80.0, "cost": 80.0}],
    )
    account = create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 5, 0),
    )

    with pytest.raises(RuntimeError, match="SHADOW_MARK_DATA_GAP"):
        create_shadow_daily_snapshot(db, account, trade_date=day, as_of=datetime(2026, 8, 20, 7, 5))
    assert db.scalar(select(ShadowDailySnapshot.id).where(
        ShadowDailySnapshot.shadow_account_id == account.id,
        ShadowDailySnapshot.trade_date == day,
    )) is None

    maintenance = maintain_shadow(db, as_of=datetime(2026, 8, 20, 7, 5), trade_date=day)
    assert maintenance["degraded"] is True
    assert maintenance["snapshot_errors"][0]["reason"].startswith("SHADOW_MARK_DATA_GAP")


def test_shadow_performance_does_not_treat_missing_benchmark_as_zero(db: Session) -> None:
    user, portfolio, snapshot = _portfolio(db, cash=10_000.0)
    account = create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 5, 0),
    )
    db.add_all([
        ShadowDailySnapshot(
            shadow_account_id=account.id,
            shadow_generation=1,
            trade_date=date(2026, 8, 20),
            cash=10_000.0,
            total_equity=10_000.0,
            benchmark_return=0.01,
        ),
        ShadowDailySnapshot(
            shadow_account_id=account.id,
            shadow_generation=1,
            trade_date=date(2026, 8, 21),
            cash=10_100.0,
            total_equity=10_100.0,
            benchmark_return=None,
        ),
    ])
    db.flush()

    performance = shadow_account_performance(db, account)
    assert performance["benchmark_return"] is None
    assert performance["excess_return"] is None
    assert performance["performance_quality"] == "DATA_GAP"


def test_validation_summary_separates_target_and_horizon_metrics(db: Session) -> None:
    user, portfolio, snapshot = _portfolio(db, cash=10_000.0)
    create_shadow_account(
        db,
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 5, 0),
    )
    run = _run(
        db,
        user=user,
        portfolio=portfolio,
        snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 6, 0),
        actions=[_action()],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=True)
    assert observation is not None
    outcomes = db.scalars(select(LiveDecisionOutcome).where(
        LiveDecisionOutcome.decision_observation_id == observation.id,
    )).all()
    for outcome in outcomes:
        outcome.status = "COMPLETED"
        outcome.excess_return = outcome.horizon_trading_days / 1000.0
    db.flush()

    summary = validation_summary(db, user_id=user.id, portfolio_id=portfolio.id)
    cohort = summary["cohorts"][0]
    buckets = cohort["outcomes_by_target_horizon"]
    assert cohort["completed_outcome_count"] == 10
    assert "mean_excess_return" not in cohort
    assert len(buckets) == 10
    assert {(
        item["target_type"],
        item["target_key"],
        item["horizon_trading_days"],
    ) for item in buckets} == {
        ("PORTFOLIO", "PORTFOLIO", horizon) for horizon in (1, 5, 10, 20, 60)
    } | {
        ("SECURITY", "600001", horizon) for horizon in (1, 5, 10, 20, 60)
    }


def test_rebase_preserves_generation_and_supersedes_old_intent(db: Session) -> None:
    day = date(2026, 8, 20)
    _calendar(db, [day, date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db, cash=1_000.0)
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5, 0))
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 20, 6, 0), actions=[_action(qty=100)])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    assert observation is not None
    old_intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    new_snapshot = _snapshot(
        db,
        user=user,
        portfolio=portfolio,
        cash=2_000.0,
        snapshot_time=datetime(2026, 8, 21, 6, 0),
        holdings=[{"code": "600002", "qty": 100, "available_qty": 100, "cost": 20.0, "price": 20.0, "market_value": 2_000.0}],
    )
    rebase_shadow_account(db, account, snapshot_id=new_snapshot.id, now=datetime(2026, 8, 21, 6, 5))
    assert account.shadow_generation == 2
    assert account.current_cash == 2_000.0
    assert rebuild_shadow_state(db, account, generation=1)["cash"] == 1_000.0
    assert rebuild_shadow_state(db, account, generation=2)["cash"] == 2_000.0
    assert rebuild_shadow_state(db, account, generation=2)["positions"]["600002"]["quantity"] == 100
    assert db.scalar(select(ShadowLedgerEntry.id).where(ShadowLedgerEntry.shadow_generation == 1)) is not None
    from app.shadow.service import shadow_account_performance

    assert shadow_account_performance(db, account, generation=1)["current_cash"] == 1_000.0
    assert shadow_account_performance(db, account, generation=2)["current_cash"] == 2_000.0

    process_pending_shadow_intents(db, now=datetime(2026, 8, 21, 6, 10), account_id=account.id)
    assert db.get(ShadowOrderIntent, old_intent.id).status == "SUPERSEDED"


def test_shadow_api_is_owner_scoped_and_has_no_manual_order_write(db: Session) -> None:
    from fastapi.testclient import TestClient

    import app.main as main_module

    user_a, portfolio_a, snapshot_a = _portfolio(db, name="A组合", user_email="a-shadow@example.com")
    user_b, portfolio_b, snapshot_b = _portfolio(db, name="B组合", user_email="b-shadow@example.com")
    account_a = create_shadow_account(db, user_id=user_a.id, portfolio_id=portfolio_a.id, snapshot_id=snapshot_a.id, now=datetime(2026, 8, 20, 5, 0))
    account_b = create_shadow_account(db, user_id=user_b.id, portfolio_id=portfolio_b.id, snapshot_id=snapshot_b.id, now=datetime(2026, 8, 20, 5, 0))
    db.commit()

    def override_db():
        yield db

    main_module.app.dependency_overrides[get_db] = override_db
    main_module.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_a.id, status="active")
    client = TestClient(main_module.app)
    try:
        listed = client.get("/api/v3/shadow/accounts")
        assert listed.status_code == 200
        assert {row["id"] for row in listed.json()} == {account_a.id}
        assert client.get(f"/api/v3/shadow/accounts/{account_b.id}").status_code == 404
        assert client.get(f"/api/v3/shadow/accounts?portfolio_id={portfolio_b.id}").status_code == 404
        assert client.post("/api/v3/shadow/orders", json={}).status_code in {404, 405}
    finally:
        main_module.app.dependency_overrides.pop(get_db, None)
        main_module.app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    ("with_holdings", "sizing_mode", "expected_quantity"),
    [(False, "weight", 500), (True, "weight", 500), (True, "quantity", 100), (True, "notional", 100)],
)
def test_shadow_sizing_and_cap_share_complete_execution_time_equity(
    db: Session, with_holdings: bool, sizing_mode: str, expected_quantity: int,
) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(
        db,
        cash=1_000.0 if with_holdings else 10_000.0,
        holdings=[{"code": "600001", "qty": 100, "price": 90.0}] if with_holdings else [],
    )
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5))
    action = _action("600002", price=1.0)
    if sizing_mode != "quantity":
        action.pop("target_qty")
        action["target_weight" if sizing_mode == "weight" else "target_notional"] = 0.05 if sizing_mode == "weight" else 100.0
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 20, 6), actions=[action])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    if with_holdings:
        _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6), price=90.0)
        # A later materialized mark cannot replace the execution-time mark.
        _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 5), price=0.01)
        refresh_shadow_materialized_state(db, account, as_of=datetime(2026, 8, 20, 6, 5))
    executable = _quote(db, code="600002", captured_at=datetime(2026, 8, 20, 6, 1), price=1.0)

    result = process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 6, 2), account_id=account.id)
    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    assert result["filled"] == 1
    assert fill.quantity == expected_quantity
    assert fill.quote_observation_id == executable.id


@pytest.mark.parametrize("recovery_mode", ["historical_backfill", "later_quote"])
def test_shadow_weight_sizing_waits_for_missing_holding_mark(db: Session, recovery_mode: str) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db, cash=1_000.0, holdings=[{"code": "600001", "qty": 100, "price": 90.0}])
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5))
    action = _action("600002", price=1.0)
    action.pop("target_qty")
    action["target_weight"] = 0.05
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 20, 6), actions=[action])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 5), price=90.0)
    executable = _quote(db, code="600002", captured_at=datetime(2026, 8, 20, 6, 1), price=1.0)

    missing = process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 6, 2), account_id=account.id)
    assert missing["fills"] == []
    assert intent.status == "PENDING"
    assert "SHADOW_MARK_DATA_GAP" in intent.reason_codes_json
    if recovery_mode == "historical_backfill":
        _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6), price=90.0)
        recovered_at = datetime(2026, 8, 20, 6, 2)
        expected_quantity = 500
    else:
        # A later complete quote may recover the intent, but its marks cannot
        # make the earlier execution time appear complete.
        _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 2), price=90.0)
        executable = _quote(db, code="600002", captured_at=datetime(2026, 8, 20, 6, 3), price=1.25)
        recovered_at = datetime(2026, 8, 20, 6, 4)
        expected_quantity = 400
    recovered = process_pending_shadow_intents(db, now=recovered_at, account_id=account.id)
    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    assert recovered["filled"] == 1
    assert fill.quantity == expected_quantity
    assert fill.quote_observation_id == executable.id
    assert fill.fill_at == executable.captured_at


@pytest.mark.parametrize(("side", "target_weight"), [("BUY", 0.05), ("SELL", 0.03)])
def test_shadow_weight_sizing_adjusts_existing_position(db: Session, side: str, target_weight: float) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db, cash=9_600.0, holdings=[{"code": "600001", "qty": 400, "price": 1.0}])
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 20, 5))
    action = _action(price=1.0, side=side)
    action.pop("target_qty")
    action["target_weight"] = target_weight
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 20, 6), actions=[action])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 6, 1), price=1.0)

    assert process_pending_shadow_intents(db, now=datetime(2026, 8, 20, 6, 2), account_id=account.id)["filled"] == 1
    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    assert fill.quantity == 100


@pytest.mark.parametrize(
    ("day", "next_day"),
    [
        (date(2026, 8, 21), date(2026, 8, 24)),
        (date(2026, 9, 30), date(2026, 10, 8)),
        (date(2026, 12, 31), date(2027, 1, 4)),
    ],
)
def test_shadow_next_session_requires_calendar_across_gaps(db: Session, day: date, next_day: date) -> None:
    from app.shadow.service import _next_trading_close, _next_trading_open

    _calendar(db, [day])
    assert _next_trading_open(db, day) is None
    assert _next_trading_close(db, day) is None
    _calendar(db, [next_day])
    assert _next_trading_open(db, day) == datetime.combine(next_day, datetime.min.time()).replace(hour=1, minute=30)
    assert _next_trading_close(db, day) == datetime.combine(next_day, datetime.min.time()).replace(hour=7)


@pytest.mark.parametrize("recover_after_expiry", [False, True])
def test_shadow_missing_calendar_recovers_only_original_valid_window(
    db: Session, recover_after_expiry: bool,
) -> None:
    from app.routers.shadow_v3 import _intent_payload

    friday, monday, tuesday = date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25)
    _calendar(db, [friday])
    user, portfolio, snapshot = _portfolio(db)
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 21, 5))
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 21, 7, 10), actions=[_action()])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 22, 1, 31))
    missing = process_pending_shadow_intents(db, now=datetime(2026, 8, 22, 2), account_id=account.id)
    assert missing["fills"] == []
    assert intent.status == "PENDING"
    assert "SHADOW_CALENDAR_DATA_GAP" in intent.reason_codes_json
    assert _intent_payload(intent)["earliest_executable_at"] is None
    assert _intent_payload(intent)["expires_at"] is None

    _calendar(db, [monday])
    quote = _quote(db, code="600001", captured_at=datetime(2026, 8, 24, 1, 31))
    recovery_time = datetime(2026, 8, 25 if recover_after_expiry else 24, 2)
    recovered = process_pending_shadow_intents(db, now=recovery_time, account_id=account.id)
    assert intent.earliest_executable_at == datetime(2026, 8, 24, 1, 30)
    assert intent.expires_at == datetime(2026, 8, 24, 7)
    assert _intent_payload(intent)["earliest_executable_at"] == "2026-08-24T01:30:00"
    assert _intent_payload(intent)["expires_at"] == "2026-08-24T07:00:00"
    if recover_after_expiry:
        assert recovered["expired"] == 1
        assert intent.status == "EXPIRED"
        assert db.scalar(select(ShadowFill.id).where(ShadowFill.order_intent_id == intent.id)) is None
        return

    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    entry = db.scalar(select(ShadowLedgerEntry).where(ShadowLedgerEntry.fill_id == fill.id))
    assert recovered["filled"] == 1
    assert fill.quote_observation_id == quote.id
    assert entry.sellable_at is None
    assert entry.payload_json["settlement_calendar_pending"] is True
    assert rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 24, 2))["positions"]["600001"]["sellable_quantity"] == 0
    frozen_fill = (fill.fill_at, fill.price, fill.quantity, fill.execution_key)

    _calendar(db, [tuesday])
    assert rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 25, 1, 31))["positions"]["600001"]["sellable_quantity"] == 100
    assert process_pending_shadow_intents(db, now=datetime(2026, 8, 25, 2), account_id=account.id)["checked"] == 0
    assert (fill.fill_at, fill.price, fill.quantity, fill.execution_key) == frozen_fill
    assert entry.sellable_at is None


def test_shadow_quote_without_open_calendar_day_cannot_fill(db: Session) -> None:
    _calendar(db, [date(2026, 8, 21), date(2026, 8, 24)])
    user, portfolio, snapshot = _portfolio(db)
    account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=datetime(2026, 8, 21, 5))
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 21, 6), actions=[_action()])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 22, 1, 31))

    assert process_pending_shadow_intents(db, now=datetime(2026, 8, 22, 2), account_id=account.id)["fills"] == []
    assert intent.status == "PENDING"
    assert "SHADOW_CALENDAR_DATA_GAP" in intent.reason_codes_json
    db.add(TradingCalendar(market="CN", trade_date=date(2026, 8, 22), is_open=False))
    db.flush()
    assert process_pending_shadow_intents(db, now=datetime(2026, 8, 22, 2), account_id=account.id)["fills"] == []
    assert "QUOTE_OUTSIDE_TRADING_DAY" in intent.reason_codes_json


@pytest.mark.parametrize("operation", ["create", "rebase"])
@pytest.mark.parametrize("case", ["unverified", "mismatched_confirmation", "future_fact", "late_ingestion"])
def test_shadow_initialization_requires_trusted_visible_snapshot(
    db: Session, operation: str, case: str,
) -> None:
    user, portfolio, snapshot = _portfolio(db)
    moment = datetime(2026, 8, 20, 2)
    account = None
    if operation == "rebase":
        account = create_shadow_account(
            db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=moment,
        )
        account.execution_contract_version = "shadow-execution-v1"
        before = (
            account.shadow_generation, account.version, account.initialized_from_snapshot_id,
            account.initialized_at, account.current_cash, account.config_json, account.execution_contract_version,
        )
        snapshot = _snapshot(
            db, user=user, portfolio=portfolio, cash=20_000,
            snapshot_time=datetime(2026, 8, 20, 1, 10),
        )
    if case in {"unverified", "mismatched_confirmation"}:
        snapshot.source = "screenshot"
        if case == "mismatched_confirmation":
            snapshot.raw_json = {
                "observed_at": "2026-08-20T09:20:00+08:00",
                "observation_time_status": "USER_CONFIRMED",
            }
    elif case == "future_fact":
        snapshot.snapshot_time = moment + timedelta(seconds=1)
    else:
        snapshot.created_at = moment + timedelta(seconds=1)
    db.flush()
    ledger_ids = list(db.scalars(select(ShadowLedgerEntry.id)))

    with pytest.raises(ValueError):
        if account is None:
            create_shadow_account(
                db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=moment,
            )
        else:
            rebase_shadow_account(db, account, snapshot_id=snapshot.id, now=moment)
    assert list(db.scalars(select(ShadowLedgerEntry.id))) == ledger_ids
    if account is None:
        assert db.scalar(select(ShadowAccount.id)) is None
    else:
        assert (
            account.shadow_generation, account.version, account.initialized_from_snapshot_id,
            account.initialized_at, account.current_cash, account.config_json, account.execution_contract_version,
        ) == before


@pytest.mark.parametrize("operation", ["create", "rebase"])
@pytest.mark.parametrize("source", ["manual", "confirmed_screenshot"])
def test_shadow_snapshot_cutoffs_are_inclusive_and_confirmation_is_verified(
    db: Session, operation: str, source: str,
) -> None:
    user, portfolio, snapshot = _portfolio(db)
    moment = datetime(2026, 8, 20, 2)
    account = None
    if operation == "rebase":
        account = create_shadow_account(
            db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=moment,
        )
        snapshot = _snapshot(db, user=user, portfolio=portfolio, cash=20_000, snapshot_time=moment)
    snapshot.snapshot_time = moment
    snapshot.created_at = moment
    if source == "confirmed_screenshot":
        snapshot.source = "screenshot"
        snapshot.raw_json = {
            "observed_at": moment.replace(tzinfo=UTC).isoformat(),
            "observation_time_status": "USER_CONFIRMED",
        }
    db.flush()

    if account is None:
        account = create_shadow_account(
            db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=moment,
        )
    else:
        rebase_shadow_account(db, account, snapshot_id=snapshot.id, now=moment)
    assert account.initialized_from_snapshot_id == snapshot.id
    assert account.current_cash == snapshot.broker_available_cash
    assert account.execution_contract_version == "shadow-execution-v2"


@pytest.mark.parametrize("operation", ["create", "rebase"])
def test_shadow_automatic_snapshot_selection_skips_untrusted_and_invisible_latest(
    db: Session, operation: str,
) -> None:
    user, portfolio, trusted = _portfolio(db)
    moment = datetime(2026, 8, 20, 2)
    account = None
    if operation == "rebase":
        account = create_shadow_account(
            db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=trusted.id, now=moment,
        )
    untrusted = _snapshot(
        db, user=user, portfolio=portfolio, cash=20_000,
        snapshot_time=datetime(2026, 8, 20, 1, 10),
    )
    untrusted.source = "screenshot"
    late = _snapshot(
        db, user=user, portfolio=portfolio, cash=30_000,
        snapshot_time=datetime(2026, 8, 20, 1, 20),
    )
    late.created_at = moment + timedelta(seconds=1)
    _snapshot(db, user=user, portfolio=portfolio, cash=40_000, snapshot_time=moment + timedelta(seconds=1))
    db.flush()

    if account is None:
        account = create_shadow_account(db, user_id=user.id, portfolio_id=portfolio.id, now=moment)
    else:
        rebase_shadow_account(db, account, now=moment)
    assert account.initialized_from_snapshot_id == trusted.id
    assert account.current_cash == 10_000


@pytest.mark.parametrize("operation", ["create", "rebase"])
@pytest.mark.parametrize(
    ("field", "invalid"),
    [("cash", None), ("cash", -1), ("cash", float("inf")), ("quantity", None), ("quantity", -1), ("quantity", float("inf"))],
)
def test_shadow_unknown_or_invalid_base_facts_do_not_mutate_account(
    db: Session, operation: str, field: str, invalid: float | None,
) -> None:
    user, portfolio, snapshot = _portfolio(db, holdings=[{"code": "600001", "qty": 100, "price": 10}])
    moment = datetime(2026, 8, 20, 2)
    account = None
    if operation == "rebase":
        account = create_shadow_account(
            db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=moment,
        )
        account.execution_contract_version = "shadow-execution-v1"
        before = (
            account.shadow_generation, account.version, account.initialized_from_snapshot_id,
            account.initialized_at, account.current_cash, account.config_json, account.execution_contract_version,
        )
        snapshot = _snapshot(
            db, user=user, portfolio=portfolio, cash=20_000,
            snapshot_time=datetime(2026, 8, 20, 1, 10),
            holdings=[{"code": "600001", "qty": 100, "price": 10}],
        )
    if field == "cash":
        snapshot.broker_available_cash = invalid
    else:
        snapshot.holdings[0].qty = invalid
    db.flush()
    ledger_ids = list(db.scalars(select(ShadowLedgerEntry.id)))

    with pytest.raises(ValueError, match=f"shadow_base_{field}_unknown_or_invalid"):
        if account is None:
            create_shadow_account(
                db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=moment,
            )
        else:
            rebase_shadow_account(db, account, snapshot_id=snapshot.id, now=moment)
    assert list(db.scalars(select(ShadowLedgerEntry.id))) == ledger_ids
    if account is None:
        assert db.scalar(select(ShadowAccount.id)) is None
    else:
        assert (
            account.shadow_generation, account.version, account.initialized_from_snapshot_id,
            account.initialized_at, account.current_cash, account.config_json, account.execution_contract_version,
        ) == before


@pytest.mark.parametrize("operation", ["create", "rebase"])
def test_shadow_excess_initial_availability_cannot_authorize_sell(db: Session, operation: str) -> None:
    from app.routers.shadow_v3 import _position_payload

    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, baseline = _portfolio(db)
    invalid = _snapshot(
        db, user=user, portfolio=portfolio, cash=10_000, snapshot_time=datetime(2026, 8, 20, 1, 10),
        holdings=[{"code": "600001", "qty": 100, "available_qty": 200, "price": 10}],
    )
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id,
        snapshot_id=invalid.id if operation == "create" else baseline.id,
        now=datetime(2026, 8, 20, 1, 30),
    )
    if operation == "rebase":
        rebase_shadow_account(db, account, snapshot_id=invalid.id, now=datetime(2026, 8, 20, 1, 30))
    initial_state = rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 20, 2))["positions"]["600001"]
    seed_facts = [(entry.id, entry.payload_json) for entry in db.scalars(select(ShadowLedgerEntry))]
    run = _run(
        db, user=user, portfolio=portfolio, snapshot=invalid,
        finished_at=datetime(2026, 8, 20, 2), actions=[_action(side="SELL")],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 1))

    result = process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 2))
    assert result["filled"] == 0
    assert intent.status == "PENDING"
    assert "SHADOW_BASE_FACTS_DATA_GAP" in intent.reason_codes_json
    assert initial_state["sellable_quantity"] == 0
    assert initial_state["sellable_quantity_known"] is False
    materialized = db.scalar(select(ShadowPosition).where(
        ShadowPosition.shadow_account_id == account.id,
        ShadowPosition.shadow_generation == account.shadow_generation,
    ))
    payload = _position_payload(materialized)
    assert payload["sellable_quantity"] is None
    assert payload["sellable_quantity_minimum"] == 0
    assert invalid.holdings[0].available_qty == 200
    assert invalid.holdings[0].qty == 100
    assert [(db.get(ShadowLedgerEntry, entry_id).id, db.get(ShadowLedgerEntry, entry_id).payload_json) for entry_id, _ in seed_facts] == seed_facts


@pytest.mark.parametrize("operation", ["create", "rebase"])
@pytest.mark.parametrize("available", [0, 50, 100])
def test_shadow_initial_sellable_accepts_only_the_proven_in_range_quantity(
    db: Session, operation: str, available: float,
) -> None:
    from app.routers.shadow_v3 import _position_payload

    user, portfolio, baseline = _portfolio(db)
    snapshot = _snapshot(
        db, user=user, portfolio=portfolio, cash=10_000, snapshot_time=datetime(2026, 8, 20, 1, 10),
        holdings=[{"code": "600001", "qty": 100, "available_qty": available, "price": 10}],
    )
    moment = datetime(2026, 8, 20, 2)
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id,
        snapshot_id=snapshot.id if operation == "create" else baseline.id,
        now=moment,
    )
    if operation == "rebase":
        rebase_shadow_account(db, account, snapshot_id=snapshot.id, now=moment)
    position = rebuild_shadow_state(db, account, as_of=moment)["positions"]["600001"]
    assert position["quantity"] == 100
    assert position["sellable_quantity"] == available
    assert position["sellable_quantity_known"] is True
    materialized = db.scalar(select(ShadowPosition).where(
        ShadowPosition.shadow_account_id == account.id,
        ShadowPosition.shadow_generation == account.shadow_generation,
    ))
    assert _position_payload(materialized)["sellable_quantity"] == available
    assert snapshot.holdings[0].available_qty == available


@pytest.mark.parametrize("available", [None, -1, 200, float("inf")])
def test_shadow_unknown_sellable_is_not_a_known_zero_and_requires_new_base_facts(
    db: Session, available: float | None,
) -> None:
    from app.routers.shadow_v3 import _position_payload

    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(
        db, holdings=[{"code": "600001", "qty": 100, "available_qty": available, "price": 10}],
    )
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 1, 30),
    )
    position = rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 20, 2))["positions"]["600001"]
    assert position["sellable_quantity"] == 0
    assert position["sellable_quantity_known"] is False
    materialized = db.scalar(select(ShadowPosition).where(ShadowPosition.shadow_account_id == account.id))
    payload = _position_payload(materialized)
    assert payload["sellable_quantity"] is None
    assert payload["sellable_quantity_minimum"] == 0
    assert payload["metadata"]["sellable_quantity_known"] is False
    run = _run(
        db, user=user, portfolio=portfolio, snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 2), actions=[_action(side="SELL")],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 1))

    result = process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 2))
    assert result["fills"] == []
    assert intent.status == "PENDING"
    assert "SHADOW_BASE_FACTS_DATA_GAP" in intent.reason_codes_json
    initial_entries = [(entry.id, entry.payload_json) for entry in db.scalars(select(ShadowLedgerEntry))]

    complete = _snapshot(
        db, user=user, portfolio=portfolio, cash=10_000, snapshot_time=datetime(2026, 8, 20, 2, 3),
        holdings=[{"code": "600001", "qty": 100, "available_qty": 100, "price": 10}],
    )
    rebase_shadow_account(db, account, snapshot_id=complete.id, now=datetime(2026, 8, 20, 2, 3))
    new_run = _run(
        db, user=user, portfolio=portfolio, snapshot=complete,
        finished_at=datetime(2026, 8, 20, 2, 4), actions=[_action(side="SELL")],
    )
    new_observation = capture_live_decision_observation(db, new_run, create_outcomes=False)
    new_intent = ensure_shadow_order_intents(db, new_observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 5))
    recovered = process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 6))
    assert recovered["filled"] == 1
    assert new_intent.status == "FILLED"
    assert intent.status == "SUPERSEDED"
    assert [(db.get(ShadowLedgerEntry, entry_id).id, db.get(ShadowLedgerEntry, entry_id).payload_json) for entry_id, _ in initial_entries] == initial_entries


def test_shadow_known_zero_cash_and_quantity_are_not_unknown(db: Session) -> None:
    user, portfolio, snapshot = _portfolio(db, cash=0, holdings=[{"code": "600001", "qty": 0, "available_qty": 0}])
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 2),
    )
    state = rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 20, 2))
    assert state["cash"] == 0
    assert state["positions"] == {}


@pytest.mark.parametrize("sellable", [None, -1, 200, float("inf")])
@pytest.mark.parametrize("known_marker", [None, True, False])
def test_shadow_rebuild_does_not_invent_invalid_initial_sellable_fact(
    db: Session, sellable: float | None, known_marker: bool | None,
) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db)
    moment = datetime(2026, 8, 20, 2)
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=moment,
    )
    payload = {"security_type": "STOCK"}
    if sellable is not None:
        payload["sellable_quantity"] = sellable
    if known_marker is not None:
        payload["sellable_quantity_known"] = known_marker
    entry = ShadowLedgerEntry(
        entry_key="invalid-initial-availability",
        shadow_account_id=account.id,
        shadow_generation=account.shadow_generation,
        entry_type="INITIAL_POSITION",
        code="600001",
        quantity=100,
        price=10,
        occurred_at=moment,
        payload_json=payload,
    )
    db.add(entry)
    db.flush()
    position = rebuild_shadow_state(db, account, as_of=moment)["positions"]["600001"]
    assert position["quantity"] == 100
    assert position["sellable_quantity"] == 0
    assert position["sellable_quantity_known"] is False
    run = _run(
        db, user=user, portfolio=portfolio, snapshot=snapshot,
        finished_at=moment, actions=[_action(side="SELL")],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=moment + timedelta(minutes=1))
    assert process_pending_shadow_intents(db, account_id=account.id, now=moment + timedelta(minutes=2))["filled"] == 0
    assert intent.status == "PENDING"
    assert "SHADOW_BASE_FACTS_DATA_GAP" in intent.reason_codes_json
    refresh_shadow_materialized_state(db, account, as_of=moment + timedelta(minutes=2))
    materialized = db.scalar(select(ShadowPosition).where(ShadowPosition.shadow_account_id == account.id))
    assert materialized.sellable_quantity == 0
    assert materialized.metadata_json["sellable_quantity_known"] is False
    db.expire(entry)
    assert entry.quantity == 100
    assert entry.payload_json == payload


def test_shadow_rebuild_preserves_valid_unknown_lower_bound_and_settled_buy(db: Session) -> None:
    from app.routers.shadow_v3 import _position_payload

    user, portfolio, snapshot = _portfolio(db)
    moment = datetime(2026, 8, 20, 2)
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id, now=moment,
    )
    initial_payload = {"sellable_quantity": 50, "sellable_quantity_known": False}
    initial = ShadowLedgerEntry(
        entry_key="valid-initial-lower-bound",
        shadow_account_id=account.id,
        shadow_generation=account.shadow_generation,
        entry_type="INITIAL_POSITION",
        code="600001",
        quantity=100,
        price=10,
        occurred_at=moment,
        payload_json=initial_payload,
    )
    db.add_all([initial, ShadowLedgerEntry(
        entry_key="settled-buy-after-lower-bound",
        shadow_account_id=account.id,
        shadow_generation=account.shadow_generation,
        entry_type="BUY_FILL",
        code="600001",
        quantity=100,
        price=10,
        cash_delta=-1_000,
        occurred_at=moment + timedelta(seconds=1),
        sellable_at=moment + timedelta(minutes=1),
    )])
    db.flush()
    before = rebuild_shadow_state(db, account, as_of=moment)["positions"]["600001"]
    assert before["sellable_quantity"] == 50
    assert before["sellable_quantity_known"] is False
    after = refresh_shadow_materialized_state(db, account, as_of=moment + timedelta(minutes=2))["positions"]["600001"]
    assert after["quantity"] == 200
    assert after["sellable_quantity"] == 150
    assert after["sellable_quantity_known"] is False
    materialized = db.scalar(select(ShadowPosition).where(ShadowPosition.shadow_account_id == account.id))
    payload = _position_payload(materialized)
    assert payload["sellable_quantity"] is None
    assert payload["sellable_quantity_minimum"] == 150
    db.expire(initial)
    assert initial.payload_json == initial_payload


def test_shadow_unknown_initial_sellable_allows_buy_and_only_proven_new_quantity_to_sell(db: Session) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24)])
    user, portfolio, snapshot = _portfolio(
        db, holdings=[{"code": "600001", "qty": 100, "available_qty": None, "price": 10}],
    )
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 1, 30),
    )
    buy_run = _run(
        db, user=user, portfolio=portfolio, snapshot=snapshot,
        finished_at=datetime(2026, 8, 20, 2), actions=[_action()],
    )
    buy_observation = capture_live_decision_observation(db, buy_run, create_outcomes=False)
    buy_intent = ensure_shadow_order_intents(db, buy_observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 1))
    assert process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 2))["filled"] == 1
    assert buy_intent.status == "FILLED"
    next_state = rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 21, 2))["positions"]["600001"]
    assert next_state["quantity"] == 200
    assert next_state["sellable_quantity"] == 100
    assert next_state["sellable_quantity_known"] is False
    sell_run = _run(
        db, user=user, portfolio=portfolio, snapshot=snapshot,
        finished_at=datetime(2026, 8, 21, 2), actions=[_action(side="SELL")],
    )
    sell_observation = capture_live_decision_observation(db, sell_run, create_outcomes=False)
    sell_intent = ensure_shadow_order_intents(db, sell_observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 21, 2, 1))
    assert process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 21, 2, 2))["filled"] == 1
    assert sell_intent.status == "FILLED"
    final_state = rebuild_shadow_state(db, account, as_of=datetime(2026, 8, 21, 2, 2))["positions"]["600001"]
    assert final_state["quantity"] == 100
    assert final_state["sellable_quantity"] == 0
    assert final_state["sellable_quantity_known"] is False


@pytest.mark.parametrize("bad_quote_at", [
    datetime(2026, 8, 20, 1, 10),
    datetime(2026, 8, 20, 1, 26),
    datetime(2026, 8, 20, 4),
    datetime(2026, 8, 20, 7),
    datetime(2026, 8, 20, 7, 5),
])
def test_shadow_closed_intraday_quotes_wait_for_next_open_session(db: Session, bad_quote_at: datetime) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db)
    finalized = bad_quote_at - timedelta(minutes=1)
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 1, 1),
    )
    # Keep post-close candidates inside this intraday intent's original window.
    if finalized.hour >= 7:
        finalized = datetime(2026, 8, 20, 6, 59)
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=finalized, actions=[_action()])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=bad_quote_at)
    waiting = process_pending_shadow_intents(db, account_id=account.id, now=bad_quote_at + timedelta(seconds=1))
    assert waiting["fills"] == []
    assert intent.status == "PENDING"
    assert "QUOTE_OUTSIDE_TRADING_SESSION" in intent.reason_codes_json
    if bad_quote_at.hour < 2:
        recovery_at = datetime(2026, 8, 20, 1, 31)
    elif bad_quote_at.hour < 7:
        recovery_at = datetime(2026, 8, 20, 5)
    else:
        recovery_at = datetime(2026, 8, 21, 1, 31)
    valid = _quote(db, code="600001", captured_at=recovery_at)
    assert process_pending_shadow_intents(db, account_id=account.id, now=recovery_at + timedelta(seconds=1))["filled"] == 1
    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    assert fill.quote_observation_id == valid.id


@pytest.mark.parametrize("captured_at", [
    datetime(2026, 8, 20, 1, 16),
    datetime(2026, 8, 20, 1, 30),
    datetime(2026, 8, 20, 3, 29, 59),
    datetime(2026, 8, 20, 5),
    datetime(2026, 8, 20, 6, 59, 59),
])
def test_shadow_canonical_open_sessions_can_fill(db: Session, captured_at: datetime) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db)
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 1, 1),
    )
    run = _run(
        db, user=user, portfolio=portfolio, snapshot=snapshot,
        finished_at=captured_at - timedelta(seconds=1), actions=[_action()],
    )
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    valid = _quote(db, code="600001", captured_at=captured_at)
    assert process_pending_shadow_intents(db, account_id=account.id, now=captured_at)["filled"] == 1
    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    assert fill.quote_observation_id == valid.id


@pytest.mark.parametrize("sizing_mode", ["quantity", "weight"])
def test_shadow_inexact_mark_cannot_complete_execution_time_equity(db: Session, sizing_mode: str) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(
        db, cash=1_000, holdings=[{"code": "600009", "qty": 1_000, "price": 100}],
    )
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 1, 30),
    )
    action = _action(price=1)
    if sizing_mode == "weight":
        action.pop("target_qty")
        action["target_weight"] = 0.005
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 20, 2), actions=[action])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    intent = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600009", captured_at=datetime(2026, 8, 20, 2, 1), price=100, precision="MINUTE")
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 1), price=1)
    equity, evidence = _shadow_equity_at(db, account, generation=1, as_of=datetime(2026, 8, 20, 2, 1))
    assert equity is None
    assert evidence["status"] == "SHADOW_MARK_DATA_GAP"
    waiting = process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 2))
    assert waiting["fills"] == []
    assert intent.status == "PENDING"
    assert "SHADOW_MARK_DATA_GAP" in intent.reason_codes_json

    # An exact future mark cannot repair the earlier candidate's cutoff.
    _quote(db, code="600009", captured_at=datetime(2026, 8, 20, 2, 2), price=100)
    assert process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 2))["fills"] == []
    later = _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 3), price=1)
    assert process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 4))["filled"] == 1
    fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == intent.id))
    assert fill.quote_observation_id == later.id
    assert fill.quantity == (500 if sizing_mode == "weight" else 100)


def test_shadow_v1_requires_explicit_rebase_and_preserves_completed_facts(db: Session) -> None:
    _calendar(db, [date(2026, 8, 20), date(2026, 8, 21)])
    user, portfolio, snapshot = _portfolio(db)
    account = create_shadow_account(
        db, user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        now=datetime(2026, 8, 20, 1, 30),
    )
    run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 20, 2), actions=[_action(price=1)])
    observation = capture_live_decision_observation(db, run, create_outcomes=False)
    completed = ensure_shadow_order_intents(db, observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 1), price=1)
    assert process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 2))["filled"] == 1
    old_fill = db.scalar(select(ShadowFill).where(ShadowFill.order_intent_id == completed.id))
    old_entry = db.scalar(select(ShadowLedgerEntry).where(ShadowLedgerEntry.fill_id == old_fill.id))
    frozen_fill = (old_fill.fill_at, old_fill.price, old_fill.quantity, old_fill.execution_key)
    frozen_entry = (old_entry.quantity, old_entry.cash_delta, old_entry.sellable_at, old_entry.payload_json)
    account.execution_contract_version = "shadow-execution-v1"
    pending_run = _run(db, user=user, portfolio=portfolio, snapshot=snapshot, finished_at=datetime(2026, 8, 20, 2, 3), actions=[_action(price=1)])
    pending_observation = capture_live_decision_observation(db, pending_run, create_outcomes=False)
    pending = ensure_shadow_order_intents(db, pending_observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 4), price=1)

    assert process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 5))["fills"] == []
    assert pending.status == "PENDING"
    assert "SHADOW_REBASE_REQUIRED" in pending.reason_codes_json
    assert completed.status == "FILLED"
    assert (old_fill.fill_at, old_fill.price, old_fill.quantity, old_fill.execution_key) == frozen_fill
    assert (old_entry.quantity, old_entry.cash_delta, old_entry.sellable_at, old_entry.payload_json) == frozen_entry

    fresh = _snapshot(db, user=user, portfolio=portfolio, cash=10_000, snapshot_time=datetime(2026, 8, 20, 2, 6))
    rebase_shadow_account(db, account, snapshot_id=fresh.id, now=datetime(2026, 8, 20, 2, 6))
    assert account.execution_contract_version == "shadow-execution-v2"
    assert account.shadow_generation == 2
    assert rebuild_shadow_state(db, account, generation=2)["positions"] == {}
    new_run = _run(db, user=user, portfolio=portfolio, snapshot=fresh, finished_at=datetime(2026, 8, 20, 2, 7), actions=[_action(price=1)])
    new_observation = capture_live_decision_observation(db, new_run, create_outcomes=False)
    new_intent = ensure_shadow_order_intents(db, new_observation, account=account)[0]
    _quote(db, code="600001", captured_at=datetime(2026, 8, 20, 2, 8), price=1)
    assert process_pending_shadow_intents(db, account_id=account.id, now=datetime(2026, 8, 20, 2, 9))["filled"] == 1
    assert new_intent.status == "FILLED"
    assert pending.status == "SUPERSEDED"
    assert (old_fill.fill_at, old_fill.price, old_fill.quantity, old_fill.execution_key) == frozen_fill
    assert (old_entry.quantity, old_entry.cash_delta, old_entry.sellable_at, old_entry.payload_json) == frozen_entry
