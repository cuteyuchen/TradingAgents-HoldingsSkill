"""Success, failure, blocked, cancel, and authorization tests for workflow audit."""
from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(TEST_DB_DIR, f"test_workflow_int_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_ARTIFACTS_DIR", os.path.join(TEST_DB_DIR, f"test_workflow_int_artifacts_{os.getpid()}"))
os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient  # noqa: E402

from app.analysis_workflow.constants import RunStatus  # noqa: E402
from app.analysis_workflow.models import AnalysisArtifact, AnalysisClaim, AnalysisNode, AnalysisNodeAttempt, AnalysisStage  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import analysis_engine  # noqa: E402
from app.services.security_master import STOCK, upsert_security  # noqa: E402
from app.market.providers.health import reset_runtime_provider_health_registry  # noqa: E402
from app.v2_models import AnalysisJob, AnalysisRun  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_analysis_side_effects(monkeypatch):
    reset_runtime_provider_health_registry()
    monkeypatch.setattr(analysis_engine, "_candidate_context_for_analysis", _empty_candidates)
    yield
    reset_runtime_provider_health_registry()


def _empty_candidates(*_args, **_kwargs):
    return {
        "status": "none",
        "quality_status": "MISSING",
        "confidence": 0.0,
        "run_id": None,
        "watchlist": [],
        "ready": [],
        "action": [],
        "candidates": [],
        "reason": "TEST_STUB",
    }


def _market_ok(codes):
    quotes = {code: {"code": code, "price": 10.0, "source": "test"} for code in codes}
    return {
        "captured_at": "2026-09-05T10:00:00+08:00",
        "quotes": quotes,
        "technicals": {},
        "indices": {},
        "quality_grade": "A",
        "errors": [],
        "source_chain": ["test-source"],
    }


def _market_blocked(codes):
    return {
        "captured_at": "2026-09-05T10:00:00+08:00",
        "quotes": {},
        "technicals": {},
        "indices": {},
        "quality_grade": "F",
        "errors": ["quote coverage missing"],
        "source_chain": ["test-source"],
    }


def _phase_payload(phase_name: str) -> dict:
    if phase_name == "analyst_evidence":
        return {"market_read": "市场平稳", "holding_evidence": [], "portfolio_risks": [], "data_gaps": [], "quality_grade": "A"}
    if phase_name == "investment_debate":
        return {"bull_case": ["趋势向上"], "bear_case": ["估值较高"], "unresolved_claims": [], "manager_verdict": "谨慎持有"}
    if phase_name == "research_verdict":
        return {"rating": "Hold", "winner": "balanced", "strategic_action": "保持观察", "reasoning": "证据平衡"}
    if phase_name in {"trader_proposal", "trader_revision"}:
        return {"orders": [{"code": "600519", "action": "hold", "quantity": None}]}
    if phase_name == "risk_revision":
        return {"decision": "pass", "reason": "无硬性违规", "hard_constraints": [], "soft_constraints": []}
    if phase_name == "risk_debate":
        return {"claims": [
            {"speaker": "aggressive", "claim": "可小仓试错"},
            {"speaker": "neutral", "claim": "维持观察"},
            {"speaker": "conservative", "claim": "先等确认"},
        ]}
    return {
        "data_quality_grade": "A",
        "market_read": "市场平稳",
        "portfolio_conclusion": "继续持有，等待触发条件。",
        "final_rating": "hold",
        "cash_target": "20%-30%",
        "confidence": "medium",
        "holdings": [{"code": "600519", "name": "贵州茅台", "action": "hold", "reason": "平衡", "quantity": None}],
        "candidates": [],
        "history_consistency": "首次分析",
        "bull_case": ["趋势向上"],
        "bear_case": ["估值较高"],
        "unresolved_claims": [],
        "risk_warnings": ["不追高"],
        "evidence": ["test-source"],
    }


