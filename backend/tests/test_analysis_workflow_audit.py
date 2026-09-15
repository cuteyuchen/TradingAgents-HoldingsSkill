"""V3-CORE-1 workflow audit model, recorder, redaction, and migration tests."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_workflow_audit_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)

from app.analysis_workflow.constants import ArtifactType, CheckpointName, RunStatus  # noqa: E402
from app.analysis_workflow.hashing import canonical_json, sha256_content  # noqa: E402
from app.analysis_workflow.models import (  # noqa: E402
    AnalysisArtifact,
    AnalysisClaim,
    AnalysisNode,
    AnalysisNodeAttempt,
    AnalysisStage,
)
from app.analysis_workflow.recorder import WorkflowAuditRecorder  # noqa: E402
from app.analysis_workflow.resume import is_run_resumable, resume_from_checkpoint  # noqa: E402
from app.analysis_workflow.timeline import build_analysis_timeline  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app as _app  # noqa: E402,F401
from app.system.release import code_head_revision  # noqa: E402
from app.v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot, User  # noqa: E402


def _db() -> Session:
    init_db()
    return SessionLocal()


def _job(db: Session) -> AnalysisJob:
    suffix = uuid.uuid4().hex[:10]
    user = User(email=f"audit-{suffix}@example.com", username=f"audit{suffix}", password_hash="hash")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name="Audit")
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


def test_canonical_json_hash_is_stable():
    left = {"b": 2, "a": [1, {"z": True}]}
    right = {"a": [1, {"z": True}], "b": 2}
    assert canonical_json(left) == canonical_json(right)
    assert sha256_content(left) == sha256_content(right)
    assert len(sha256_content(left)) == 64


def test_analysis_run_lifecycle_fields_and_uniqueness():
    db = _db()
    try:
        job = _job(db)
        recorder = WorkflowAuditRecorder(db)
        run = recorder.start_run(job, analysis_mode="deep", skill_version="test")
        assert run.status == RunStatus.RUNNING
        assert run.started_at is not None
        assert run.workflow_version
        recorder.start_stage("context_loading")
        recorder.start_node("context_loader")
        recorder.start_attempt()
        recorder.finish_attempt()
        recorder.finish_node()
        recorder.finish_stage()
        first_stage = recorder.start_stage("context_loading")
        reused_stage = recorder.start_stage("context_loading")
        assert first_stage.id == reused_stage.id
        recorder.finish_stage()
        recorder.start_stage("market_collecting")
        recorder.start_node("market_snapshot_collector")
        recorder.start_attempt()
        recorder.finish_attempt()
        first_node = recorder.start_node("market_snapshot_collector")
        reused_node = recorder.start_node("market_snapshot_collector")
        assert first_node.id == reused_node.id
        recorder.start_attempt()
        first_attempt = recorder._attempt()
        assert first_attempt is not None
        node = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="market_snapshot_collector").one()
        with pytest.raises(IntegrityError):
            dup = AnalysisNodeAttempt(
                analysis_run_id=run.id,
                stage_id=node.stage_id,
                node_id=node.id,
                attempt_no=first_attempt.attempt_no,
                status="running",
            )
            db.add(dup)
            db.commit()
        db.rollback()
        with pytest.raises(IntegrityError):
            db.add(
                AnalysisStage(
                    analysis_run_id=run.id,
                    phase_key="market_collecting",
                    phase_order=20,
                    display_name="dup",
                    status="pending",
                )
            )
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_recorder_stage_node_attempt_artifact_claim_checkpoint():
    db = _db()
    try:
        job = _job(db)
        recorder = WorkflowAuditRecorder(db)
        run = recorder.start_run(job, analysis_mode="standard")
        recorder.start_stage("investment_debate")
        recorder.start_node("investment_debate_legacy")
        recorder.start_attempt(provider="openai_compatible", model="test-model")
        artifact = recorder.record_artifact(ArtifactType.STRUCTURED_OUTPUT, {"ok": True}, artifact_key="debate.output")
        assert artifact.sha256 == sha256_content(canonical_json({"ok": True}))
        recorder.finish_attempt(output_hash=artifact.sha256, structured_retry_count=2, transport_retry_count=1)
        recorder.finish_node()
        claims = recorder.record_claims(
            [
                {
                    "claim_id": "INV-1",
                    "speaker": "bull",
                    "stance": "bullish",
                    "claim": "趋势向上",
                    "evidence": ["资金流入"],
                    "confidence": 0.7,
                    "status": "open",
                    "target_claim_ids": ["INV-2"],
                }
            ]
        )
        assert claims[0].claim_id == "INV-1"
        recorder.fail_stage(RuntimeError("not used"))
        # stage already finished; fail_stage is a no-op without open stage
        recorder.start_stage("research_verdict")
        recorder.finish_stage(output={"rating": "Hold"})
        recorder.checkpoint(CheckpointName.RESEARCH_DONE)
        recorder.fail_run(RuntimeError("research_manager_failed"))
        db.refresh(run)
        assert run.status == RunStatus.FAILED
        assert db.query(AnalysisStage).filter_by(analysis_run_id=run.id).count() >= 2
        assert db.query(AnalysisNode).filter_by(analysis_run_id=run.id).count() >= 1
        assert db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run.id).count() >= 1
        assert db.query(AnalysisArtifact).filter_by(analysis_run_id=run.id).count() >= 1
        assert db.query(AnalysisClaim).filter_by(analysis_run_id=run.id, claim_id="INV-1").count() == 1
        assert run.last_checkpoint == CheckpointName.RESEARCH_DONE
        assert is_run_resumable(run) is True
        contract = resume_from_checkpoint(run, db)
        assert contract["resumable"] is True
        assert contract["checkpoint"] == CheckpointName.RESEARCH_DONE
        timeline = build_analysis_timeline(db, run.id)
        assert timeline
        assert timeline[0]["type"] == "run_started"
        assert any(item["type"] == "run_failed" for item in timeline)
    finally:
        db.close()


def test_artifact_redaction_strips_secrets():
    db = _db()
    try:
        job = _job(db)
        recorder = WorkflowAuditRecorder(db)
        recorder.start_run(job, analysis_mode="fast")
        recorder.start_stage("context_loading")
        payload = {
            "api_key": "sk-live-should-not-persist",
            "Authorization": "Bearer supersecret-token-value",
            "cookie": "session=abc123456",
            "password": "hunter2-secret",
            "secret": "top-secret-value",
            "webhook": "https://hooks.example/abc",
            "nested": {"access_token": "tok_live_12345678"},
            "note": "Authorization: Bearer leaked-bearer-token-value",
        }
        artifact = recorder.record_artifact(ArtifactType.INPUT, payload, artifact_key="secrets")
        stored = artifact.content_json or {}
        blob = canonical_json(stored) + (artifact.content_text or "")
        for secret in (
            "sk-live-should-not-persist",
            "supersecret-token-value",
            "session=abc123456",
            "hunter2-secret",
            "top-secret-value",
            "https://hooks.example/abc",
            "tok_live_12345678",
            "leaked-bearer-token-value",
        ):
            assert secret not in blob
        assert "[REDACTED]" in blob
    finally:
        db.close()


def test_fail_stage_and_fail_node_keep_prior_rows():
    db = _db()
    try:
        job = _job(db)
        recorder = WorkflowAuditRecorder(db)
        run = recorder.start_run(job, analysis_mode="deep")
        recorder.start_stage("analysts_running")
        recorder.start_node("analyst_team_legacy")
        recorder.start_attempt()
        recorder.finish_attempt()
        recorder.finish_node()
        recorder.finish_stage()
        recorder.start_stage("research_verdict")
        recorder.start_node("research_manager")
        recorder.start_attempt()
        recorder.fail_attempt(RuntimeError("boom"), structured_retry_count=3)
        recorder.fail_node(RuntimeError("boom"))
        recorder.fail_stage(RuntimeError("boom"))
        recorder.fail_run(RuntimeError("boom"))
        assert db.query(AnalysisStage).filter_by(analysis_run_id=run.id, phase_key="analysts_running").one().status == "completed"
        failed_node = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="research_manager").one()
        assert failed_node.status == "failed"
        attempt = db.query(AnalysisNodeAttempt).filter_by(node_id=failed_node.id).one()
        assert attempt.status == "failed"
        assert attempt.error_message
    finally:
        db.close()


def _upgrade(backend_dir: Path, database_path: Path, revision: str) -> None:
    env = os.environ.copy()
    env["ADVISOR_DB_PATH"] = str(database_path)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def _downgrade(backend_dir: Path, database_path: Path, revision: str) -> None:
    env = os.environ.copy()
    env["ADVISOR_DB_PATH"] = str(database_path)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", revision],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_workflow_audit_migration_upgrade_and_downgrade(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "workflow_audit.db"
    _upgrade(backend_dir, database_path, "20260829_0020")
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "analysis_stages" not in tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(analysis_runs)")}
        assert "workflow_version" not in columns

    _upgrade(backend_dir, database_path, "20260905_0021")
    _upgrade(backend_dir, database_path, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (code_head_revision(),)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "analysis_stages",
            "analysis_nodes",
            "analysis_node_attempts",
            "analysis_artifacts",
            "analysis_claims",
        } <= tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(analysis_runs)")}
        assert {"status", "workflow_version", "last_checkpoint", "resumable"} <= columns
        attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(analysis_node_attempts)")}
        assert "failure_class" in attempt_columns

    _downgrade(backend_dir, database_path, "20260829_0020")
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "analysis_stages" not in tables
        assert "analysis_runs" in tables
