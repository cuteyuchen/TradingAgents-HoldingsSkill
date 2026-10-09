"""Analysis completion and restart recovery must converge the durable claim."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, null
from sqlalchemy.orm import Query, Session
from sqlalchemy.sql.dml import Update

from app import main
from app.database import Base, SessionLocal
from app.market_models import TradingCalendar
from app.operations import workflow
from app.operations.models import DailyOperationalCheckpoint, DailyOperationalRun
from app.services import analysis_engine, scheduler
from app.services.analysis_lease import renew_analysis_checkpoint_lease
from app.system.startup import collect_startup_recovery_report
from app.v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot, User
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
        if "FROM daily_operational_checkpoints JOIN analysis_jobs" in statement and "daily_operational_checkpoints.id," in statement:
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


@pytest.mark.parametrize("already_terminal", [False, True])
def test_lifespan_reconciles_before_starting_scheduler(db, monkeypatch, already_terminal):
    _seed(db, job_id=4, claim_id=18)
    _seed(db, checkpoint="10:30", job_id=5, claim_id=19)
    for claim in db.query(DailyOperationalCheckpoint).all():
        if already_terminal:
            claim.status, claim.completed_at, claim.lease_expires_at = "SUCCESS", db.get(AnalysisJob, claim.job_id).finished_at, None
    for op_run in db.query(DailyOperationalRun).all():
        op_run.checkpoint_state_json = {key: {**record, "status": "RUNNING"}
                                        for key, record in op_run.checkpoint_state_json.items()}
    db.commit()
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
            for op_run in session.query(DailyOperationalRun).all():
                assert all(record["status"] == "SUCCESS" for record in op_run.checkpoint_state_json.values())
    monkeypatch.setattr(main, "start_scheduler", start_scheduler)
    async def start_and_stop():
        async with main.lifespan(main.app):
            assert reports[0]["counts"]["stale_checkpoints"] == 0
    asyncio.run(start_and_stop())


def _runner_fixture(monkeypatch):
    monkeypatch.setattr(analysis_engine.settings, "TRUE_MULTI_AGENT_WORKFLOW_ENABLED", False)
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)
    monkeypatch.setattr(analysis_engine, "_candidate_context_for_analysis", _empty_candidates)
    monkeypatch.setattr(analysis_engine, "_structured_call_json",
                        lambda _profile, _system, _payload, _instruction, phase_name: _phase_payload(phase_name))
    monkeypatch.setattr(analysis_engine.AnalysisLeaseHeartbeat, "for_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(analysis_engine, "utc_now", lambda: NOW.replace(tzinfo=UTC))
    client = TestClient(main.app)
    headers, snapshot_id = _register_and_seed(client)
    with SessionLocal() as session:
        snapshot = session.get(PortfolioSnapshot, snapshot_id)
        job = AnalysisJob(user_id=snapshot.user_id, portfolio_id=snapshot.portfolio_id,
                          snapshot_id=snapshot.id, mode="standard", status="queued", notify=False,
                          checkpoint="09:35", trigger_type="scheduled",
                          idempotency_key=workflow.checkpoint_idempotency_key(snapshot.portfolio_id, DAY, "09:35"))
        session.add(job)
        session.flush()
        claim = DailyOperationalCheckpoint(
            user_id=job.user_id, portfolio_id=job.portfolio_id, trade_date=DAY,
            checkpoint_name="09:35", job_id=job.id, status="RUNNING", attempt_count=1,
            claimed_at=NOW - timedelta(hours=1), lease_expires_at=NOW - timedelta(minutes=30),
            metadata_json={"source": "created"},
        )
        session.add(claim)
        op_run = workflow.ensure_operational_run(session, user_id=job.user_id,
                                                 portfolio_id=job.portfolio_id, trade_date=DAY)
        op_run.checkpoint_state_json = {"09:35": {"status": "RUNNING", "job_id": job.id, "source": "created"}}
        session.commit()
        return client, headers, job.id, claim.id, op_run.id


@pytest.mark.parametrize("old_outcome", ["exception", "success", "stop"])
@pytest.mark.parametrize("new_finished", [False, True])
def test_obsolete_runner_cannot_fail_or_complete_new_generation(monkeypatch, old_outcome, new_finished):
    _, _, job_id, claim_id, run_id = _runner_fixture(monkeypatch)
    old_waiting, new_waiting = threading.Event(), threading.Event()
    release_old, release_new = threading.Event(), threading.Event()
    calls = []
    def interleaved_market(codes):
        calls.append(threading.current_thread().name)
        if len(calls) == 1:
            old_waiting.set()
            assert release_old.wait(15)
            if old_outcome != "success":
                raise RuntimeError("old worker exit")
        else:
            new_waiting.set()
            assert release_new.wait(15)
        return _market_ok(codes)
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", interleaved_market)
    from app.system.workers import signal_worker, worker_active
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(analysis_engine.run_analysis_job, job_id)
        try:
            assert old_waiting.wait(15)
            if old_outcome == "stop":
                assert signal_worker("analysis", job_id)
            with SessionLocal() as session:
                job = session.get(AnalysisJob, job_id)
                claim, owned, reclaimed = workflow._claim_checkpoint(
                    session, portfolio=session.get(Portfolio, job.portfolio_id), trade_date=DAY,
                    checkpoint_name="09:35", now=NOW,
                )
                assert (owned, reclaimed) == (True, True)
                admission = workflow._admit_checkpoint_job(
                    session, portfolio=session.get(Portfolio, job.portfolio_id), trade_date=DAY,
                    checkpoint="09:35", mode="standard", now=NOW, reclaim=True,
                )
                assert admission.job.id == job_id and admission.should_start
                workflow._finish_checkpoint_claim(claim, status="RUNNING", local=NOW, job_id=job_id)
                session.commit()
            new = pool.submit(analysis_engine.run_analysis_job, job_id)
            assert new_waiting.wait(15)
            if new_finished:
                release_new.set()
                new.result(timeout=15)
            with SessionLocal() as session:
                job = session.get(AnalysisJob, job_id)
                claim = session.get(DailyOperationalCheckpoint, claim_id)
                run = session.query(AnalysisRun).filter_by(job_id=job_id).one()
                before = (job.status, job.finished_at, job.error_message, run.status, run.completed_at,
                          claim.status, claim.lease_expires_at, claim.completed_at, claim.attempt_count,
                          session.get(DailyOperationalRun, run_id).checkpoint_state_json)
            release_old.set()
            old.result(timeout=15)
            with SessionLocal() as session:
                # The next tick must not be able to amplify a stale Job write.
                workflow.reconcile_operational_checkpoints(session, now=NOW)
                session.commit()
                job = session.get(AnalysisJob, job_id)
                claim = session.get(DailyOperationalCheckpoint, claim_id)
                run = session.query(AnalysisRun).filter_by(job_id=job_id).one()
                assert (job.status, job.finished_at, job.error_message, run.status, run.completed_at,
                        claim.status, claim.lease_expires_at, claim.completed_at, claim.attempt_count,
                        session.get(DailyOperationalRun, run_id).checkpoint_state_json) == before
                if not new_finished:
                    assert worker_active("analysis", job_id)
            release_new.set()
            new.result(timeout=15)
            with SessionLocal() as session:
                assert session.get(AnalysisJob, job_id).status == "succeeded"
                assert session.get(DailyOperationalCheckpoint, claim_id).status == "SUCCESS"
                assert session.get(DailyOperationalRun, run_id).checkpoint_state_json["09:35"]["status"] == "SUCCESS"
        finally:
            release_old.set()
            release_new.set()


@pytest.mark.parametrize("first_status", ["failed", "cancelled"])
@pytest.mark.parametrize("retry_outcome", ["succeeded", "failed"])
def test_real_retry_reclaims_same_checkpoint_and_converges(monkeypatch, first_status, retry_outcome):
    client, headers, job_id, claim_id, run_id = _runner_fixture(monkeypatch)
    def initial_exit(*_args):
        if first_status == "cancelled":
            response = client.post(f"/api/v2/analysis/jobs/{job_id}/cancel", headers=headers)
            assert response.status_code == 200, response.text
        raise RuntimeError("first attempt failed")
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", initial_exit)
    analysis_engine.run_analysis_job(job_id)
    with SessionLocal() as session:
        assert session.get(AnalysisJob, job_id).status == first_status
        assert session.get(DailyOperationalCheckpoint, claim_id).status == {"failed": "FAILED", "cancelled": "SKIPPED"}[first_status]
        analysis_run_id = session.query(AnalysisRun).filter_by(job_id=job_id).one().id
        job = session.get(AnalysisJob, job_id)
        protected = []
        state = dict(session.get(DailyOperationalRun, run_id).checkpoint_state_json)
        for name, status in [("10:30", "REUSED"), ("13:05", "SUCCESS"), ("14:30", "MISSED"), ("auction", "FAILED")]:
            marker = DailyOperationalCheckpoint(user_id=job.user_id, portfolio_id=job.portfolio_id,
                trade_date=DAY, checkpoint_name=name, job_id=job_id, status=status, attempt_count=1,
                completed_at=NOW, lease_expires_at=None)
            session.add(marker)
            session.flush()
            protected.append((marker.id, name, status))
            state[name] = {"status": status, "job_id": job_id}
        session.get(DailyOperationalRun, run_id).checkpoint_state_json = state
        session.commit()
    def retry_market(codes):
        with SessionLocal() as session:
            claim = session.get(DailyOperationalCheckpoint, claim_id)
            assert claim.status in {"CLAIMED", "RUNNING"}
            assert claim.attempt_count == 2
            assert claim.job_id == job_id and claim.completed_at is None
            assert claim.lease_expires_at is not None
            assert session.get(DailyOperationalRun, run_id).checkpoint_state_json["09:35"]["status"] == "RUNNING"
        if retry_outcome == "failed":
            raise RuntimeError("retry failed")
        return _market_ok(codes)
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", retry_market)
    response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    with SessionLocal() as session:
        job = session.get(AnalysisJob, job_id)
        assert job.status == retry_outcome, job.error_message
        claim = session.get(DailyOperationalCheckpoint, claim_id)
        assert claim.attempt_count == 2
        assert claim.status == {"succeeded": "SUCCESS", "failed": "FAILED"}[retry_outcome]
        assert claim.completed_at == job.finished_at and claim.lease_expires_at is None
        assert session.get(DailyOperationalRun, run_id).checkpoint_state_json["09:35"]["status"] == claim.status
        assert session.query(AnalysisJob).filter_by(idempotency_key=job.idempotency_key).count() == 1
        assert session.query(DailyOperationalCheckpoint).filter_by(portfolio_id=job.portfolio_id, trade_date=DAY,
                                                                  checkpoint_name="09:35").count() == 1
        assert session.query(AnalysisRun).filter_by(job_id=job_id).one().id == analysis_run_id
        for marker_id, name, status in protected:
            marker = session.get(DailyOperationalCheckpoint, marker_id)
            assert (marker.status, marker.attempt_count, marker.lease_expires_at) == (status, 1, None)
            assert session.get(DailyOperationalRun, run_id).checkpoint_state_json[name]["status"] == status
    if retry_outcome == "succeeded":
        assert client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers).status_code == 409


def test_scheduler_stale_json_cannot_overwrite_worker_terminal_commit(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler-worker.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        portfolio, job, claim, op_run = _seed(session, status="running")
        portfolio_id, job_id, claim_id, run_id = portfolio.id, job.id, claim.id, op_run.id
        op_run.checkpoint_state_json = {cp.key: {"status": "SUCCESS"} for cp in workflow.CHECKPOINTS}
        op_run.checkpoint_state_json["09:35"] = {"status": "RUNNING", "job_id": job_id, "source": "created"}
        claim.lease_expires_at = NOW + timedelta(minutes=10)
        session.commit()
    original_status = workflow._checkpoint_status
    interleaved = []
    def worker_finishes_after_scheduler_copies_state(**kwargs):
        if not interleaved and kwargs["checkpoint"].key == workflow.ANALYSIS_CHECKPOINTS[-1].key:
            interleaved.append(True)
            with Session(engine, autoflush=False) as worker:
                job = worker.get(AnalysisJob, job_id)
                job.status, job.finished_at = "succeeded", NOW
                workflow.finish_analysis_job_checkpoints(worker, job)
                worker.commit()
        return original_status(**kwargs)
    monkeypatch.setattr(workflow, "_checkpoint_status", worker_finishes_after_scheduler_copies_state)
    try:
        with Session(engine, autoflush=False) as session:
            workflow.run_due_checkpoints(session, portfolio=session.get(Portfolio, portfolio_id), now=NOW.replace(tzinfo=UTC))
        with Session(engine) as session:
            assert session.get(DailyOperationalCheckpoint, claim_id).status == "SUCCESS"
            assert session.get(DailyOperationalRun, run_id).checkpoint_state_json["09:35"]["status"] == "SUCCESS"
    finally:
        engine.dispose()


@pytest.mark.parametrize("repair", ["startup", "scheduler", "non_trading_day"])
@pytest.mark.parametrize("job_status,claim_status", [("succeeded", "SUCCESS"), ("failed", "FAILED"), ("cancelled", "SKIPPED")])
def test_terminal_checkpoint_repairs_stale_run_json_across_days(db, monkeypatch, repair, job_status, claim_status):
    portfolio, job, claim, op_run = _seed(db, status=job_status)
    claim.status, claim.completed_at, claim.lease_expires_at = claim_status, job.finished_at, None
    claim.last_error = None if claim_status == "SUCCESS" else "injected failure"
    op_run.checkpoint_state_json = {"09:35": {"status": "RUNNING", "job_id": job.id, "source": "created"},
                                    "daily_review": {"status": "PENDING", "review_id": 123}}
    db.commit()
    later = NOW.replace(tzinfo=UTC) + timedelta(days=2)
    if repair == "startup":
        # Exercise the same pre-scheduler reconciliation used by lifespan.
        workflow.reconcile_operational_checkpoints(db, now=later)
        db.commit()
    elif repair == "scheduler":
        scheduler._run_daily_operations(db, now_utc=later, portfolios=[])
        db.commit()
    else:
        assert workflow.run_due_checkpoints(db, portfolio=portfolio, now=later)["status"] == "NON_TRADING_DAY"
        db.rollback()
    db.expire_all()
    assert op_run.checkpoint_state_json["09:35"]["status"] == claim_status
    assert op_run.checkpoint_state_json["daily_review"] == {"status": "PENDING", "review_id": 123}
    before = (claim.updated_at, op_run.updated_at, dict(op_run.checkpoint_state_json))
    assert workflow.reconcile_operational_checkpoints(db, now=later) == []
    db.commit()
    db.expire_all()
    assert (claim.updated_at, op_run.updated_at, op_run.checkpoint_state_json) == before


def test_duplicate_dispatch_does_not_unregister_active_owner(monkeypatch):
    client, headers, job_id, claim_id, run_id = _runner_fixture(monkeypatch)
    waiting, release = threading.Event(), threading.Event()
    def paused_market(codes):
        waiting.set()
        assert release.wait(15)
        return _market_ok(codes)
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", paused_market)
    from app.system.workers import worker_active
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(analysis_engine.run_analysis_job, job_id)
        try:
            assert waiting.wait(15)
            analysis_engine.run_analysis_job(job_id)
            assert worker_active("analysis", job_id)
            response = client.post(f"/api/v2/analysis/jobs/{job_id}/cancel", headers=headers)
            assert response.status_code == 200, response.text
        finally:
            release.set()
        worker.result(timeout=15)
    with SessionLocal() as session:
        assert session.get(AnalysisJob, job_id).status == "cancelled"
        assert session.query(AnalysisRun).filter_by(job_id=job_id).one().status == "cancelled"
        assert session.get(DailyOperationalCheckpoint, claim_id).status == "SKIPPED"
        assert session.get(DailyOperationalRun, run_id).checkpoint_state_json["09:35"]["status"] == "SKIPPED"


def test_current_owner_shutdown_exit_converges(monkeypatch):
    _, _, job_id, claim_id, run_id = _runner_fixture(monkeypatch)
    from app.system.workers import signal_worker
    def stopped_market(_codes):
        assert signal_worker("analysis", job_id)
        raise RuntimeError("worker stop requested")
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", stopped_market)
    analysis_engine.run_analysis_job(job_id)
    with SessionLocal() as session:
        job = session.get(AnalysisJob, job_id)
        assert job.status == "failed" and job.error_message == "analysis_worker_interrupted"
        assert session.query(AnalysisRun).filter_by(job_id=job_id).one().status == "interrupted"
        assert session.get(DailyOperationalCheckpoint, claim_id).status == "FAILED"
        assert session.get(DailyOperationalRun, run_id).checkpoint_state_json["09:35"]["status"] == "FAILED"


def test_cancelled_startup_reader_cannot_finalize_a_completed_retry(monkeypatch):
    client, headers, job_id, claim_id, run_id = _runner_fixture(monkeypatch)
    assert client.post(f"/api/v2/analysis/jobs/{job_id}/cancel", headers=headers).status_code == 200
    with SessionLocal() as session:
        engine = session.get_bind()
    retried = []
    class InterleavedQuery(Query):
        def first(self):
            row = super().first()
            if not retried and isinstance(row, AnalysisJob) and row.id == job_id and row.status == "cancelled":
                retried.append(True)
                response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
                assert response.status_code == 202, response.text
                with SessionLocal() as session:
                    assert session.get(AnalysisJob, job_id).status == "succeeded"
                # Make any stale finished_at write observable after the retry.
                monkeypatch.setattr(analysis_engine, "utc_now", lambda: (NOW + timedelta(minutes=1)).replace(tzinfo=UTC))
            return row
    monkeypatch.setattr(analysis_engine, "SessionLocal", lambda: Session(engine, autoflush=False, query_cls=InterleavedQuery))
    analysis_engine.run_analysis_job(job_id)
    assert retried == [True]
    with SessionLocal() as session:
        job = session.get(AnalysisJob, job_id)
        assert job.status == "succeeded" and job.finished_at == NOW
        assert session.query(AnalysisRun).filter_by(job_id=job_id).one().status == "completed"
        claim = session.get(DailyOperationalCheckpoint, claim_id)
        assert claim.status == "SUCCESS" and claim.completed_at == job.finished_at
        assert session.get(DailyOperationalRun, run_id).checkpoint_state_json["09:35"]["status"] == "SUCCESS"


@pytest.mark.parametrize("sql_null", [False, True])
def test_terminal_repair_can_persist_missing_json_projection(db, monkeypatch, sql_null):
    _, job, claim, op_run = _seed(db)
    claim.status, claim.completed_at, claim.lease_expires_at = "SUCCESS", job.finished_at, None
    op_run.checkpoint_state_json = null() if sql_null else None
    db.commit()
    execute = db.execute
    attempts = []
    def bounded_execute(statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "daily_operational_runs":
            attempts.append(True)
            assert len(attempts) < 4, "repair kept retrying instead of persisting the missing projection"
        return execute(statement, *args, **kwargs)
    monkeypatch.setattr(db, "execute", bounded_execute)
    assert workflow.reconcile_operational_checkpoints(db, now=NOW) == [claim]
    db.commit()
    db.expire_all()
    assert op_run.checkpoint_state_json["09:35"]["status"] == "SUCCESS"
    assert workflow.reconcile_operational_checkpoints(db, now=NOW) == []


def test_real_retry_of_cancelled_queued_job_renews_active_checkpoint(monkeypatch):
    client, headers, job_id, claim_id, run_id = _runner_fixture(monkeypatch)
    assert client.post(f"/api/v2/analysis/jobs/{job_id}/cancel", headers=headers).status_code == 200
    original_check = analysis_engine.snapshot_identity_issues
    def check_retry_claim(*args):
        with SessionLocal() as session:
            claim = session.get(DailyOperationalCheckpoint, claim_id)
            assert claim.attempt_count == 2
            assert claim.status in {"CLAIMED", "RUNNING"}
            assert claim.lease_expires_at > NOW
        return original_check(*args)
    monkeypatch.setattr(analysis_engine, "snapshot_identity_issues", check_retry_claim)
    response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    with SessionLocal() as session:
        job = session.get(AnalysisJob, job_id)
        assert job.status == "succeeded", job.error_message
        claim = session.get(DailyOperationalCheckpoint, claim_id)
        assert claim.attempt_count == 2 and claim.status == "SUCCESS"
        assert claim.completed_at == job.finished_at and claim.lease_expires_at is None
        assert session.get(DailyOperationalRun, run_id).checkpoint_state_json["09:35"]["status"] == "SUCCESS"
        assert session.query(AnalysisJob).filter_by(idempotency_key=job.idempotency_key).count() == 1