def _register_and_seed(client: TestClient) -> tuple[dict[str, str], int]:
    init_db()
    with SessionLocal() as db:
        upsert_security(db, {"code": "600519", "exchange": "SSE", "name": "贵州茅台", "security_type": STOCK})
        db.commit()
    suffix = uuid.uuid4().hex
    email = f"audit-{suffix}@example.com"
    password = "password123"
    assert client.post("/api/v2/auth/register", json={"email": email, "password": password}).status_code == 201
    login = client.post("/api/v2/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    portfolio = client.post("/api/v2/portfolios", headers=headers, json={"name": f"审计-{suffix[:8]}"})
    assert portfolio.status_code == 201, portfolio.text
    provider = client.post(
        "/api/v2/model-settings/providers",
        headers=headers,
        json={"provider": "openai_compatible", "display_name": f"test-{suffix[:8]}", "base_url": "http://model.invalid/v1"},
    )
    assert provider.status_code == 201, provider.text
    profile = client.post(
        "/api/v2/model-settings/profiles",
        headers=headers,
        json={
            "provider_id": provider.json()["id"],
            "purpose": "analysis",
            "model_name": "test-model",
            "parameters": {},
            "is_default": True,
        },
    )
    assert profile.status_code == 201, profile.text
    holdings = {
        "holdings": [{"code": "600519", "name": "贵州茅台", "qty": 100, "available_qty": 80, "cost": 1500, "price": 1600, "market_value": 160000}],
        "total_assets": 200000,
        "total_market_value": 160000,
        "broker_available_cash": 40000,
        "excluded_items": [],
        "notes": [],
    }
    upload = client.post(
        f"/api/v2/portfolios/{portfolio.json()['id']}/uploads",
        headers=headers,
        data={"holdings_json": json.dumps(holdings, ensure_ascii=False)},
        files={"screenshot": ("holdings.png", b"\x89PNG\r\n\x1a\n" + b"test-image", "image/png")},
    )
    assert upload.status_code == 201, upload.text
    snapshot = client.post(f"/api/v2/uploads/{upload.json()['id']}/confirm", headers=headers)
    assert snapshot.status_code == 201, snapshot.text
    return headers, snapshot.json()["id"]


def test_analysis_success_persists_workflow_audit(monkeypatch):
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)
    monkeypatch.setattr(
        analysis_engine,
        "_structured_call_json",
        lambda _profile, _system, _payload, _instruction, phase_name: _phase_payload(phase_name),
    )
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)
    created = client.post(
        "/api/v2/analysis/jobs",
        headers=headers,
        json={"snapshot_id": snapshot_id, "mode": "deep", "checkpoint": "10:00", "notify": False},
    )
    assert created.status_code == 202, created.text
    job = client.get(f"/api/v2/analysis/jobs/{created.json()['id']}", headers=headers)
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    run_id = job.json()["run_id"]
    assert run_id is not None
    report = client.get(f"/api/v2/analysis/runs/{run_id}", headers=headers)
    assert report.status_code == 200
    assert report.json()["markdown"]
    assert report.json()["structured_result"]["result"]
    workflow = client.get(f"/api/v2/analysis/runs/{run_id}/workflow", headers=headers)
    assert workflow.status_code == 200, workflow.text
    assert workflow.json()["run"]["status"] == RunStatus.COMPLETED
    assert workflow.json()["stages"]
    assert any(stage["phase_key"] == "research_verdict" for stage in workflow.json()["stages"])
    nodes = client.get(f"/api/v2/analysis/runs/{run_id}/nodes", headers=headers)
    assert any(node["node_key"] == "research_manager" for node in nodes.json()["nodes"])
    attempts = client.get(f"/api/v2/analysis/runs/{run_id}/attempts", headers=headers)
    assert attempts.json()["attempts"]
    artifacts = client.get(f"/api/v2/analysis/runs/{run_id}/artifacts", headers=headers)
    assert artifacts.status_code == 200
    assert artifacts.json()
    assert "content_json" not in artifacts.json()[0]
    claims = client.get(f"/api/v2/analysis/runs/{run_id}/claims", headers=headers)
    assert {item["claim_id"] for item in claims.json()} >= {"INV-1", "RISK-1"}
    timeline = client.get(f"/api/v2/analysis/runs/{run_id}/timeline", headers=headers)
    assert timeline.json()["events"]
    with SessionLocal() as db:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).one()
        assert run.status == RunStatus.COMPLETED
        assert run.structured_result_json
        assert db.query(AnalysisStage).filter_by(analysis_run_id=run_id).count() >= 8
        assert db.query(AnalysisNode).filter_by(analysis_run_id=run_id).count() >= 8
        llm_nodes = [row.node_key for row in db.query(AnalysisNode).filter_by(analysis_run_id=run_id).all()]
        assert "analyst_team_legacy" in llm_nodes
        assert db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run_id).count() >= 1
        assert db.query(AnalysisArtifact).filter_by(analysis_run_id=run_id).count() >= 1
        assert db.query(AnalysisClaim).filter_by(analysis_run_id=run_id).count() >= 1


