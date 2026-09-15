"""V3-CORE-2 node executor, retry, resume, and audit isolation tests."""
from __future__ import annotations

import os
import sys
import uuid

import pytest
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_workflow_executor_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)

from app.analysis_workflow.constants import (  # noqa: E402
    ArtifactType,
    CheckpointName,
    FailureClass,
    NodeStatus,
    RunStatus,
)
from app.analysis_workflow.executor import NodeExecutor  # noqa: E402
from app.analysis_workflow.failures import ResumeRejected, classify_failure  # noqa: E402
from app.analysis_workflow.models import AnalysisArtifact, AnalysisNode, AnalysisNodeAttempt, AnalysisStage  # noqa: E402
from app.analysis_workflow.policy import NodeRetryPolicy  # noqa: E402
from app.analysis_workflow.recorder import WorkflowAuditRecorder  # noqa: E402
from app.analysis_workflow.resume import is_run_resumable, validate_resume_inputs  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app as _app  # noqa: E402,F401
from app.services.model_client import StructuredOutputError  # noqa: E402
from app.v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot, User  # noqa: E402


def _db() -> Session:
    init_db()
    return SessionLocal()


def _job(db: Session) -> AnalysisJob:
    suffix = uuid.uuid4().hex[:10]
    user = User(email=f"exec-{suffix}@example.com", username=f"exec{suffix}", password_hash="hash")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name="Executor")
    db.add(portfolio)
    db.flush()
    snapshot = PortfolioSnapshot(user_id=user.id, portfolio_id=portfolio.id, status="confirmed")
    db.add(snapshot)
    db.flush()
    job = AnalysisJob(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        status="running",
        current_stage="running",
        mode="deep",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_classify_failure_classes():
    assert classify_failure(TimeoutError("timed out")) == FailureClass.TRANSIENT
    assert classify_failure(ConnectionError("connection reset")) == FailureClass.TRANSIENT
    assert classify_failure(RuntimeError("upstream 503 unavailable")) == FailureClass.TRANSIENT
    assert classify_failure(StructuredOutputError("INVALID_JSON", "invalid json")) == FailureClass.STRUCTURED_OUTPUT
    assert classify_failure(RuntimeError("maximum context length exceeded")) == FailureClass.CONTEXT_OVERFLOW
    assert classify_failure(RuntimeError("401 unauthorized")) == FailureClass.NON_RETRYABLE
    assert classify_failure(RuntimeError("model not found")) == FailureClass.NON_RETRYABLE
    assert classify_failure(RuntimeError("default_analysis_model_not_configured")) == FailureClass.NON_RETRYABLE
    assert classify_failure(ResumeRejected("mismatch")) == FailureClass.NON_RETRYABLE
    assert classify_failure(RuntimeError("research_manager_failed")) == FailureClass.NON_RETRYABLE


def test_transient_failure_retries_and_keeps_attempts():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("research_verdict")
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("503 bad gateway")
            return {"rating": "Hold"}

        result = NodeExecutor(recorder).execute("research_manager", boom)
        recorder.finish_stage(output=result.output)
        node = db.query(AnalysisNode).filter_by(analysis_run_id=recorder.run_id, node_key="research_manager").one()
        attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).order_by(AnalysisNodeAttempt.attempt_no.asc()).all()
        assert result.status == NodeStatus.SUCCEEDED
        assert calls["n"] == 2
        assert [item.attempt_no for item in attempts] == [1, 2]
        assert attempts[0].status == "failed"
        assert attempts[0].failure_class == FailureClass.TRANSIENT
        assert attempts[1].status == "completed"
        assert node.attempt_count == 2
        assert db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).count() == 2
    finally:
        recorder.close()
        db.close()


def test_structured_failure_retries_then_succeeds():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("research_verdict")
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            if calls["n"] == 1:
                raise StructuredOutputError("INVALID_JSON", "invalid json")
            return {"rating": "Hold", "winner": "balanced"}

        result = NodeExecutor(recorder).execute("research_manager", boom)
        node = db.query(AnalysisNode).filter_by(analysis_run_id=recorder.run_id, node_key="research_manager").one()
        attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).order_by(AnalysisNodeAttempt.attempt_no.asc()).all()
        assert result.status == NodeStatus.SUCCEEDED
        assert attempts[0].failure_class == FailureClass.STRUCTURED_OUTPUT
        assert attempts[1].status == "completed"
        assert node.status == NodeStatus.SUCCEEDED
    finally:
        recorder.close()
        db.close()


