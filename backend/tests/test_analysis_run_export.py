"""AnalysisRun Standard/Debug evidence package coverage."""
from __future__ import annotations

import json
import os
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_export_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)

from app.analysis_workflow.constants import ArtifactType, RunStatus  # noqa: E402
from app.analysis_workflow.exporter import (  # noqa: E402
    AnalysisExportSizeLimitExceeded,
    AnalysisRunExporter,
    verify_export_package,
)
from app.analysis_workflow.models import AnalysisNode, AnalysisNodeAttempt  # noqa: E402
from app.analysis_workflow.recorder import WorkflowAuditRecorder  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import analysis_engine, model_client  # noqa: E402
from app.v2_models import AnalysisJob, AnalysisRun, PortfolioSnapshot  # noqa: E402
from test_analysis_workflow_audit_integration import _register_and_seed  # noqa: E402
from test_true_multi_agent_workflow import _run, environment  # noqa: E402,F401


def _download(client: TestClient, headers: dict[str, str], run_id: int, mode: str, target: Path) -> Path:
    response = client.get(f"/api/v2/analysis/runs/{run_id}/export?mode={mode}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment" in response.headers["content-disposition"]
    target.write_bytes(response.content)
    return target


def _json_from_zip(path: Path, member: str) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read(member).decode("utf-8"))


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def _create_terminal_run(snapshot_id: int, *, status: str) -> int:
    init_db()
    with SessionLocal() as db:
        snapshot = db.get(PortfolioSnapshot, snapshot_id)
        assert snapshot is not None
        job = AnalysisJob(
            user_id=snapshot.user_id,
            portfolio_id=snapshot.portfolio_id,
            snapshot_id=snapshot.id,
            trigger_type="manual",
            checkpoint="15:10",
            mode="deep",
            status="running",
            current_stage="running",
            notify=False,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        recorder = WorkflowAuditRecorder(db)
        run = recorder.start_run(job, analysis_mode="deep", skill_version="test-export")
        recorder.start_stage("context_loading")
        recorder.record_artifact(
            ArtifactType.PORTFOLIO_SNAPSHOT,
            {"id": snapshot.id, "holdings": []},
            artifact_key="portfolio_snapshot",
        )
        recorder.start_node("context_loader")
        recorder.start_attempt()
        recorder.finish_attempt(output={"status": "ready"})
        recorder.finish_node(output={"status": "ready"})
        recorder.finish_stage()
        recorder.record_artifact(
            ArtifactType.MARKET_SNAPSHOT,
            {"captured_at": "2026-09-07T15:10:00+08:00", "quality_grade": "A", "quotes": {}},
            artifact_key="market_snapshot",
        )
        if status == RunStatus.BLOCKED:
            recorder.record_artifact(
                ArtifactType.QUALITY_GATE,
                {"status": "blocked", "grade": "F", "reason": "fixture"},
                artifact_key="quality_gate",
            )
            recorder.finish_run(
                RunStatus.BLOCKED,
                summary="Blocked fixture",
                data_quality_grade="F",
                structured_payload={"result": {"final_rating": "watch_only", "candidate_actions": []}},
                blocked=True,
            )
        else:
            recorder.start_stage("research_verdict")
            recorder.start_node("research_manager")
            recorder.start_attempt(provider="fixture", model="fixture-model")
            recorder.fail_run(
                RuntimeError(f"{status}_fixture_failure"),
                cancelled=status == RunStatus.CANCELLED,
                interrupted=status == RunStatus.INTERRUPTED,
            )
        db.refresh(run)
        assert run.status == status
        return run.id


def _second_user_headers(client: TestClient) -> dict[str, str]:
    suffix = uuid.uuid4().hex
    email = f"export-other-{suffix}@example.com"
    assert client.post("/api/v2/auth/register", json={"email": email, "password": "password123"}).status_code == 201
    response = client.post("/api/v2/auth/login", json={"email": email, "password": "password123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_completed_deep_run_exports_standard_and_debug_without_reanalysis(environment, monkeypatch, tmp_path):
    harness, make_job, client, headers = environment
    run = _run(make_job("deep"))
    assert run.status == RunStatus.COMPLETED
    with SessionLocal() as db:
        before_attempts = db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run.id).count()
        before_artifacts = db.query(AnalysisRun).filter_by(id=run.id).one().last_artifact_id

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("export must not call live analysis dependencies")

    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", unexpected_call)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", unexpected_call)
    monkeypatch.setattr(model_client, "call_model", unexpected_call)

    standard = _download(client, headers, run.id, "standard", tmp_path / "standard.zip")
    standard_names = _zip_names(standard)
    assert {
        "manifest.json",
        "README.md",
        "run/analysis-run.json",
        "run/timeline.json",
        "evidence/evidence-index.json",
        "evidence/sources.json",
        "debate/investment-claims.json",
        "debate/risk-claims.json",
        "decision/final-decision.json",
        "errors/failures.json",
        "reports/full-analysis.md",
    } <= standard_names
    assert "agents/news_analyst/prompt-template.json" not in standard_names
    manifest = _json_from_zip(standard, "manifest.json")
    assert manifest["run_status"] == RunStatus.COMPLETED
    assert manifest["export_mode"] == "standard"
    assert "manifest.json" not in {item["path"] for item in manifest["files"]}
    assert verify_export_package(standard)["valid"] is True
    final = _json_from_zip(standard, "decision/final-decision.json")
    assert final["candidate_actions"] == []
    evidence = _json_from_zip(standard, "evidence/evidence-index.json")
    assert evidence["evidence"]
    assert evidence["legacy_ref_to_evidence_id"]

    debug = _download(client, headers, run.id, "debug", tmp_path / "debug.zip")
    debug_names = _zip_names(debug)
    assert {
        "evidence/evidence-snapshot.json",
        "evidence/raw-artifacts-index.json",
        "artifacts/index.json",
        "prompts/manifest.json",
        "checkpoints/checkpoints.json",
        "workflow/node-attempts.json",
        "workflow/stage-records.json",
        "errors/raw-errors.json",
        "agents/news_analyst/input.json",
        "agents/news_analyst/prompt-template.json",
        "agents/news_analyst/rendered-prompt.json",
        "agents/news_analyst/structured-output.json",
        "agents/news_analyst/raw-output.txt",
        "agents/news_analyst/attempts.json",
        "agents/news_analyst/metadata.json",
    } <= debug_names
    assert verify_export_package(debug)["valid"] is True
    attempts = _json_from_zip(debug, "agents/news_analyst/attempts.json")["attempts"]
    assert attempts
    assert {
        "attempt_no",
        "status",
        "started_at",
        "completed_at",
        "latency_ms",
        "provider",
        "model",
        "request_id",
        "input_hash",
        "output_hash",
        "input_tokens",
        "output_tokens",
        "transport_retry_count",
        "structured_retry_count",
        "failure_class",
        "error_type",
        "error_code",
        "error_message",
        "retryable",
    } <= set(attempts[0])
    with SessionLocal() as db:
        assert db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run.id).count() == before_attempts
        assert db.query(AnalysisRun).filter_by(id=run.id).one().last_artifact_id == before_artifacts