def test_research_manager_failure_keeps_prior_audit(monkeypatch):
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)

    def fake(_profile, _system, _payload, _instruction, phase_name):
        if phase_name == "research_verdict":
            raise RuntimeError("research_manager_failed")
        return _phase_payload(phase_name)

    monkeypatch.setattr(analysis_engine, "_structured_call_json", fake)
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)
    created = client.post(
        "/api/v2/analysis/jobs",
        headers=headers,
        json={"snapshot_id": snapshot_id, "mode": "deep", "notify": False},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["id"]
    job = client.get(f"/api/v2/analysis/jobs/{job_id}", headers=headers)
    assert job.json()["status"] == "failed"
    with SessionLocal() as db:
        row = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).one()
        run = db.query(AnalysisRun).filter(AnalysisRun.job_id == job_id).one()
        assert row.status == "failed"
        assert run.status == RunStatus.FAILED
        assert db.query(AnalysisStage).filter_by(analysis_run_id=run.id, phase_key="analysts_running").one().status == "completed"
        failed = db.query(AnalysisNode).filter_by(analysis_run_id=run.id, node_key="research_manager").one()
        assert failed.status == "failed"
        attempt = db.query(AnalysisNodeAttempt).filter_by(node_id=failed.id).one()
        assert attempt.status == "failed"
        assert "research_manager_failed" in (attempt.error_message or "")
        timeline = client.get(f"/api/v2/analysis/runs/{run.id}/timeline", headers=headers)
        assert timeline.status_code == 200
        assert any(item["type"] == "run_failed" for item in timeline.json()["events"])


def test_quality_gate_blocked_run_is_persisted(monkeypatch):
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_blocked)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)
    created = client.post(
        "/api/v2/analysis/jobs",
        headers=headers,
        json={"snapshot_id": snapshot_id, "mode": "deep", "notify": False},
    )
    assert created.status_code == 202, created.text
    job = client.get(f"/api/v2/analysis/jobs/{created.json()['id']}", headers=headers)
    assert job.json()["status"] == "succeeded"
    run_id = job.json()["run_id"]
    report = client.get(f"/api/v2/analysis/runs/{run_id}", headers=headers)
    assert report.json()["final_rating"] in {"watch_only", "no_action"}
    structured = report.json()["structured_result"]["result"]
    assert structured["final_rating"] in {"watch_only", "no_action"}
    for holding in structured.get("holdings") or []:
        assert holding.get("action") in {None, "watch", "hold"}
        assert holding.get("quantity") in {None, "", 0, "0"}
    workflow = client.get(f"/api/v2/analysis/runs/{run_id}/workflow", headers=headers)
    assert workflow.json()["run"]["status"] == RunStatus.BLOCKED


def test_cancelled_job_keeps_audit_rows(monkeypatch):
    original_stage = analysis_engine._job_stage

    def cancel_on_analysts(db, job, stage, progress):
        if stage == "analysts_running":
            job.status = "cancelled"
            db.commit()
        return original_stage(db, job, stage, progress)

    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)
    monkeypatch.setattr(
        analysis_engine,
        "_structured_call_json",
        lambda _profile, _system, _payload, _instruction, phase_name: _phase_payload(phase_name),
    )
    monkeypatch.setattr(analysis_engine, "_job_stage", cancel_on_analysts)
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)
    created = client.post(
        "/api/v2/analysis/jobs",
        headers=headers,
        json={"snapshot_id": snapshot_id, "mode": "deep", "notify": False},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["id"]
    job = client.get(f"/api/v2/analysis/jobs/{job_id}", headers=headers)
    assert job.json()["status"] == "cancelled"
    with SessionLocal() as db:
        run = db.query(AnalysisRun).filter(AnalysisRun.job_id == job_id).one()
        assert run.status == RunStatus.CANCELLED
        assert db.query(AnalysisStage).filter_by(analysis_run_id=run.id).count() >= 1


def test_user_cannot_read_another_users_workflow(monkeypatch):
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)
    monkeypatch.setattr(
        analysis_engine,
        "_structured_call_json",
        lambda _profile, _system, _payload, _instruction, phase_name: _phase_payload(phase_name),
    )
    client = TestClient(app)
    headers_a, snapshot_id = _register_and_seed(client)
    created = client.post(
        "/api/v2/analysis/jobs",
        headers=headers_a,
        json={"snapshot_id": snapshot_id, "mode": "fast", "notify": False},
    )
    assert created.status_code == 202, created.text
    run_id = client.get(f"/api/v2/analysis/jobs/{created.json()['id']}", headers=headers_a).json()["run_id"]
    suffix = uuid.uuid4().hex
    email = f"other-{suffix}@example.com"
    assert client.post("/api/v2/auth/register", json={"email": email, "password": "password123"}).status_code == 201
    login = client.post("/api/v2/auth/login", json={"email": email, "password": "password123"})
    headers_b = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for path in (
        f"/api/v2/analysis/runs/{run_id}/workflow",
        f"/api/v2/analysis/runs/{run_id}/stages",
        f"/api/v2/analysis/runs/{run_id}/nodes",
        f"/api/v2/analysis/runs/{run_id}/attempts",
        f"/api/v2/analysis/runs/{run_id}/artifacts",
        f"/api/v2/analysis/runs/{run_id}/claims",
        f"/api/v2/analysis/runs/{run_id}/timeline",
    ):
        response = client.get(path, headers=headers_b)
        assert response.status_code == 404, path