def test_structured_exhaustion_still_starts_a_fresh_node_attempt():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("research_verdict")
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            if calls["n"] == 1:
                raise StructuredOutputError("INVALID_JSON", "invalid json", retry_count=2)
            return {"rating": "Hold"}

        result = NodeExecutor(recorder).execute("research_manager", boom)
        node = db.query(AnalysisNode).filter_by(analysis_run_id=recorder.run_id, node_key="research_manager").one()
        attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).order_by(AnalysisNodeAttempt.attempt_no.asc()).all()
        assert result.status == NodeStatus.SUCCEEDED
        assert calls["n"] == 2
        assert attempts[0].failure_class == FailureClass.STRUCTURED_OUTPUT
        assert attempts[0].structured_retry_count == 2
        assert [item.attempt_no for item in attempts] == [1, 2]
    finally:
        recorder.close()
        db.close()


def test_context_overflow_advances_full_compressed_minimal():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("research_verdict")
        modes: list[str] = []

        def boom(context_mode: str = "full"):
            modes.append(context_mode)
            if context_mode != "minimal":
                raise RuntimeError("maximum context length exceeded")
            return {"rating": "Hold", "context_mode": context_mode}

        result = NodeExecutor(recorder).execute("research_manager", boom, input_payload={"notes": ["x"] * 20})
        node = db.query(AnalysisNode).filter_by(analysis_run_id=recorder.run_id, node_key="research_manager").one()
        assert result.status == NodeStatus.SUCCEEDED
        assert modes == ["full", "compressed", "minimal"]
        assert node.attempt_count == 3
        attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).order_by(AnalysisNodeAttempt.attempt_no.asc()).all()
        assert [item.failure_class for item in attempts[:2]] == [FailureClass.CONTEXT_OVERFLOW, FailureClass.CONTEXT_OVERFLOW]
    finally:
        recorder.close()
        db.close()


def test_auth_failure_does_not_retry_node():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("research_verdict")
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise RuntimeError("401 unauthorized")

        with pytest.raises(RuntimeError, match="401"):
            NodeExecutor(recorder).execute("research_manager", boom)
        node = db.query(AnalysisNode).filter_by(analysis_run_id=recorder.run_id, node_key="research_manager").one()
        assert calls["n"] == 1
        assert node.attempt_count == 1
        assert node.status == NodeStatus.FAILED
        attempt = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).one()
        assert attempt.failure_class == FailureClass.NON_RETRYABLE
        assert attempt.retryable is False
    finally:
        recorder.close()
        db.close()


def test_config_failure_does_not_retry_node():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("research_verdict")

        def boom():
            raise RuntimeError("default_analysis_model_not_configured")

        with pytest.raises(RuntimeError, match="not_configured"):
            NodeExecutor(recorder).execute("research_manager", boom)
        node = db.query(AnalysisNode).filter_by(analysis_run_id=recorder.run_id, node_key="research_manager").one()
        assert node.attempt_count == 1
    finally:
        recorder.close()
        db.close()


def test_optional_node_skips_after_terminal_failure():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("candidate_screening")
        result = NodeExecutor(recorder).execute("candidate_llm_review", lambda: (_ for _ in ()).throw(RuntimeError("401 unauthorized")))
        assert result.status == NodeStatus.SKIPPED
        node = db.query(AnalysisNode).filter_by(analysis_run_id=recorder.run_id, node_key="candidate_llm_review").one()
        assert node.status == NodeStatus.SKIPPED
        assert node.attempt_count == 1
    finally:
        recorder.close()
        db.close()


def test_required_json_fail_closed_still_fails_important_node():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("investment_debate")
        with pytest.raises(StructuredOutputError, match="invalid json"):
            NodeExecutor(recorder).execute(
                "investment_debate_legacy",
                lambda: (_ for _ in ()).throw(StructuredOutputError("INVALID_JSON", "invalid json")),
                fail_closed=True,
            )
        node = db.query(AnalysisNode).filter_by(analysis_run_id=recorder.run_id, node_key="investment_debate_legacy").one()
        assert node.status == NodeStatus.FAILED
        assert node.attempt_count == 3
    finally:
        recorder.close()
        db.close()