def test_export_redacts_secrets_and_hidden_reasoning_from_debug_package(environment, tmp_path):
    _harness, make_job, client, headers = environment
    run = _run(make_job("deep"))
    raw = json.dumps(
        {
            "visible": "public conclusion",
            "reasoning": "PRIVATE_REASONING_SENTINEL",
            "api_key": "API_KEY_EXPORT_SENTINEL",
            "Authorization": "Bearer AUTHORIZATION_EXPORT_SENTINEL",
            "cookie": "COOKIE_EXPORT_SENTINEL",
            "password": "PASSWORD_EXPORT_SENTINEL",
            "secret": "SECRET_EXPORT_SENTINEL",
            "refresh_token": "REFRESH_TOKEN_EXPORT_SENTINEL",
            "webhook": "WEBHOOK_EXPORT_SENTINEL",
            "note": "Bearer BEARER_EXPORT_SENTINEL",
        }
    )
    with SessionLocal() as db:
        node = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="news_analyst").one()
        attempt = db.query(AnalysisNodeAttempt).filter_by(node_id=node.id).order_by(AnalysisNodeAttempt.id.desc()).first()
        assert attempt is not None
        recorder = WorkflowAuditRecorder(db)
        recorder.run_id = run.id
        artifact = recorder.record_artifact(
            ArtifactType.MODEL_RAW_OUTPUT,
            raw,
            artifact_key="export_sentinel.raw",
            node_id=node.id,
            attempt_id=attempt.id,
        )
        attempt.raw_output_artifact_id = artifact.id
        db.commit()

    debug = _download(client, headers, run.id, "debug", tmp_path / "redacted-debug.zip")
    with zipfile.ZipFile(debug) as archive:
        payload = b"\n".join(archive.read(name) for name in archive.namelist())
    text = payload.decode("utf-8", errors="replace")
    for sentinel in (
        "PRIVATE_REASONING_SENTINEL",
        "API_KEY_EXPORT_SENTINEL",
        "AUTHORIZATION_EXPORT_SENTINEL",
        "COOKIE_EXPORT_SENTINEL",
        "PASSWORD_EXPORT_SENTINEL",
        "SECRET_EXPORT_SENTINEL",
        "REFRESH_TOKEN_EXPORT_SENTINEL",
        "WEBHOOK_EXPORT_SENTINEL",
        "BEARER_EXPORT_SENTINEL",
    ):
        assert sentinel not in text
    assert "public conclusion" in text
    assert verify_export_package(debug)["valid"] is True


