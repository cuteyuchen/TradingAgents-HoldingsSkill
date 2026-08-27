"""Phase H operating-workbench regression coverage."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.market_engine_models import MarketScoreSnapshot
from app.market_models import TradingCalendar
from app.market_runtime_models import ProviderHealth
from app.memory.models import DailyReviewRun, DecisionMemory
from app.memory.review import run_daily_review
from app.operations import workflow
from app.operations.dashboard import build_daily_dashboard
from app.operations.models import DailyOperationalCheckpoint, DailyOperationalRun, OperatingNotification
from app.operations.notifications import (
    OperatingNotificationEvent,
    collect_material_events,
    dispatch_material_events,
    dispatch_operating_event,
    list_operating_notifications,
    mark_operating_notification_read,
)
from app.portfolio_models import PortfolioRiskSnapshot
from app.routers.operations_v3 import _portfolio
from app.services import scheduler as scheduler_service
from app.services.trading_calendar import CHINA_TZ
from app.v2_models import AnalysisJob, AnalysisRun, NotificationChannel, Portfolio, PortfolioSnapshot, Schedule, User


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


def test_checkpoint_database_claim_is_unique_across_workers():
    db = _db()
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(db)
        first, first_owner = workflow.claim_checkpoint(
            db,
            portfolio=portfolio,
            trade_date=date(2026, 8, 20),
            checkpoint_name="09:35",
        )
        db.commit()
        second, second_owner = workflow.claim_checkpoint(
            db,
            portfolio=portfolio,
            trade_date=date(2026, 8, 20),
            checkpoint_name="09:35",
        )

        assert first_owner is True
        assert second_owner is False
        assert second.id == first.id
        assert db.query(DailyOperationalCheckpoint).count() == 1
    finally:
        db.close()


def test_operational_run_unique_claim_keeps_outer_transaction_state():
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        first = workflow.ensure_operational_run(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            trade_date=date(2026, 8, 20),
        )
        first.status = "RUNNING"
        db.flush()
        second = workflow.ensure_operational_run(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            trade_date=date(2026, 8, 20),
        )
        assert second.id == first.id
        assert db.get(DailyOperationalRun, first.id).status == "RUNNING"
    finally:
        db.close()


def test_checkpoint_boundaries_are_deterministic_and_success_never_becomes_missed():
    checkpoint = workflow.CHECKPOINT_BY_KEY["09:35"]
    base = _local(date(2026, 8, 20), 9, 35)
    cases = [
        (base, "DUE"),
        (base + timedelta(minutes=14, seconds=59), "DUE"),
        (base + timedelta(minutes=15), "DUE"),
        (base + timedelta(minutes=15, seconds=1), "MISSED"),
    ]
    for moment, expected in cases:
        status, actionable = workflow._checkpoint_status(checkpoint=checkpoint, local=moment, state={})
        assert status == expected
        assert actionable is True

    completed, actionable = workflow._checkpoint_status(
        checkpoint=checkpoint,
        local=base + timedelta(minutes=30),
        state={"09:35": {"status": "SUCCESS"}},
    )
    assert completed == "SUCCESS"
    assert actionable is False


def test_checkpoint_claim_survives_restart_without_duplicate_job(monkeypatch):
    db = _db()
    bind = db.bind
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(db)
        _FakeThread.starts = []
        monkeypatch.setattr(workflow.threading, "Thread", _FakeThread)
        first = workflow.run_due_checkpoints(db, portfolio=portfolio, now=_local(date(2026, 8, 20), 9, 40))
        assert first["started_jobs"]
        portfolio_id = portfolio.id
    finally:
        db.close()
    restarted = Session(bind=bind)
    try:
        portfolio = restarted.get(Portfolio, portfolio_id)
        second = workflow.run_due_checkpoints(restarted, portfolio=portfolio, now=_local(date(2026, 8, 20), 9, 49))
        assert second["started_jobs"] == []
        assert restarted.query(AnalysisJob).filter(AnalysisJob.checkpoint == "09:35").count() == 1
    finally:
        restarted.close()


def test_stale_analysis_claim_reclaims_same_queued_job_after_restart(monkeypatch):
    db = _db()
    bind = db.bind
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(db)
        portfolio_id = portfolio.id
        _FakeThread.starts = []
        monkeypatch.setattr(workflow.threading, "Thread", _FakeThread)
        first = workflow.run_due_checkpoints(
            db,
            portfolio=portfolio,
            now=_local(date(2026, 8, 20), 9, 40),
        )
        job_id = first["started_jobs"][0]
        claim = db.query(DailyOperationalCheckpoint).filter_by(
            portfolio_id=portfolio.id,
            trade_date=date(2026, 8, 20),
            checkpoint_name="09:35",
        ).one()
        claim.lease_expires_at = _utc_naive(_local(date(2026, 8, 20), 9, 39))
        db.commit()
        assert claim.completed_at is None
    finally:
        db.close()

    restarted = Session(bind=bind)
    try:
        _FakeThread.starts = []
        result = workflow.run_due_checkpoints(
            restarted,
            portfolio=restarted.get(Portfolio, portfolio_id),
            now=_local(date(2026, 8, 20), 9, 49),
        )
        claim = restarted.query(DailyOperationalCheckpoint).filter_by(
            portfolio_id=portfolio_id,
            trade_date=date(2026, 8, 20),
            checkpoint_name="09:35",
        ).one()
        assert result["started_jobs"] == [job_id]
        assert _FakeThread.starts == [(job_id,)]
        assert restarted.query(AnalysisJob).count() == 1
        assert claim.job_id == job_id
        assert claim.attempt_count == 2
        assert claim.completed_at is None
    finally:
        restarted.close()


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


def test_dashboard_get_is_read_only_even_when_facts_are_missing(monkeypatch):
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        calls: list[str] = []

        def fail(name: str):
            def _inner(*_args, **_kwargs):
                calls.append(name)
                raise AssertionError(name)
            return _inner

        from app.candidates import service as candidate_service
        from app.memory import review as memory_review
        from app.operations import dashboard as dashboard_module
        from app.operations import notifications as notification_module
        from app.services import analysis_engine, market_engine

        monkeypatch.setattr(analysis_engine, "run_analysis_job", fail("analysis"))
        monkeypatch.setattr(candidate_service, "scan_candidates", fail("candidate"))
        monkeypatch.setattr(market_engine.MarketEngine, "calculate", fail("market"))
        monkeypatch.setattr(memory_review, "run_daily_review", fail("review"))
        monkeypatch.setattr(notification_module, "dispatch_material_events", fail("notification"))

        result = build_daily_dashboard(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 0),
        )
        assert result["portfolio"]["snapshot_id"] is not None
        assert calls == []
    finally:
        db.close()


@pytest.mark.parametrize("section_name, symbol", [
    ("market", "_market_section"),
    ("candidates", "_candidate_section"),
    ("analysis", "_analysis_section"),
    ("notifications", "list_operating_notifications"),
])
def test_dashboard_sections_fail_independently(monkeypatch, section_name, symbol):
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        from app.operations import dashboard as dashboard_module

        def fail(*_args, **_kwargs):
            raise RuntimeError(f"{section_name}_boom")

        monkeypatch.setattr(dashboard_module, symbol, fail)
        result = build_daily_dashboard(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 0),
        )
        assert result[section_name]["status"] == "ERROR"
        healthy_sections = ["market", "portfolio", "candidates", "triggers", "analysis", "decisions", "executions", "memory", "timeline", "notifications"]
        assert any(result[name].get("status") != "ERROR" for name in healthy_sections if name != section_name)
    finally:
        db.close()


def test_notification_is_durable_across_restart_and_retries_after_cooldown(monkeypatch):
    db = _db()
    bind = db.bind
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        db.add(NotificationChannel(
            user_id=user.id,
            type="dingtalk",
            name="phase-h-channel",
            encrypted_webhook="https://example.invalid/webhook",
            enabled=True,
        ))
        db.commit()
        event = OperatingNotificationEvent(
            title="触发复核",
            summary="测试重试。",
            severity="IMPORTANT",
            portfolio_id=portfolio.id,
            user_id=user.id,
            event_type="trigger_confirmed",
            entity_type="trigger",
            entity_id="t-1",
            occurred_at=_local(date(2026, 8, 20), 10, 0),
            deep_link=f"/dashboard?portfolio={portfolio.id}#triggers",
            dedupe_key=f"trigger_confirmed:{portfolio.id}:t-1",
        )
        monkeypatch.setattr("app.operations.notifications._post_channel", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timeout")))
        first = dispatch_operating_event(db, event, now=_local(date(2026, 8, 20), 10, 0))
        assert first["status"] == "FAILED"
        assert db.query(OperatingNotification).one().attempt_count == 1
        db.close()

        restarted = Session(bind=bind)
        try:
            cooldown = dispatch_operating_event(restarted, event, now=_local(date(2026, 8, 20), 10, 1))
            assert cooldown["status"] == "COOLDOWN"
            monkeypatch.setattr("app.operations.notifications._post_channel", lambda *_args, **_kwargs: (200, "ok"))
            retried = dispatch_operating_event(restarted, event, now=_local(date(2026, 8, 20), 11, 1))
            assert retried["status"] == "SENT"
            row = restarted.query(OperatingNotification).one()
            assert row.attempt_count == 2
            assert row.status == "SENT"
        finally:
            restarted.close()
    finally:
        if db.is_active:
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


def test_stale_notification_dispatching_claim_is_reclaimed(monkeypatch):
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        db.add(NotificationChannel(
            user_id=user.id,
            type="dingtalk",
            name="phase-h1-channel",
            encrypted_webhook="https://example.invalid/webhook",
            enabled=True,
        ))
        event = OperatingNotificationEvent(
            title="重要触发",
            summary="测试 crash recovery。",
            severity="IMPORTANT",
            portfolio_id=portfolio.id,
            user_id=user.id,
            event_type="trigger_confirmed",
            entity_type="trigger",
            entity_id="stale-claim",
            occurred_at=_local(date(2026, 8, 20), 10, 0),
            deep_link=f"/dashboard?portfolio={portfolio.id}#triggers",
            dedupe_key=f"trigger_confirmed:{portfolio.id}:stale-claim",
        )
        stale_at = _utc_naive(_local(date(2026, 8, 20), 9, 0))
        db.add(OperatingNotification(
            notification_id="opn_stale_claim",
            user_id=user.id,
            portfolio_id=portfolio.id,
            trade_date=date(2026, 8, 20),
            dedupe_key=event.dedupe_key,
            event_type=event.event_type,
            severity=event.severity,
            status="DISPATCHING",
            occurred_at=_utc_naive(event.occurred_at),
            last_attempt_at=stale_at,
            lease_expires_at=stale_at,
            attempt_count=1,
        ))
        db.commit()
        monkeypatch.setattr("app.operations.notifications._post_channel", lambda *_args, **_kwargs: (200, "ok"))

        result = dispatch_operating_event(
            db,
            event,
            now=_local(date(2026, 8, 20), 10, 0),
        )

        row = db.query(OperatingNotification).one()
        assert result["status"] == "SENT"
        assert row.status == "SENT"
        assert row.attempt_count == 2
        assert row.lease_expires_at is None
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


def test_primary_outage_without_fallback_is_blocked(monkeypatch):
    db = _db()
    try:
        user, portfolio, _snapshot = _portfolio_fixture(db)
        from app.config import settings

        monkeypatch.setattr(settings, "MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS", ())
        db.add(ProviderHealth(
            provider_name=settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER,
            data_type="quote",
            status="CIRCUIT_OPEN",
            last_failure_at=_utc_naive(_local(date(2026, 8, 20), 9, 40)),
        ))
        db.commit()

        events = collect_material_events(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 0),
        )
        outage = next(item for item in events if item.event_type == "data_health")
        assert outage.severity == "CRITICAL"
        assert "暂停增加新风险" in outage.summary
    finally:
        db.close()


def test_dashboard_uses_authoritative_reserve_assets_formula():
    db = _db()
    try:
        user, portfolio, snapshot = _portfolio_fixture(db)
        snapshot.repo_or_standard_bond_value = 30000
        db.add(PortfolioRiskSnapshot(
            calculation_key="phase-h1-reserve",
            user_id=user.id,
            portfolio_id=portfolio.id,
            portfolio_snapshot_id=snapshot.id,
            as_of=snapshot.snapshot_time,
            total_assets=100000,
            market_value=50000,
            cash_ratio=0.20,
            quality_status="VALID",
        ))
        db.commit()

        dashboard = build_daily_dashboard(
            db,
            user_id=user.id,
            portfolio_id=portfolio.id,
            as_of=_local(date(2026, 8, 20), 10, 0),
        )

        assert dashboard["portfolio"]["cash_ratio"] == pytest.approx(0.20)
        assert dashboard["portfolio"]["reserve_assets"] == pytest.approx(50000)
        assert dashboard["portfolio"]["reserve_ratio"] == pytest.approx(0.50)
    finally:
        db.close()


def test_dashboard_delta_15m_uses_matching_same_day_baseline():
    db = _db()
    try:
        _user, _portfolio, _snapshot = _portfolio_fixture(db)
        day = date(2026, 8, 20)
        for minute, score in ((15, 60), (20, 65), (25, 68), (30, 70)):
            db.add(MarketScoreSnapshot(
                snapshot_id=f"phase-h1-score-{minute}",
                market="CN",
                trade_date=day,
                captured_at=_utc_naive(_local(day, 10, minute)),
                display_score=score,
                raw_score=score,
                quality_status="VALID",
                is_frozen=False,
            ))
        db.commit()

        from app.operations import dashboard as dashboard_module

        section = dashboard_module._market_section(
            db,
            cutoff=_utc_naive(_local(day, 10, 30)),
        )
        assert section["delta_15m"] == pytest.approx(10)
    finally:
        db.close()


def test_snapshot_hooks_are_not_success_without_persisted_snapshot(monkeypatch):
    db = _db()
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(db)
        monkeypatch.setattr(workflow, "_run_monitor_lifecycle", lambda _local: {"status": "STOPPED", "running": False})

        morning = workflow.run_due_checkpoints(
            db,
            portfolio=portfolio,
            now=_local(date(2026, 8, 20), 11, 30),
        )
        close = workflow.run_due_checkpoints(
            db,
            portfolio=portfolio,
            now=_local(date(2026, 8, 20), 15, 0),
        )

        assert morning["checkpoints"]["morning_snapshot"]["status"] == "NOT_AVAILABLE"
        assert morning["checkpoints"]["morning_snapshot"]["reason"] == "HOOK_ONLY"
        assert close["checkpoints"]["market_close"]["status"] == "NOT_AVAILABLE"
        assert close["checkpoints"]["market_close"]["reason"] == "HOOK_ONLY"
    finally:
        db.close()


def test_persisted_snapshot_hook_reports_success_and_snapshot_id(monkeypatch):
    db = _db()
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(db)
        day = date(2026, 8, 20)
        db.add_all([
            MarketScoreSnapshot(
                snapshot_id="phase-h1-morning-snapshot",
                market="CN",
                trade_date=day,
                captured_at=_utc_naive(_local(day, 11, 29)),
                display_score=66,
                raw_score=66,
                quality_status="VALID",
                is_frozen=False,
            ),
            MarketScoreSnapshot(
                snapshot_id="phase-h1-close-snapshot",
                market="CN",
                trade_date=day,
                captured_at=_utc_naive(_local(day, 15, 1)),
                display_score=64,
                raw_score=64,
                quality_status="VALID",
                is_frozen=False,
            ),
        ])
        db.commit()
        monkeypatch.setattr(workflow, "_run_monitor_lifecycle", lambda _local: {"status": "STOPPED", "running": False})

        morning = workflow.run_due_checkpoints(db, portfolio=portfolio, now=_local(day, 11, 30))
        close = workflow.run_due_checkpoints(db, portfolio=portfolio, now=_local(day, 15, 5))

        assert morning["checkpoints"]["morning_snapshot"]["status"] == "SUCCESS"
        assert morning["checkpoints"]["morning_snapshot"]["snapshot_id"] == "phase-h1-morning-snapshot"
        assert close["checkpoints"]["market_close"]["status"] == "SUCCESS"
        assert close["checkpoints"]["market_close"]["snapshot_id"] == "phase-h1-close-snapshot"
    finally:
        db.close()


def test_daily_review_stale_running_claim_is_reclaimed_after_restart(monkeypatch):
    db = _db()
    bind = db.bind
    try:
        _user, portfolio, _snapshot = _portfolio_fixture(db)
        starts: list[dict[str, object]] = []

        class FakeReviewThread:
            def __init__(self, *, target, kwargs, daemon):
                assert target is scheduler_service.run_scheduled_daily_review
                assert daemon is True
                starts.append(kwargs)

            def start(self):
                return None

        monkeypatch.setattr(scheduler_service.threading, "Thread", FakeReviewThread)
        now = _local(date(2026, 8, 20), 15, 31).astimezone(UTC)
        scheduler_service._enqueue_memory_reviews(db, trade_date=date(2026, 8, 20), now_utc=now)
        review = db.query(DailyReviewRun).one()
        review.lease_expires_at = _utc_naive(_local(date(2026, 8, 20), 15, 30))
        db.commit()
        review_id = review.id
    finally:
        db.close()

    restarted = Session(bind=bind)
    try:
        starts.clear()
        scheduler_service._enqueue_memory_reviews(
            restarted,
            trade_date=date(2026, 8, 20),
            now_utc=_local(date(2026, 8, 20), 15, 40).astimezone(UTC),
        )
        review = restarted.query(DailyReviewRun).one()
        assert review.id == review_id
        assert review.status == "RUNNING"
        assert review.attempt_count == 2
        assert starts == [{"user_id": review.user_id, "portfolio_id": review.portfolio_id, "trade_date": date(2026, 8, 20)}]
    finally:
        restarted.close()


def test_legacy_scheduler_reuses_phase_h_checkpoint_job(monkeypatch):
    db = _db()
    try:
        user, portfolio, snapshot = _portfolio_fixture(db)
        day = date(2026, 8, 20)
        schedule = Schedule(
            user_id=user.id,
            portfolio_id=portfolio.id,
            name="09:35",
            checkpoint="09:35",
            mode="standard",
            hour=9,
            minute=35,
        )
        db.add(schedule)
        db.flush()
        existing = AnalysisJob(
            user_id=user.id,
            portfolio_id=portfolio.id,
            snapshot_id=snapshot.id,
            trigger_type="scheduled",
            checkpoint="09:35",
            mode="standard",
            status="succeeded",
            current_stage="completed",
            idempotency_key=f"phase-h:{portfolio.id}:{day.isoformat()}:09:35",
            created_at=_utc_naive(_local(day, 9, 42)),
        )
        db.add(existing)
        db.commit()

        admission = scheduler_service.create_scheduled_job(
            db,
            schedule,
            with_admission=True,
            now=_local(day, 9, 45),
        )

        assert admission.job.id == existing.id
        assert admission.should_start is False
        assert admission.source == "existing_checkpoint"
        assert db.query(AnalysisJob).count() == 1
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
