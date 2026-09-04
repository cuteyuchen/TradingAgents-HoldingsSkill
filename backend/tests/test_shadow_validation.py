"""Phase N live observation and paper-only shadow execution contracts."""

from __future__ import annotations

from datetime import date, datetime, timedelta
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
        source="TEST",
        snapshot_time=datetime(2026, 8, 20, 6, 0),
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
        source="TEST",
        snapshot_time=snapshot_time,
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
        now=datetime(2026, 8, 20, 1, 0),
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
        now=datetime(2026, 8, 20, 1, 0),
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
