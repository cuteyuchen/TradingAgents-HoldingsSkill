"""Phase K scheduler ownership, shutdown, and startup recovery reporting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.research.models import BacktestRun
from app.services.scheduler import scheduler_running, start_scheduler, stop_scheduler
from app.operations.models import DailyOperationalCheckpoint
from app.system.workers import register_worker, signal_workers, unregister_worker, active_workers
from app.system.startup import collect_startup_recovery_report
from app.v2_models import AnalysisJob


def _full_db() -> Session:
    import app.candidates.models  # noqa: F401
    import app.governance.models  # noqa: F401
    import app.market_engine_models  # noqa: F401
    import app.market_models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.portfolio_models  # noqa: F401
    import app.research.models  # noqa: F401
    import app.trigger_models  # noqa: F401
    import app.v2_models  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_scheduler_is_single_owner_and_stops(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    start_scheduler()
    first = None
    from app.services import scheduler as scheduler_module

    first = scheduler_module._scheduler
    start_scheduler()
    assert scheduler_module._scheduler is first
    assert scheduler_running()
    stop_scheduler()
    assert not scheduler_running()
    assert scheduler_module._scheduler is None


def test_startup_recovery_report_counts_stale_claims():
    db = _full_db()
    try:
        moment = datetime.now(UTC).replace(tzinfo=None)
        db.add(BacktestRun(
            user_id=None,
            portfolio_id=None,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=__import__("datetime").date(2026, 1, 1),
            end_date=__import__("datetime").date(2026, 1, 31),
            status="RUNNING",
            lease_expires_at=moment - timedelta(hours=1),
            attempt_count=1,
            data_hash="hash",
            calculation_key="stale-backtest-1",
        ))
        db.add(AnalysisJob(
            user_id=1,
            portfolio_id=1,
            snapshot_id=1,
            mode="standard",
            status="running",
            current_stage="running",
            started_at=moment - timedelta(hours=1),
        ))
        db.commit()
        report = collect_startup_recovery_report(db)
        assert report["counts"]["stale_backtests"] == 1
        assert report["counts"]["stale_analysis_jobs"] == 0
        assert report["counts"]["active_analysis_jobs"] == 1
        assert report["errors"] == []
    finally:
        db.close()


def test_startup_recovery_report_only_counts_expired_analysis_lease():
    db = _full_db()
    try:
        moment = datetime.now(UTC).replace(tzinfo=None)
        job = AnalysisJob(
            user_id=1,
            portfolio_id=1,
            snapshot_id=1,
            mode="standard",
            status="running",
            current_stage="running",
            started_at=moment - timedelta(hours=1),
        )
        db.add(job)
        db.flush()
        db.add(DailyOperationalCheckpoint(
            user_id=1,
            portfolio_id=1,
            trade_date=__import__("datetime").date(2026, 8, 28),
            checkpoint_name="10:30",
            workflow_version="daily-workflow-v1",
            status="RUNNING",
            job_id=job.id,
            claimed_at=moment - timedelta(hours=2),
            lease_expires_at=moment - timedelta(hours=1),
            attempt_count=1,
        ))
        db.commit()
        report = collect_startup_recovery_report(db)
        assert report["counts"]["stale_analysis_jobs"] == 1
        assert report["counts"]["active_analysis_jobs"] == 0
    finally:
        db.close()


def test_worker_registry_signals_and_unregisters():
    stop_event = threading.Event()
    register_worker("backtest", 7, stop_event)
    try:
        assert active_workers() == [{"kind": "backtest", "work_id": 7}]
        assert signal_workers(timeout=0.1) == 1
        assert stop_event.is_set()
    finally:
        unregister_worker("backtest", 7)
    assert active_workers() == []


def test_analysis_heartbeat_stops_on_external_signal():
    from app.services.analysis_lease import AnalysisLeaseHeartbeat

    class FakeDb:
        def execute(self, *args, **kwargs):
            raise RuntimeError("injected")

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    external = threading.Event()
    heartbeat = AnalysisLeaseHeartbeat(
        job_id=99,
        attempt_count=1,
        interval_seconds=0.05,
        session_factory=lambda: FakeDb(),
        external_stop=external,
    )
    heartbeat.start()
    try:
        external.set()
        deadline = __import__("time").monotonic() + 2.0
        while not heartbeat._stop.is_set() and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.02)
        assert heartbeat._stop.is_set()
    finally:
        heartbeat.stop()