def test_resume_skips_completed_node_and_adds_new_attempt():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        run = recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("analysts_running")
        NodeExecutor(recorder).execute("analyst_team_legacy", lambda: {"quality_grade": "A"})
        recorder.finish_stage(output={"quality_grade": "A"})
        recorder.start_stage("research_verdict")
        with pytest.raises(RuntimeError, match="research_manager_failed"):
            NodeExecutor(recorder).execute("research_manager", lambda: (_ for _ in ()).throw(RuntimeError("research_manager_failed")))
        recorder.fail_run(RuntimeError("research_manager_failed"))
        db.refresh(run)
        assert run.status == RunStatus.FAILED
        assert is_run_resumable(run) is True
        first_attempts = db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run.id).count()

        calls = []
        recorder.start_run(job, analysis_mode="deep", resume=True)
        recorder.start_stage("analysts_running")
        analyst = NodeExecutor(recorder).execute("analyst_team_legacy", lambda: calls.append("analyst") or {"quality_grade": "A"})
        assert analyst.skipped is True
        assert "analyst" not in calls
        recorder.finish_stage()
        recorder.start_stage("research_verdict")
        research = NodeExecutor(recorder).execute("research_manager", lambda: calls.append("research") or {"rating": "Hold"})
        assert research.skipped is False
        assert calls == ["research"]
        node = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="research_manager").one()
        attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).order_by(AnalysisNodeAttempt.attempt_no.asc()).all()
        assert [item.attempt_no for item in attempts] == [1, 2]
        assert attempts[0].status == "failed"
        assert attempts[1].status == "completed"
        assert db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run.id).count() > first_attempts
        assert db.query(AnalysisStage).filter_by(analysis_run_id=run.id, phase_key="analysts_running").count() == 1
    finally:
        recorder.close()
        db.close()


def test_retry_does_not_purge_history():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        run = recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("context_loading")
        recorder.start_node("context_loader")
        recorder.start_attempt()
        recorder.finish_attempt()
        recorder.finish_node()
        recorder.finish_stage()
        artifact_count = db.query(AnalysisArtifact).filter_by(analysis_run_id=run.id).count()
        with pytest.raises(RuntimeError, match="forbids purging"):
            recorder._purge_children(run.id)
        recorder.start_run(job, analysis_mode="deep", resume=True)
        assert db.query(AnalysisStage).filter_by(analysis_run_id=run.id).count() == 1
        assert db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run.id).count() == 1
        assert db.query(AnalysisArtifact).filter_by(analysis_run_id=run.id).count() == artifact_count
        assert run.id == recorder.run_id
    finally:
        recorder.close()
        db.close()


def test_force_restart_replays_from_start_without_deleting_attempts():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        run = recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("context_loading")
        NodeExecutor(recorder).execute("context_loader", lambda: {"ok": 1})
        recorder.finish_stage()
        recorder.fail_run(RuntimeError("later_failed"))
        first_attempts = db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run.id).count()
        recorder.start_run(job, analysis_mode="deep", force_restart=True)
        recorder.start_stage("context_loading")
        result = NodeExecutor(recorder).execute("context_loader", lambda: {"ok": 2})
        assert result.skipped is False
        node = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="context_loader").one()
        assert node.attempt_count == 2
        assert db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).count() == first_attempts + 1
        assert recorder.run_id == run.id
    finally:
        recorder.close()
        db.close()


def test_interrupted_run_is_resumable():
    db = _db()
    recorder = WorkflowAuditRecorder(db)
    try:
        job = _job(db)
        run = recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("context_loading")
        NodeExecutor(recorder).execute("context_loader", lambda: {"ok": True})
        recorder.finish_stage()
        recorder.fail_run(RuntimeError("crash"), cancelled=False)
        run.status = RunStatus.INTERRUPTED
        run.interrupted_at = run.completed_at
        run.resumable = True
        db.commit()
        db.refresh(run)
        assert is_run_resumable(run) is True
        assert run.last_checkpoint == CheckpointName.CONTEXT_READY
        recorder.start_run(job, analysis_mode="deep", resume=True)
        recorder.start_stage("context_loading")
        skipped = NodeExecutor(recorder).execute("context_loader", lambda: {"ok": False})
        assert skipped.skipped is True
        assert skipped.output == {"ok": True}
    finally:
        recorder.close()
        db.close()


