"""Phase K scheduler ownership, shutdown, and startup recovery reporting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.research.models import BacktestRun
from app.services.scheduler import scheduler_running, start_scheduler, stop_scheduler
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
        assert report["counts"]["stale_analysis_jobs"] == 1
        assert report["errors"] == []
    finally:
        db.close()
