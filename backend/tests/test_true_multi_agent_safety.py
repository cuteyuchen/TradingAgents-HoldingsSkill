"""Failure isolation, frozen contracts and cooperative cancellation for CORE-3."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from test_true_multi_agent_workflow import environment, _run
from app.analysis_workflow import agents
from app.analysis_workflow.dag import ANALYST_ROLES, RISK_ROLES
from app.analysis_workflow.models import AnalysisArtifact, AnalysisNode, AnalysisNodeAttempt
from app.analysis_workflow.recorder import WorkflowAuditRecorder
from app.clock import utc_now
from app.database import SessionLocal
from app.services import analysis_engine
from app.services.analysis_lease import reclaim_running_analysis_job
from app.system.workers import signal_worker
from app.v2_models import AnalysisJob, AnalysisRun, ModelProfile, PortfolioSnapshot


@pytest.mark.parametrize("message,failure_class", [
    ("429 rate limit", "transient"),
    ("maximum context length exceeded", "context_overflow"),
])
def test_node_retry_preserves_binding_and_does_not_restart_siblings(environment, message, failure_class):
    harness, make_job, *_ = environment
    harness.failures[("news_analyst", 1)] = RuntimeError(message)
    run = _run(make_job())
    assert run.status == "completed", run.error_message
    assert harness.counts()["news_analyst"] == 2
    assert all(harness.counts()[key] == 1 for key in ANALYST_ROLES if key != "news_analyst")
    news = [call["payload"] for call in harness.calls if call["key"] == "news_analyst"]
    for field in ("analysis_run_id", "portfolio_snapshot_id", "evidence_snapshot_id", "evidence_hash", "market_snapshot_at"):
        assert news[0][field] == news[1][field]
    assert news[0]["input"]["snapshot"] == news[1]["input"]["snapshot"]
    assert news[0]["input"]["portfolio_context"] == news[1]["input"]["portfolio_context"]
    with SessionLocal() as db:
        node = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="news_analyst").one()
        attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).order_by(AnalysisNodeAttempt.attempt_no).all()
        assert [attempt.status for attempt in attempts] == ["failed", "completed"]
        assert attempts[0].failure_class == failure_class
        prompts = db.query(AnalysisArtifact).filter_by(node_id=node.id, artifact_key="news_analyst.rendered").order_by(AnalysisArtifact.id).all()
        assert [prompt.content_json["context_mode"] for prompt in prompts] == ["full", "compressed" if failure_class == "context_overflow" else "full"]


def _pause_before_quality(monkeypatch, phase="quality_gate"):
    stage = analysis_engine._job_stage
    stopped = False

    def pause(db, job, name, progress):
        nonlocal stopped
        if name == phase and not stopped:
            stopped = True
            raise RuntimeError("simulated_interruption")
        return stage(db, job, name, progress)

    monkeypatch.setattr(analysis_engine, "_job_stage", pause)


@pytest.mark.parametrize("changed", ["portfolio", "profile", "prompt", "mode", "checkpoint"])
def test_resume_rejects_changed_frozen_contract(environment, monkeypatch, changed):
    harness, make_job, client, headers = environment
    _pause_before_quality(monkeypatch)
    job_id = make_job()
    first = _run(job_id)
    assert first.status == "failed"
    before = harness.counts()
    if changed == "prompt":
        monkeypatch.setattr(agents, "PROMPT_VERSION", "changed-template")
    else:
        with SessionLocal() as db:
            job = db.get(AnalysisJob, job_id)
            if changed == "portfolio":
                snapshot = db.get(PortfolioSnapshot, job.snapshot_id)
                snapshot.broker_available_cash += 1
            elif changed == "profile":
                profile = db.query(ModelProfile).filter_by(user_id=job.user_id, purpose="analysis").one()
                profile.model_name = "changed-model"
            elif changed == "mode":
                job.mode = "standard"
            else:
                job.checkpoint = "changed-checkpoint"
            db.commit()
    response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        resumed = db.get(AnalysisRun, first.id)
        assert resumed.status == "failed"
        assert resumed.error_code == "resume_input_hash_mismatch"
        assert db.query(AnalysisArtifact).filter_by(analysis_run_id=first.id, artifact_key="evidence_snapshot").count() == 1
    assert harness.counts() == before


@pytest.mark.parametrize("phase", ["quality_gate", "research_verdict"])
def test_skipped_optional_node_stays_skipped_on_resume(environment, monkeypatch, phase):
    harness, make_job, client, headers = environment
    harness.failures[("lockup_supply_analyst", 1)] = RuntimeError("401 optional source denied")
    _pause_before_quality(monkeypatch, phase)
    job_id = make_job()
    first = _run(job_id)
    assert first.status == "failed"
    response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    assert harness.counts()["lockup_supply_analyst"] == 1
    with SessionLocal() as db:
        resumed = db.get(AnalysisRun, first.id)
        assert resumed.status == "completed", resumed.error_message
        quality = resumed.structured_result_json["result"]["quality_gate"]
        assert quality["agent_statuses"]["lockup_supply_analyst"] == "SKIPPED"
        assert "lockup_supply_analyst" in quality["agent_failures"]


def test_risk_failure_is_visible_and_blocks_new_risk(environment):
    harness, make_job, *_ = environment
    harness.failures[("conservative_risk_agent", 1)] = RuntimeError("401 risk profile denied")
    harness.unsafe = True
    run = _run(make_job())
    assert run.status == "completed", run.error_message
    final = run.structured_result_json["result"]
    assert final["quality_gate"]["grade"] == "C"
    assert final["quality_gate"]["risk_increase_allowed"] is False
    assert final["candidate_actions"] == []
    assert all(row["action"] not in {"add", "conditional_add"} for row in final["holdings"])
    synthesis = next(call["payload"] for call in harness.calls if call["key"] == "risk_synthesis")
    assert synthesis["agent_statuses"]["conservative_risk_agent"] == "FAILED"
    assert "conservative_risk_agent" in synthesis["agent_failures"]
    assert all(harness.counts()[key] == 1 for key in RISK_ROLES)


@pytest.mark.parametrize("revise", [False, True])
def test_late_resume_restores_normalized_managers_and_retries_only_synthesis(environment, revise):
    harness, make_job, client, headers = environment
    harness.revise = revise
    harness.failures[("risk_synthesis", 1)] = RuntimeError("401 synthesis profile denied")
    job_id = make_job()
    first = _run(job_id)
    assert first.status == "failed"
    before = harness.counts()
    response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        resumed = db.get(AnalysisRun, first.id)
        assert resumed.status == "completed", resumed.error_message
    assert harness.counts()["risk_synthesis"] == 2
    assert harness.counts()["portfolio_manager"] == 1
    assert all(harness.counts()[key] == count for key, count in before.items() if key != "risk_synthesis")


def test_each_parallel_node_owns_session_and_recorder_in_worker(environment, monkeypatch):
    harness, make_job, *_ = environment
    factory = WorkflowAuditRecorder.node_scope_factory
    scopes = []
    lock = threading.Lock()

    def capture(coordinator):
        open_scope = factory(coordinator)

        def open_in_worker():
            recorder = open_scope()
            with lock:
                scopes.append((recorder, recorder.db, threading.get_ident()))
            return recorder

        return open_in_worker

    monkeypatch.setattr(WorkflowAuditRecorder, "node_scope_factory", capture)
    main_thread = threading.get_ident()
    run = _run(make_job())
    assert run.status == "completed", run.error_message
    assert len({id(recorder) for recorder, _, _ in scopes}) == len(scopes)
    assert len({id(db) for _, db, _ in scopes}) == len(scopes)
    assert all(thread != main_thread for _, _, thread in scopes)
    assert {call["thread"] for call in harness.calls if call["key"] in ANALYST_ROLES} <= {thread for _, _, thread in scopes}


def test_failed_final_refresh_cannot_reuse_old_prices(environment, monkeypatch):
    harness, make_job, *_ = environment
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: {
        **market, "final_quote_refresh_status": "failed", "final_quote_refresh_at": utc_now().isoformat(),
    })
    run = _run(make_job())
    assert run.status == "failed"
    assert run.failed_node == "final_quote_refresh"
    assert "portfolio_manager" not in harness.counts()
    with SessionLocal() as db:
        assert db.query(AnalysisArtifact).filter_by(analysis_run_id=run.id, artifact_key="final_quote_refresh.response").count() == 1


def test_resume_rejects_expired_final_quotes_without_refetching_evidence(environment, monkeypatch):
    harness, make_job, client, headers = environment
    stage = analysis_engine._job_stage
    stopped = False

    def pause(db, job, name, progress):
        nonlocal stopped
        if name == "portfolio_synthesis" and not stopped:
            stopped = True
            raise RuntimeError("stopped_after_final_refresh")
        return stage(db, job, name, progress)

    monkeypatch.setattr(analysis_engine, "_job_stage", pause)
    job_id = make_job()
    first = _run(job_id)
    assert first.status == "failed"
    calls_before = harness.counts()
    future_time = utc_now() + timedelta(minutes=3)
    monkeypatch.setattr(analysis_engine, "utc_now", lambda: future_time)
    response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        resumed = db.get(AnalysisRun, first.id)
        assert resumed.status == "failed"
        assert "final_quote_snapshot_expired" in resumed.error_message
    assert harness.counts() == calls_before


def _wait_for_completed_sibling(job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            run = db.query(AnalysisRun).filter_by(job_id=job_id).first()
            if run is not None:
                done = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="market_analyst", status="succeeded").first()
                waiting = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="news_analyst", status="running").first()
                if done and waiting:
                    return
        time.sleep(0.02)
    pytest.fail("parallel phase did not reach the cancellation test barrier")


@pytest.mark.parametrize("user_cancelled", [True, False])
def test_parallel_cancel_or_shutdown_preserves_results_and_allows_manual_resume(environment, monkeypatch, user_cancelled):
    harness, make_job, client, headers = environment
    harness.block_keys = {"news_analyst", "fundamentals_analyst", "policy_analyst"}
    job_id = make_job()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run, job_id)
        try:
            _wait_for_completed_sibling(job_id)
            if user_cancelled:
                response = client.post(f"/api/v2/analysis/jobs/{job_id}/cancel", headers=headers)
                assert response.status_code == 200, response.text
                retry = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
                assert retry.status_code == 409
            else:
                assert signal_worker("analysis", job_id)
        finally:
            harness.release.set()
        first = future.result(timeout=15)
    assert first.status == ("cancelled" if user_cancelled else "interrupted"), first.error_message
    assert first.resumable
    assert "portfolio_manager" not in harness.counts()
    calls_before = harness.counts()
    with SessionLocal() as db:
        completed = [node.node_key for node in db.query(AnalysisNode).filter_by(analysis_run_id=first.id, status="succeeded")]
        assert "market_analyst" in completed
        assert not reclaim_running_analysis_job(db, job_id=job_id)
    _run(job_id)
    assert harness.counts() == calls_before
    harness.block_keys.clear()

    def forbidden_collection(_codes):
        pytest.fail("resume must reuse frozen market evidence")

    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", forbidden_collection)
    response = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        resumed = db.get(AnalysisRun, first.id)
        assert resumed.status == "completed", resumed.error_message
        assert not db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=first.id, status="running").count()
    assert all(harness.counts()[key] == calls_before[key] for key in completed if key in ANALYST_ROLES)