@pytest.mark.parametrize("run_status", [RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.CANCELLED, RunStatus.INTERRUPTED])
def test_terminal_runs_export_and_user_isolation(run_status, tmp_path):
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)
    run_id = _create_terminal_run(snapshot_id, status=run_status)
    package = _download(client, headers, run_id, "standard", tmp_path / f"{run_status}.zip")
    manifest = _json_from_zip(package, "manifest.json")
    assert manifest["run_status"] == run_status
    assert verify_export_package(package)["valid"] is True
    names = _zip_names(package)
    assert {"run/timeline.json", "errors/failures.json", "decision/final-decision.json"} <= names
    if run_status == RunStatus.BLOCKED:
        assert {"evidence/quality-gate.json", "market/market-snapshot.json", "portfolio/portfolio-snapshot.json"} <= names
    other_headers = _second_user_headers(client)
    denied = client.get(f"/api/v2/analysis/runs/{run_id}/export?mode=debug", headers=other_headers)
    assert denied.status_code == 404


def test_debug_export_of_legacy_partial_run_and_tamper_verification(tmp_path):
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)
    init_db()
    with SessionLocal() as db:
        snapshot = db.get(PortfolioSnapshot, snapshot_id)
        assert snapshot is not None
        job = AnalysisJob(
            user_id=snapshot.user_id,
            portfolio_id=snapshot.portfolio_id,
            snapshot_id=snapshot.id,
            mode="deep",
            status="succeeded",
            notify=False,
        )
        db.add(job)
        db.commit()
        recorder = WorkflowAuditRecorder(db)
        run = recorder.start_run(job, analysis_mode="deep", skill_version="legacy-fixture")
        recorder.finish_run(RunStatus.COMPLETED, summary="Old minimal run", structured_payload={"result": {"candidate_actions": []}})
        run_id = run.id
    package = _download(client, headers, run_id, "debug", tmp_path / "partial-debug.zip")
    assert _json_from_zip(package, "artifacts/index.json")["partial_debug"] is True
    assert verify_export_package(package)["valid"] is True
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(package) as archive:
        archive.extractall(extracted)
    decision = extracted / "decision" / "final-decision.json"
    decision.write_text('{"tampered":true}', encoding="utf-8")
    result = verify_export_package(extracted)
    assert result["valid"] is False
    assert any(item.startswith("sha256_mismatch:decision/final-decision.json") for item in result["errors"])
    with SessionLocal() as db:
        exporter = AnalysisRunExporter(db, max_bytes=1)
        with pytest.raises(AnalysisExportSizeLimitExceeded):
            exporter.build_standard_package(run_id)
