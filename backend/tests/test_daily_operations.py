"""Phase H operating-workbench regression coverage."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.market_models import TradingCalendar
from app.market_runtime_models import ProviderHealth
from app.memory.models import DailyReviewRun, DecisionMemory
from app.memory.review import run_daily_review
from app.operations import workflow
from app.operations.dashboard import build_daily_dashboard
from app.operations.models import DailyOperationalRun
from app.operations.notifications import (
    OperatingNotificationEvent,
    dispatch_material_events,
    dispatch_operating_event,
    list_operating_notifications,
    mark_operating_notification_read,
)
from app.routers.operations_v3 import _portfolio
from app.services.trading_calendar import CHINA_TZ
from app.v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=CHINA_TZ)


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _portfolio_fixture(
    db: Session,
    *,
    trade_date: date = date(2026, 8, 20),
) -> tuple[User, Portfolio, PortfolioSnapshot]:
    user = User(
        email=f"phase-h-{trade_date.isoformat()}@example.com",
        username=f"phaseh{trade_date.strftime('%m%d')}",
        password_hash="hash",
    )
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name="Phase H")
    db.add(portfolio)
    db.flush()
    snapshot = PortfolioSnapshot(
        user_id=user.id,
        portfolio_id=portfolio.id,
        status="confirmed",
        snapshot_time=_utc_naive(_local(trade_date, 8, 50)),
        total_assets=100000,
        total_market_value=80000,
        broker_available_cash=20000,
    )
    db.add(snapshot)
    db.add(TradingCalendar(market="CN", trade_date=trade_date, is_open=True))
    db.flush()
    return user, portfolio, snapshot


class _FakeThread:
    starts: list[tuple[int, ...]] = []

    def __init__(self, *, target, args=(), **_kwargs):
        self.args = args

    def start(self) -> None:
        self.starts.append(self.args)


def test_checkpoint_catches_up_once_and_records_missed_after_window(monkeypatch):
    db = _db()
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(db)
        _FakeThread.starts = []
        monkeypatch.setattr(workflow.threading, "Thread", _FakeThread)

        caught_up = workflow.run_due_checkpoints(
            db,
            portfolio=portfolio,
            now=_local(date(2026, 8, 20), 9, 42),
        )
        assert caught_up["checkpoints"]["09:35"]["status"] == "RUNNING"
        assert len(caught_up["started_jobs"]) == 1
        assert _FakeThread.starts == [(caught_up["started_jobs"][0],)]

        second_tick = workflow.run_due_checkpoints(
            db,
            portfolio=portfolio,
            now=_local(date(2026, 8, 20), 9, 43),
        )
        assert second_tick["started_jobs"] == []
        assert db.query(AnalysisJob).filter(AnalysisJob.checkpoint == "09:35").count() == 1
    finally:
        db.close()

    late_db = _db()
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(late_db)
        late = workflow.run_due_checkpoints(
            late_db,
            portfolio=portfolio,
            now=_local(date(2026, 8, 20), 10, 0),
        )
        assert late["checkpoints"]["09:35"]["status"] == "MISSED"
        assert late_db.query(AnalysisJob).filter(AnalysisJob.checkpoint == "09:35").count() == 0
    finally:
        late_db.close()


def test_timeline_derives_checkpoint_state_without_operational_run():
    db = _db()
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(db)
        timeline = workflow.operational_timeline(
            db,
            user_id=portfolio.user_id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 9, 40),
        )
        checkpoint = next(item for item in timeline["timeline"] if item["key"] == "09:35")
        assert checkpoint["status"] == "PENDING"
        assert checkpoint["reason"] == "CHECKPOINT_PENDING"
    finally:
        db.close()


def test_checkpoint_reuses_the_active_portfolio_analysis_without_second_job(monkeypatch):
    db = _db()
    try:
        user, portfolio, snapshot = _portfolio_fixture(db)
        active = AnalysisJob(
            user_id=user.id,
            portfolio_id=portfolio.id,
            snapshot_id=snapshot.id,
            trigger_type="realtime_trigger",
            mode="fast",
            status="running",
            current_stage="running",
            idempotency_key="active-phase-h",
        )
        db.add(active)
        db.flush()
        _FakeThread.starts = []
        monkeypatch.setattr(workflow.threading, "Thread", _FakeThread)

        result = workflow.run_due_checkpoints(
            db,
            portfolio=portfolio,
            now=_local(date(2026, 8, 20), 14, 30),
        )

        checkpoint = result["checkpoints"]["14:30"]
        assert checkpoint["status"] == "REUSED"
        assert checkpoint["job_id"] == active.id
        assert db.query(AnalysisJob).count() == 1
        assert _FakeThread.starts == []
    finally:
        db.close()


def test_monitor_lifecycle_recovers_then_pauses_for_lunch_and_stops_at_close(monkeypatch):
    class Monitor:
        def __init__(self):
            self.running = False
            self.starts = 0
            self.stops = 0

        def start(self):
            self.running = True
            self.starts += 1

        def stop(self):
            self.running = False
            self.stops += 1

        def is_running(self):
            return self.running

    monitor = Monitor()
    monkeypatch.setattr(workflow, "get_realtime_monitor", lambda: monitor)
    monkeypatch.setattr(workflow, "settings", SimpleNamespace(REALTIME_MONITOR_ENABLED=True))

    assert workflow._run_monitor_lifecycle(_local(date(2026, 8, 20), 9, 40))["status"] == "RUNNING"
    assert monitor.starts == 1
    assert workflow._run_monitor_lifecycle(_local(date(2026, 8, 20), 11, 31))["status"] == "PAUSED"
    assert monitor.stops == 1
    assert workflow._run_monitor_lifecycle(_local(date(2026, 8, 20), 13, 5))["status"] == "RUNNING"
    assert workflow._run_monitor_lifecycle(_local(date(2026, 8, 20), 15, 0))["status"] == "STOPPED"
    assert monitor.stops == 2


def test_non_trading_dashboard_uses_latest_completed_review_without_writes():
    db = _db()
    try:
        friday = date(2026, 8, 21)
        saturday = date(2026, 8, 22)
        user, portfolio, snapshot = _portfolio_fixture(db, trade_date=friday)
        db.add(TradingCalendar(market="CN", trade_date=saturday, is_open=False))
        review = DailyReviewRun(
            user_id=user.id,
            portfolio_id=portfolio.id,
            trade_date=friday,
            status="COMPLETED",
            quality_status="VALID",
            review_version="daily-review-v1",
            created_at=_utc_naive(_local(friday, 15, 30)),
            completed_at=_utc_naive(_local(friday, 15, 35)),
        )
        db.add(review)
        db.commit()

        before_operations = db.query(DailyOperationalRun).count()
        before_jobs = db.query(AnalysisJob).count()
        dashboard = build_daily_dashboard(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(saturday, 10, 0),
        )

        assert dashboard["workflow_state"] == "NON_TRADING_DAY"
        assert dashboard["market_open"] is False
        assert dashboard["memory"]["review"]["id"] == review.id
        assert dashboard["portfolio"]["snapshot_id"] == snapshot.id
        assert db.query(DailyOperationalRun).count() == before_operations
        assert db.query(AnalysisJob).count() == before_jobs
    finally:
        db.close()


def test_review_refresh_keeps_same_row_and_increments_count():
    db = _db()
    try:
        trade_date = date(2026, 8, 20)
        user, portfolio, _snapshot = _portfolio_fixture(db, trade_date=trade_date)
        first = run_daily_review(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            trade_date=trade_date,
            as_of=_local(trade_date, 15, 30),
        )
        assert first is not None and first.status == "COMPLETED"
        first.review_stale = True
        db.commit()

        refreshed = run_daily_review(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            trade_date=trade_date,
            as_of=_local(trade_date, 15, 45),
            force=True,
        )
        assert refreshed is not None
        assert refreshed.id == first.id
        assert refreshed.review_stale is False
        assert refreshed.refresh_count == 1
        assert db.query(DailyReviewRun).count() == 1
    finally:
        db.close()


def test_notification_is_deduped_without_webhook_delivery():
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        event = OperatingNotificationEvent(
            title="市场状态切换",
            summary="测试事件。",
            severity="IMPORTANT",
            portfolio_id=portfolio.id,
            user_id=user.id,
            event_type="market_regime",
            entity_type="market_score",
            entity_id="market-1",
            occurred_at=_local(date(2026, 8, 20), 10, 0),
            deep_link=f"/dashboard?portfolio={portfolio.id}#market",
            dedupe_key=f"market_regime:{portfolio.id}:RISK_ON",
        )
        first = dispatch_operating_event(db, event, now=_local(date(2026, 8, 20), 10, 0))
        second = dispatch_operating_event(db, event, now=_local(date(2026, 8, 20), 10, 1))
        assert first["status"] == "DASHBOARD_ONLY"
        assert second["status"] == "DEDUPED"
    finally:
        db.close()


def test_notification_read_model_is_scoped_and_respects_as_of():
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        occurred_at = _local(date(2026, 8, 20), 10, 0)
        event = OperatingNotificationEvent(
            title="市场状态切换",
            summary="测试事件。",
            severity="IMPORTANT",
            portfolio_id=portfolio.id,
            user_id=user.id,
            event_type="market_regime",
            entity_type="market_score",
            entity_id="market-2",
            occurred_at=occurred_at,
            deep_link=f"/dashboard?portfolio={portfolio.id}#market",
            dedupe_key=f"market_regime:{portfolio.id}:NEUTRAL",
        )
        dispatch_operating_event(db, event, now=occurred_at)

        before = list_operating_notifications(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=occurred_at - timedelta(minutes=1),
        )
        visible = list_operating_notifications(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=occurred_at,
        )
        other_user = list_operating_notifications(
            db,
            user_id=user.id + 1,
            portfolio_id=portfolio.id,
            as_of=occurred_at,
        )

        assert before["count"] == 0
        assert visible["count"] == 1
        assert visible["items"][0]["dedupe_key"] == event.dedupe_key
        assert other_user["count"] == 0

        marked = mark_operating_notification_read(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            notification_id=visible["items"][0]["notification_id"],
            read_at=occurred_at + timedelta(minutes=2),
        )
        assert marked["read"] is True
        unread = list_operating_notifications(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=occurred_at + timedelta(minutes=2),
            unread_only=True,
        )
        assert unread["count"] == 0
    finally:
        db.close()


def test_provider_health_distinguishes_unknown_from_total_outage():
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        as_of = _local(date(2026, 8, 20), 10, 0)
        unknown = build_daily_dashboard(db, user_id=user.id, portfolio_id=portfolio.id, as_of=as_of)
        component = next(item for item in unknown["data_health"]["components"] if item["name"] == "Primary/Fallback Provider")
        assert component["status"] == "UNKNOWN"

        from app.config import settings

        db.add_all([
            ProviderHealth(
                provider_name=settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER,
                data_type="quote",
                status="CIRCUIT_OPEN",
            ),
            ProviderHealth(
                provider_name=settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS[0],
                data_type="quote",
                status="CIRCUIT_OPEN",
            ),
        ])
        db.commit()
        blocked = build_daily_dashboard(db, user_id=user.id, portfolio_id=portfolio.id, as_of=as_of)
        component = next(item for item in blocked["data_health"]["components"] if item["name"] == "Primary/Fallback Provider")
        assert component["status"] == "BLOCKED"
    finally:
        db.close()


def test_dashboard_historical_as_of_does_not_expose_later_analysis_or_review_completion():
    db = _db()
    try:
        user, portfolio, snapshot = _portfolio_fixture(db)
        decision_at = _local(date(2026, 8, 20), 9, 30)
        completed_at = _local(date(2026, 8, 20), 10, 0)
        job = AnalysisJob(
            user_id=user.id,
            portfolio_id=portfolio.id,
            snapshot_id=snapshot.id,
            mode="standard",
            status="succeeded",
            current_stage="completed",
            idempotency_key="phase-h-historical-as-of",
            created_at=_utc_naive(_local(date(2026, 8, 20), 9, 0)),
            started_at=_utc_naive(_local(date(2026, 8, 20), 9, 1)),
            finished_at=_utc_naive(completed_at),
        )
        db.add(job)
        db.flush()
        run = AnalysisRun(
            job_id=job.id,
            user_id=user.id,
            portfolio_snapshot_id=snapshot.id,
            final_rating="no_action",
            data_quality_grade="A",
            markdown_text="historical fixture",
            created_at=_utc_naive(completed_at),
            structured_result_json={"result": {"portfolio_action": "no_action", "candidates": []}},
        )
        db.add(run)
        db.flush()
        db.add(DecisionMemory(
            user_id=user.id,
            portfolio_id=portfolio.id,
            analysis_run_id=run.id,
            analysis_job_id=job.id,
            trade_date=date(2026, 8, 20),
            decision_at=_utc_naive(decision_at),
            available_at=_utc_naive(decision_at),
            analysis_mode="standard",
            decision_type="NO_ACTION",
            quality_status="VALID",
            confidence=0.9,
            created_at=_utc_naive(decision_at),
        ))
        db.add(DailyReviewRun(
            user_id=user.id,
            portfolio_id=portfolio.id,
            trade_date=date(2026, 8, 20),
            status="COMPLETED",
            quality_status="VALID",
            created_at=_utc_naive(_local(date(2026, 8, 20), 9, 35)),
            completed_at=_utc_naive(completed_at),
            last_refreshed_at=_utc_naive(completed_at),
        ))
        db.commit()

        historical = build_daily_dashboard(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 9, 45),
        )
        assert historical["analysis"]["latest"] is None
        assert historical["analysis"]["jobs"][0]["status"] == "RUNNING"
        assert historical["decisions"]["latest"] is None
        assert historical["memory"]["review"] is None

        after_completion = build_daily_dashboard(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 5),
        )
        assert after_completion["analysis"]["latest"]["analysis_run_id"] == run.id
        assert after_completion["decisions"]["latest"]["analysis_run_id"] == run.id
        assert after_completion["memory"]["review"]["status"] == "COMPLETED"
    finally:
        db.close()


def test_missing_calendar_is_blocked_in_dashboard_health():
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        db.query(TradingCalendar).delete()
        db.commit()

        dashboard = build_daily_dashboard(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 0),
        )
        calendar_component = next(
            item for item in dashboard["data_health"]["components"] if item["name"] == "TradingCalendar"
        )
        assert calendar_component["status"] == "BLOCKED"
        assert dashboard["data_health"]["overall"] == "BLOCKED"
    finally:
        db.close()


def test_provider_outage_episode_and_recovery_notifications_are_deduped(monkeypatch):
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        from app.config import settings

        primary_name = settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER
        fallback_name = settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS[0]
        first_failure = _utc_naive(_local(date(2026, 8, 20), 9, 40))
        second_failure = _utc_naive(_local(date(2026, 8, 20), 9, 50))
        db.add_all([
            ProviderHealth(
                provider_name=primary_name,
                data_type="quote",
                status="CIRCUIT_OPEN",
                last_failure_at=first_failure,
            ),
            ProviderHealth(
                provider_name=fallback_name,
                data_type="quote",
                status="HEALTHY",
                last_success_at=_utc_naive(_local(date(2026, 8, 20), 9, 30)),
            ),
        ])
        db.commit()

        first = dispatch_material_events(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 0),
        )
        assert [item["status"] for item in first if item["event_type"] == "data_health"] == ["DASHBOARD_ONLY"]

        primary = db.query(ProviderHealth).filter_by(provider_name=primary_name, data_type="quote").one()
        primary.last_failure_at = second_failure
        db.commit()
        second = dispatch_material_events(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 1),
        )
        assert [item["status"] for item in second if item["event_type"] == "data_health"] == ["DEDUPED"]

        primary.status = "HEALTHY"
        primary.last_success_at = _utc_naive(_local(date(2026, 8, 20), 10, 2))
        db.commit()
        recovered = dispatch_material_events(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 3),
        )
        assert [item["status"] for item in recovered if item["event_type"] == "provider_recovery"] == ["DASHBOARD_ONLY"]

        primary.last_success_at = _utc_naive(_local(date(2026, 8, 20), 10, 4))
        db.commit()
        recovered_again = dispatch_material_events(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 5),
        )
        assert [item["status"] for item in recovered_again if item["event_type"] == "provider_recovery"] == ["DEDUPED"]

        info_event = OperatingNotificationEvent(
            title="普通信息",
            summary="仅用于 Dashboard 展示。",
            severity="INFO",
            portfolio_id=portfolio.id,
            user_id=user.id,
            event_type="scheduled_analysis_completed",
            entity_type="analysis_job",
            entity_id="phase-h-info",
            occurred_at=_local(date(2026, 8, 20), 10, 6),
            deep_link=f"/dashboard?portfolio={portfolio.id}#analysis",
            dedupe_key=f"scheduled_analysis_completed:{portfolio.id}:phase-h-info",
        )
        info = dispatch_operating_event(db, info_event, now=_local(date(2026, 8, 20), 10, 6))
        assert info["status"] == "DASHBOARD_ONLY"
        assert info["deliveries"] == []
    finally:
        db.close()


def test_operations_portfolio_lookup_is_user_scoped():
    db = _db()
    try:
        owner, portfolio, _snapshot = _portfolio_fixture(db)
        other = User(email="other-phase-h@example.com", username="otherphaseh", password_hash="hash")
        db.add(other)
        db.flush()
        assert _portfolio(db, user_id=owner.id, portfolio_id=portfolio.id).id == portfolio.id
        with pytest.raises(HTTPException) as exc_info:
            _portfolio(db, user_id=other.id, portfolio_id=portfolio.id)
        assert exc_info.value.status_code == 404
    finally:
        db.close()
