"""Analysis completion and restart recovery must converge the durable claim."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update

from app import main
from app.database import Base, SessionLocal
from app.market_models import TradingCalendar
from app.operations import workflow
from app.operations.models import DailyOperationalCheckpoint, DailyOperationalRun
from app.services import analysis_engine, scheduler
from app.services.analysis_lease import renew_analysis_checkpoint_lease
from app.system.startup import collect_startup_recovery_report
from app.v2_models import AnalysisJob, Portfolio, PortfolioSnapshot, User
from test_analysis_workflow_audit_integration import (
    _empty_candidates, _market_ok, _phase_payload, _register_and_seed,
)

DAY = date(2026, 8, 20)
NOW = datetime(2026, 8, 20, 2, 40)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _seed(db, *, status="succeeded", checkpoint="09:35", job_id=None, claim_id=None):
    portfolio = db.query(Portfolio).first()
    if portfolio is None:
        user = User(email="lifecycle@example.com", username="lifecycle", password_hash="hash")
        db.add(user)
        db.flush()
        portfolio = Portfolio(user_id=user.id, name="Lifecycle")
        db.add(portfolio)
        db.flush()
        db.add(PortfolioSnapshot(
            user_id=user.id, portfolio_id=portfolio.id, status="confirmed",
            snapshot_time=NOW - timedelta(hours=2),
        ))
        db.add(TradingCalendar(market="CN", trade_date=DAY, is_open=True))
        db.flush()
    snapshot = db.query(PortfolioSnapshot).first()
    job = AnalysisJob(
        user_id=portfolio.user_id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        mode="standard", status=status, checkpoint=checkpoint, trigger_type="scheduled",
        idempotency_key=workflow.checkpoint_idempotency_key(portfolio.id, DAY, checkpoint),
        finished_at=NOW - timedelta(minutes=20) if status in {"succeeded", "failed", "cancelled"} else None,
        error_message="injected failure" if status == "failed" else None,
    )
    if job_id is not None:
        job.id = job_id
    db.add(job)
    db.flush()
    claim = DailyOperationalCheckpoint(
        user_id=portfolio.user_id, portfolio_id=portfolio.id, trade_date=DAY,
        checkpoint_name=checkpoint, status="RUNNING", job_id=job.id,
        claimed_at=NOW - timedelta(hours=1), lease_expires_at=NOW - timedelta(minutes=30),
        attempt_count=1, metadata_json={"source": "created"}, last_error="old error",
    )
    if claim_id is not None:
        claim.id = claim_id
    db.add(claim)
    op_run = workflow.ensure_operational_run(
        db, user_id=portfolio.user_id, portfolio_id=portfolio.id, trade_date=DAY,
    )
    op_run.checkpoint_state_json = {
        **dict(op_run.checkpoint_state_json or {}),
        checkpoint: {"status": "SUCCESS", "job_id": job.id, "source": "created"},
    }
    db.commit()
    return portfolio, job, claim, op_run


def _assert_finished(claim, job, op_run, expected):
    assert claim.status == expected
    assert claim.completed_at == job.finished_at
    assert claim.lease_expires_at is None
    assert claim.attempt_count == 1
    record = op_run.checkpoint_state_json[claim.checkpoint_name]
    assert record["status"] == expected
    assert record["job_id"] == job.id
    assert record["source"] == "created"
    if expected == "SUCCESS":
        assert claim.last_error is None
    else:
        assert claim.last_error
        if job.error_message:
            assert claim.last_error == job.error_message


@pytest.mark.parametrize("status,expected", [("succeeded", "SUCCESS"), ("failed", "FAILED"), ("cancelled", "SKIPPED")])
def test_analysis_runner_writes_checkpoint_terminal_state(monkeypatch, status, expected):
    monkeypatch.setattr(analysis_engine.settings, "TRUE_MULTI_AGENT_WORKFLOW_ENABLED", False)
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)
    monkeypatch.setattr(analysis_engine, "_candidate_context_for_analysis", _empty_candidates)
    monkeypatch.setattr(analysis_engine, "_structured_call_json",
                        lambda _profile, _system, _payload, _instruction, phase_name: _phase_payload(phase_name))
    monkeypatch.setattr(analysis_engine.AnalysisLeaseHeartbeat, "for_job", lambda *args, **kwargs: None)
    _, snapshot_id = _register_and_seed(TestClient(main.app))
    with SessionLocal() as session:
        snapshot = session.get(PortfolioSnapshot, snapshot_id)
        job = AnalysisJob(user_id=snapshot.user_id, portfolio_id=snapshot.portfolio_id,
                          snapshot_id=snapshot.id, mode="standard", status="queued", notify=False)
        session.add(job)
        session.flush()
        job_id = job.id
        claim = DailyOperationalCheckpoint(
            user_id=job.user_id, portfolio_id=job.portfolio_id, trade_date=DAY,
            checkpoint_name="09:35", job_id=job.id, status="RUNNING",
            claimed_at=NOW, lease_expires_at=NOW + timedelta(minutes=10), attempt_count=1,
        )
        session.add(claim)
        op_run = workflow.ensure_operational_run(session, user_id=job.user_id,
                                                 portfolio_id=job.portfolio_id, trade_date=DAY)
        op_run.checkpoint_state_json = {"09:35": {"status": "RUNNING", "job_id": job.id, "source": "created"}}
        session.commit()
        claim_id, run_id = claim.id, op_run.id

    if status != "succeeded":
        def fail_snapshot_check(*_args):
            if status == "cancelled":
                with SessionLocal() as session:
                    session.get(AnalysisJob, job_id).status = "cancelled"
                    session.commit()
            raise RuntimeError("injected failure")
        monkeypatch.setattr(analysis_engine, "snapshot_identity_issues", fail_snapshot_check)

    analysis_engine.run_analysis_job(job_id)
    with SessionLocal() as session:
        job = session.get(AnalysisJob, job_id)
        assert job.status == status, job.error_message
        assert job.finished_at is not None
        _assert_finished(session.get(DailyOperationalCheckpoint, claim_id), job,
                         session.get(DailyOperationalRun, run_id), expected)


def test_reconciliation_repairs_production_shape_and_is_idempotent(db):
    _, job4, claim18, op_run = _seed(db, job_id=4, claim_id=18)
    _, job5, claim19, _ = _seed(db, checkpoint="10:30", job_id=5, claim_id=19)
    changed = workflow.reconcile_operational_checkpoints(db, now=NOW.replace(tzinfo=UTC))
    assert {claim.id for claim in changed} == {18, 19}
    db.commit()
    _assert_finished(claim18, job4, op_run, "SUCCESS")
    _assert_finished(claim19, job5, op_run, "SUCCESS")
    previous = (claim18.updated_at, claim19.updated_at, dict(op_run.checkpoint_state_json), op_run.updated_at)
    assert workflow.reconcile_operational_checkpoints(db, now=NOW + timedelta(hours=1)) == []
    db.commit()
    db.expire_all()
    assert (claim18.updated_at, claim19.updated_at, op_run.checkpoint_state_json, op_run.updated_at) == previous


def test_cancelled_before_runner_starts_finishes_checkpoint(db, monkeypatch):
    _, job, claim, op_run = _seed(db, status="cancelled")
    job.finished_at = None
    db.commit()
    monkeypatch.setattr(analysis_engine, "SessionLocal", lambda: Session(db.bind, autoflush=False))
    analysis_engine.run_analysis_job(job.id)
    db.expire_all()
    assert job.finished_at is not None
    _assert_finished(claim, job, op_run, "SKIPPED")


def test_terminal_job_converges_before_lease_expiry(db):
    _, job, claim, op_run = _seed(db)
    claim.lease_expires_at = NOW + timedelta(minutes=10)
    db.commit()
    assert workflow.reconcile_operational_checkpoints(db, now=NOW) == [claim]
    _assert_finished(claim, job, op_run, "SUCCESS")


def test_concurrent_reconciliation_updates_checkpoint_once(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
    barrier = threading.Barrier(2)
    def synchronize_reads(_conn, _cursor, statement, _params, _context, _many):
        if "FROM daily_operational_checkpoints JOIN analysis_jobs" in statement:
            barrier.wait(timeout=10)
    event.listen(engine, "after_cursor_execute", synchronize_reads)
    def reconcile():
        with Session(engine, autoflush=False) as session:
            changed = workflow.reconcile_operational_checkpoints(session, now=NOW)
            ids = [claim.id for claim in changed]
            session.commit()
            return ids
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reconcile(), range(2)))
        assert sorted(len(result) for result in results) == [0, 1]
        with Session(engine) as session:
            claim = session.query(DailyOperationalCheckpoint).one()
            _assert_finished(claim, session.get(AnalysisJob, claim.job_id),
                             session.query(DailyOperationalRun).one(), "SUCCESS")
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_reads)
        engine.dispose()


@pytest.mark.parametrize("status,expected", [("failed", "FAILED"), ("cancelled", "SKIPPED")])
def test_reconciliation_preserves_terminal_errors(db, status, expected):
    _, job, claim, op_run = _seed(db, status=status)
    assert workflow.reconcile_operational_checkpoints(db, now=NOW) == [claim]
    db.commit()
    _assert_finished(claim, job, op_run, expected)
    assert workflow.reconcile_operational_checkpoints(db, now=NOW) == []


@pytest.mark.parametrize("status", ["queued", "running", "retrying"])
@pytest.mark.parametrize("lease", ["expired", "live", "heartbeat"])
def test_reconciliation_never_finishes_active_job(db, status, lease):
    _, job, claim, op_run = _seed(db, status=status)
    if lease != "expired":
        claim.lease_expires_at = NOW + timedelta(minutes=2)
    db.commit()
    if lease == "heartbeat":
        assert renew_analysis_checkpoint_lease(db, job_id=job.id, attempt_count=1, now=NOW)
        db.refresh(claim)
    before = (claim.status, claim.lease_expires_at, claim.completed_at, dict(op_run.checkpoint_state_json))
    assert workflow.reconcile_operational_checkpoints(db, now=NOW) == []
    assert (claim.status, claim.lease_expires_at, claim.completed_at, op_run.checkpoint_state_json) == before


@pytest.mark.parametrize("untrusted", ["missing_job", "wrong_user", "wrong_portfolio", "cancel_requested"])
def test_reconciliation_requires_trusted_finished_job(db, untrusted):
    _, job, claim, _ = _seed(db)
    if untrusted == "missing_job":
        claim.job_id = None
    elif untrusted == "wrong_user":
        job.user_id += 100
    elif untrusted == "wrong_portfolio":
        job.portfolio_id += 100
    else:
        job.status = "cancelled"
        job.finished_at = None
    db.commit()
    assert workflow.reconcile_operational_checkpoints(db, now=NOW) == []
    assert claim.status == "RUNNING"
    assert claim.completed_at is None


def test_reconciliation_preserves_reused_checkpoint(db):
    _, _, claim, op_run = _seed(db)
    claim.status = "REUSED"
    claim.completed_at = NOW - timedelta(hours=1)
    claim.lease_expires_at = None
    op_run.checkpoint_state_json = {"09:35": {"status": "REUSED"}}
    db.commit()
    assert workflow.reconcile_operational_checkpoints(db, now=NOW) == []
    assert claim.status == "REUSED"
    assert op_run.checkpoint_state_json["09:35"]["status"] == "REUSED"


@pytest.mark.parametrize("status,expected", [("succeeded", "SUCCESS"), ("failed", "FAILED"), ("cancelled", "SKIPPED")])
def test_expired_claim_with_finished_job_converges_without_reclaim(db, status, expected):
    portfolio, job, claim, op_run = _seed(db, status=status)
    with Session(db.bind) as contender:
        stale_view = contender.get(DailyOperationalCheckpoint, claim.id)
        result, owned, reclaimed = workflow._claim_checkpoint(
            db, portfolio=portfolio, trade_date=DAY, checkpoint_name="09:35", now=NOW,
        )
        assert result.id == claim.id
        assert (owned, reclaimed) == (False, False)
        db.commit()
        _assert_finished(claim, job, op_run, expected)
        result, owned, reclaimed = workflow._claim_checkpoint(
            contender, portfolio=contender.get(Portfolio, portfolio.id), trade_date=DAY,
            checkpoint_name="09:35", now=NOW,
        )
        assert result.id == stale_view.id
        assert result.status == expected
        assert (owned, reclaimed) == (False, False)
        assert result.attempt_count == 1
    assert db.query(AnalysisJob).count() == 1


def test_job_finishing_between_read_and_reclaim_is_not_claimed(db, monkeypatch):
    portfolio, job, claim, op_run = _seed(db, status="running")
    execute = db.execute
    intercepted = []
    def finish_before_reclaim(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "daily_operational_checkpoints":
            if statement.compile().params.get("status") == "CLAIMED":
                intercepted.append(True)
                job.status = "succeeded"
                job.finished_at = NOW
                db.flush()
        return execute(statement, *args, **kwargs)
    monkeypatch.setattr(db, "execute", finish_before_reclaim)
    _, owned, reclaimed = workflow._claim_checkpoint(
        db, portfolio=portfolio, trade_date=DAY, checkpoint_name="09:35", now=NOW,
    )
    assert intercepted == [True]
    assert (owned, reclaimed) == (False, False)
    _assert_finished(claim, job, op_run, "SUCCESS")


def test_run_due_repairs_checkpoint_even_when_json_is_already_success(db, monkeypatch):
    portfolio, job, claim, op_run = _seed(db)
    op_run.checkpoint_state_json = {
        checkpoint.key: {"status": "SUCCESS", "job_id": job.id, "source": "created"}
        for checkpoint in workflow.CHECKPOINTS
    }
    db.commit()
    def unexpected_thread(*_args, **_kwargs):
        pytest.fail("finished checkpoint must not start another worker")
    monkeypatch.setattr(workflow.threading, "Thread", unexpected_thread)
    result = workflow.run_due_checkpoints(db, portfolio=portfolio, now=NOW.replace(tzinfo=UTC))
    assert result["started_jobs"] == []
    _assert_finished(claim, job, op_run, "SUCCESS")


def test_reconciliation_flushes_without_committing_callers_transaction(db):
    _, _, claim, _ = _seed(db)
    workflow.reconcile_operational_checkpoints(db, now=NOW)
    db.rollback()
    assert claim.status == "RUNNING"
    assert claim.completed_at is None


def test_run_due_persists_reconciliation_on_non_trading_day(db):
    portfolio, job, claim, op_run = _seed(db)
    result = workflow.run_due_checkpoints(db, portfolio=portfolio, now=NOW.replace(tzinfo=UTC) + timedelta(days=2))
    assert result["status"] == "NON_TRADING_DAY"
    db.rollback()
    _assert_finished(claim, job, op_run, "SUCCESS")


def test_startup_report_no_longer_counts_converged_checkpoints(db):
    _seed(db, job_id=4, claim_id=18)
    _seed(db, checkpoint="10:30", job_id=5, claim_id=19)
    assert collect_startup_recovery_report(db)["counts"]["stale_checkpoints"] == 2
    workflow.reconcile_operational_checkpoints(db, now=NOW)
    db.commit()
    report = collect_startup_recovery_report(db)
    assert report["counts"]["stale_checkpoints"] == 0
    assert report["errors"] == []


def test_scheduler_reconciles_even_without_portfolios(db):
    _, job, claim, op_run = _seed(db)
    scheduler._run_daily_operations(db, now_utc=NOW.replace(tzinfo=UTC), portfolios=[])
    db.commit()
    _assert_finished(claim, job, op_run, "SUCCESS")


def test_scheduler_tick_reconciles_before_calendar_early_return(db, monkeypatch):
    _, job, claim, op_run = _seed(db)
    db.query(TradingCalendar).delete()
    db.commit()
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: Session(db.bind, autoflush=False))
    monkeypatch.setattr(scheduler, "_dispatch_research_backtests", lambda _db: None)
    monkeypatch.setattr(scheduler, "_sync_monitor_lifecycle", lambda *args, **kwargs: None)
    scheduler.tick_schedules()
    db.expire_all()
    _assert_finished(claim, job, op_run, "SUCCESS")


def test_lifespan_reconciles_before_starting_scheduler(db, monkeypatch):
    _seed(db, job_id=4, claim_id=18)
    _seed(db, checkpoint="10:30", job_id=5, claim_id=19)
    monkeypatch.setattr(main, "SessionLocal", lambda: Session(db.bind))
    for name in ("configure_logging", "ensure_pre_upgrade_backup", "init_db", "sync_runtime_provider_health",
                 "start_remote_market_identity_sync", "start_realtime_monitor", "stop_scheduler",
                 "stop_realtime_monitor", "stop_remote_market_identity_sync", "signal_workers"):
        monkeypatch.setattr(main, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "run_startup_preflight", lambda _db: {"blocked": False})
    monkeypatch.setattr(main, "hydrate_runtime_provider_health", lambda _db: 0)
    monkeypatch.setattr(main, "initialize_local_market_identity", lambda: {"status": "ready"})
    monkeypatch.setattr(main, "runtime_prompt", lambda: "test")
    monkeypatch.setattr(main, "runtime_metadata", lambda: {"name": "test", "version": "1", "runtime_sha256": "test"})
    monkeypatch.setattr(analysis_engine, "CORE_RULES", analysis_engine.CORE_RULES)
    monkeypatch.setattr(main.auth, "ensure_token", lambda: None)
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)
    from app.history import sync
    monkeypatch.setattr(sync, "reclaim_stale_history_sync_runs", lambda _db: [])
    reports = []
    def start_scheduler():
        with Session(db.bind) as session:
            reports.append(collect_startup_recovery_report(session))
    monkeypatch.setattr(main, "start_scheduler", start_scheduler)
    async def start_and_stop():
        async with main.lifespan(main.app):
            assert reports[0]["counts"]["stale_checkpoints"] == 0
    asyncio.run(start_and_stop())