def test_job_retry_resumes_same_run_without_replaying_completed_nodes(monkeypatch):
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)
    calls: list[str] = []

    def fake(_profile, _system, _payload, _instruction, phase_name):
        calls.append(phase_name)
        if phase_name == "research_verdict" and calls.count("research_verdict") == 1:
            raise RuntimeError("research_manager_failed")
        return _phase_payload(phase_name)

    monkeypatch.setattr(analysis_engine, "_structured_call_json", fake)
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)
    created = client.post(
        "/api/v2/analysis/jobs",
        headers=headers,
        json={"snapshot_id": snapshot_id, "mode": "deep", "notify": False},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["id"]
    failed = client.get(f"/api/v2/analysis/jobs/{job_id}", headers=headers)
    assert failed.json()["status"] == "failed"
    with SessionLocal() as db:
        run = db.query(AnalysisRun).filter(AnalysisRun.job_id == job_id).one()
        run_id = run.id
        first_attempts = db.query(AnalysisNodeAttempt).filter_by(analysis_run_id=run_id, node_id=db.query(AnalysisNode).filter_by(analysis_run_id=run_id, node_key="research_manager").one().id).count()
        analyst_attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=db.query(AnalysisNode).filter_by(analysis_run_id=run_id, node_key="analyst_team_legacy").one().id).count()
    retried = client.post(f"/api/v2/analysis/jobs/{job_id}/retry", headers=headers)
    assert retried.status_code == 202, retried.text
    job = client.get(f"/api/v2/analysis/jobs/{job_id}", headers=headers)
    assert job.json()["status"] == "succeeded"
    assert job.json()["run_id"] == run_id
    assert calls.count("analyst_evidence") == 1
    assert calls.count("research_verdict") == 2
    with SessionLocal() as db:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).one()
        assert run.status == RunStatus.COMPLETED
        research = db.query(AnalysisNode).filter_by(analysis_run_id=run_id, node_key="research_manager").one()
        attempts = db.query(AnalysisNodeAttempt).filter_by(node_id=research.id).order_by(AnalysisNodeAttempt.attempt_no.asc()).all()
        assert [item.attempt_no for item in attempts] == [1, 2]
        assert attempts[0].status == "failed"
        assert attempts[1].status == "completed"
        assert first_attempts == 1
        analyst = db.query(AnalysisNode).filter_by(analysis_run_id=run_id, node_key="analyst_team_legacy").one()
        assert db.query(AnalysisNodeAttempt).filter_by(node_id=analyst.id).count() == analyst_attempts
        assert db.query(AnalysisStage).filter_by(analysis_run_id=run_id, phase_key="analysts_running").count() == 1


def test_job_force_restart_replays_from_start_on_same_run(monkeypatch):
    monkeypatch.setattr(analysis_engine, "collect_market_snapshot", _market_ok)
    monkeypatch.setattr(analysis_engine, "refresh_snapshot_quotes", lambda market, codes: market)
    calls: list[str] = []

    def fake(_profile, _system, _payload, _instruction, phase_name):
        calls.append(phase_name)
        if phase_name == "research_verdict" and calls.count("research_verdict") == 1:
            raise RuntimeError("research_manager_failed")
        return _phase_payload(phase_name)

    monkeypatch.setattr(analysis_engine, "_structured_call_json", fake)
    client = TestClient(app)
    headers, snapshot_id = _register_and_seed(client)
    created = client.post(
        "/api/v2/analysis/jobs",
        headers=headers,
        json={"snapshot_id": snapshot_id, "mode": "deep", "notify": False},
    )
    job_id = created.json()["id"]
    assert client.get(f"/api/v2/analysis/jobs/{job_id}", headers=headers).json()["status"] == "failed"
    with SessionLocal() as db:
        run_id = db.query(AnalysisRun).filter(AnalysisRun.job_id == job_id).one().id
    retried = client.post(f"/api/v2/analysis/jobs/{job_id}/retry?force_restart=true", headers=headers)
    assert retried.status_code == 202, retried.text
    job = client.get(f"/api/v2/analysis/jobs/{job_id}", headers=headers)
    assert job.json()["status"] == "succeeded"
    assert job.json()["run_id"] == run_id
    assert calls.count("analyst_evidence") == 2
    assert calls.count("research_verdict") == 2
    with SessionLocal() as db:
        analyst = db.query(AnalysisNode).filter_by(analysis_run_id=run_id, node_key="analyst_team_legacy").one()
        assert analyst.attempt_count == 2
        assert db.query(AnalysisNodeAttempt).filter_by(node_id=analyst.id).count() == 2