def test_input_hash_mismatch_blocks_resume():
    with pytest.raises(ResumeRejected) as exc:
        validate_resume_inputs({"portfolio_snapshot": "aaa"}, {"portfolio_snapshot": "bbb"})
    assert "portfolio_snapshot" in exc.value.mismatches
    validate_resume_inputs({"portfolio_snapshot": "aaa"}, {"portfolio_snapshot": "aaa", "evidence_pack": "new"})


def test_retry_policy_separates_node_and_model_attempts():
    from app.analysis_workflow.constants import node_spec

    policy = NodeRetryPolicy()
    spec = node_spec("research_manager")
    assert policy.max_attempts_for(spec) == 3
    assert policy.should_retry(spec, FailureClass.TRANSIENT, 1) is True
    assert policy.should_retry(spec, FailureClass.TRANSIENT, 3) is False
    assert policy.should_retry(spec, FailureClass.NON_RETRYABLE, 1) is False
    gate = node_spec("quality_gate")
    assert policy.max_attempts_for(gate) == 1
    assert policy.should_retry(gate, FailureClass.TRANSIENT, 1) is False


def test_audit_session_isolation_survives_business_rollback():
    business = _db()
    audit = WorkflowAuditRecorder(business_db=business)
    try:
        job = _job(business)
        run = audit.start_run(job, analysis_mode="deep")
        audit.start_stage("context_loading")
        audit.record_artifact(ArtifactType.INPUT, {"ok": True}, artifact_key="isolation")
        job.error_message = "should-not-commit"
        leaked = User(
            email=f"uncommitted-{uuid.uuid4().hex[:8]}@example.com",
            username=f"uncommitted{uuid.uuid4().hex[:8]}",
            password_hash="hash",
        )
        business.add(leaked)
        business.rollback()
        other = SessionLocal()
        try:
            persisted = other.query(AnalysisRun).filter_by(id=run.id).one()
            assert persisted.id == run.id
            assert other.query(AnalysisArtifact).filter_by(analysis_run_id=run.id, artifact_key="isolation").count() == 1
            fresh_job = other.query(AnalysisJob).filter_by(id=job.id).one()
            assert fresh_job.error_message is None
            assert other.query(User).filter_by(email=leaked.email).first() is None
        finally:
            other.close()
    finally:
        audit.close()
        business.close()


def test_audit_commit_does_not_commit_business_objects():
    business = _db()
    audit = WorkflowAuditRecorder(business_db=business)
    try:
        job = _job(business)
        leaked = User(
            email=f"ghost-{uuid.uuid4().hex[:8]}@example.com",
            username=f"ghost{uuid.uuid4().hex[:8]}",
            password_hash="hash",
        )
        business.add(leaked)
        run = audit.start_run(job, analysis_mode="deep")
        other = SessionLocal()
        try:
            assert other.query(AnalysisRun).filter_by(id=run.id).one().id == run.id
            assert other.query(User).filter_by(email=leaked.email).first() is None
        finally:
            other.close()
        business.rollback()
    finally:
        audit.close()
        business.close()


def test_isolated_audit_commits_while_business_has_open_read():
    business = _db()
    audit = WorkflowAuditRecorder(business_db=business)
    try:
        job = _job(business)
        business.query(AnalysisJob).filter_by(id=job.id).first()
        run = audit.start_run(job, analysis_mode="deep")
        audit.start_stage("context_loading")
        audit.record_artifact(ArtifactType.INPUT, {"lock": True}, artifact_key="read-lock")
        other = SessionLocal()
        try:
            assert other.query(AnalysisArtifact).filter_by(analysis_run_id=run.id, artifact_key="read-lock").count() == 1
        finally:
            other.close()
    finally:
        audit.close()
        business.close()
