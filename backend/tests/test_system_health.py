"""Phase K release metadata, schema state, readiness, and request correlation."""

from __future__ import annotations

from types import SimpleNamespace
import io
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.governance.models import ParameterSetVersion
from app.system.health import (
    RuntimeNotReadyError,
    database_status,
    disk_status,
    readiness,
    require_runtime_ready_for_risk_work,
    run_quick_check,
    schema_status,
)
from app.system.logging import RequestIDMiddleware, configure_logging, redact_text
from app.system.release import build_release_metadata, schema_state


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


def test_release_metadata_uses_injected_git_sha(monkeypatch):
    monkeypatch.setattr(settings, "APP_GIT_SHA", "abc123")
    monkeypatch.setattr(settings, "APP_VERSION", "1.2.3")
    db = _full_db()
    try:
        metadata = build_release_metadata(db)
        assert metadata["git_sha"] == "abc123"
        assert metadata["app_version"] == "1.2.3"
        assert metadata["database_backend"] == "sqlite"
        assert metadata["schema_state"] in {"UNKNOWN", "CURRENT", "BEHIND"}
    finally:
        db.close()


def test_release_metadata_unknown_sha_does_not_crash(monkeypatch):
    monkeypatch.setattr(settings, "APP_GIT_SHA", "")
    db = _full_db()
    try:
        metadata = build_release_metadata(db)
        assert metadata["git_sha"] == "UNKNOWN"
    finally:
        db.close()


def test_schema_state_current_behind_and_ahead():
    head = "20260828_0018"
    revisions = ["20260827_0017", head]
    assert schema_state(head, head, revisions)["state"] == "CURRENT"
    assert schema_state("20260827_0017", head, revisions)["state"] == "BEHIND"
    ahead = schema_state("20260901_0019", head, revisions)
    assert ahead["state"] == "AHEAD"
    assert ahead["blocked"] is True


def test_schema_ahead_is_readiness_blocked(monkeypatch):
    db = _full_db()
    try:
        db.execute(__import__("sqlalchemy").text(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        ))
        db.execute(__import__("sqlalchemy").text(
            "DELETE FROM alembic_version"
        ))
        db.execute(__import__("sqlalchemy").text(
            "INSERT INTO alembic_version (version_num) VALUES ('20990101_9999')"
        ))
        db.commit()
        monkeypatch.setattr(settings, "DISK_BLOCKED_RATIO", 0.001)
        monkeypatch.setattr(settings, "DISK_DEGRADED_RATIO", 0.002)
        result = readiness(db, detailed=True)
        assert result["status"] == "BLOCKED"
        assert result["checks"]["schema"]["status"] == "BLOCKED"
    finally:
        db.close()


def test_schema_status_maps_ahead_to_blocked():
    db = _full_db()
    try:
        db.execute(__import__("sqlalchemy").text(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        ))
        db.execute(__import__("sqlalchemy").text("DELETE FROM alembic_version"))
        db.execute(__import__("sqlalchemy").text(
            "INSERT INTO alembic_version (version_num) VALUES ('20990101_9999')"
        ))
        db.commit()
        assert schema_status(db)["status"] == "BLOCKED"
    finally:
        db.close()


def test_governance_history_without_active_blocks_readiness(monkeypatch):
    db = _full_db()
    try:
        db.add(ParameterSetVersion(
            version=1,
            status="SUPERSEDED",
            snapshot_json={"candidate": {"min_decision_edge": 5}},
            config_hash="hash",
            runtime_contract_version="2.4.0",
            decision_contract_version="2.4.0",
        ))
        db.commit()
        monkeypatch.setattr(settings, "DISK_BLOCKED_RATIO", 0.001)
        monkeypatch.setattr(settings, "DISK_DEGRADED_RATIO", 0.002)
        result = readiness(db, detailed=True)
        assert result["status"] == "BLOCKED"
        assert result["checks"]["governance"]["status"] == "BLOCKED"
    finally:
        db.close()


def test_disk_status_uses_configured_ratios(monkeypatch):
    monkeypatch.setattr(settings, "DISK_DEGRADED_RATIO", 0.10)
    monkeypatch.setattr(settings, "DISK_BLOCKED_RATIO", 0.03)
    monkeypatch.setattr(
        "app.system.health.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100, used=91, free=9),
    )
    assert disk_status()["status"] == "DEGRADED"
    monkeypatch.setattr(
        "app.system.health.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100, used=98, free=2),
    )
    assert disk_status()["status"] == "BLOCKED"


def test_request_id_is_reused_or_generated():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/probe")
    def probe(request: Request):
        return {"request_id": request.state.request_id}

    with TestClient(app) as client:
        reused = client.get("/probe", headers={"X-Request-ID": "client-abc"})
        assert reused.headers["X-Request-ID"] == "client-abc"
        assert reused.json()["request_id"] == "client-abc"
        generated = client.get("/probe")
        assert generated.headers["X-Request-ID"].startswith("req_")
        assert generated.json()["request_id"] == generated.headers["X-Request-ID"]


def test_secret_redaction(monkeypatch):
    monkeypatch.setattr(settings, "ADVISOR_TOKEN", "supersecret-token-value")
    assert "supersecret-token-value" not in redact_text("Authorization: Bearer supersecret-token-value")
    assert "[REDACTED]" in redact_text('{"api_key": "supersecret-token-value"}')
    assert redact_text("api_key=abc123456") == "api_key=[REDACTED]"


def test_require_runtime_ready_blocks_behind_schema_and_governance(monkeypatch):
    db = _full_db()
    try:
        db.execute(__import__("sqlalchemy").text(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        ))
        db.execute(__import__("sqlalchemy").text("DELETE FROM alembic_version"))
        db.execute(__import__("sqlalchemy").text(
            "INSERT INTO alembic_version (version_num) VALUES ('20260827_0017')"
        ))
        db.commit()
        with pytest.raises(RuntimeNotReadyError, match="RUNTIME_NOT_READY.*schema"):
            require_runtime_ready_for_risk_work(db)
    finally:
        db.close()


def test_database_status_defers_heavy_quick_check(monkeypatch):
    from app.system import health as health_module

    db = _full_db()
    try:
        called = {"count": 0}
        health_module._QUICK_CHECK_CACHE.clear()

        def counting(db_session):
            called["count"] += 1
            return run_quick_check(db_session)

        monkeypatch.setattr("app.system.health.run_quick_check", counting)
        result = readiness(db, detailed=True)
        assert called["count"] == 0
        assert result["checks"]["database"]["quick_check_source"] == "deferred"
        assert result["checks"]["database"]["status"] == "OK"
    finally:
        db.close()


def test_analysis_and_candidate_guards_fail_closed(monkeypatch):
    from fastapi import HTTPException

    db = _full_db()
    try:
        db.add(ParameterSetVersion(
            version=1,
            status="SUPERSEDED",
            snapshot_json={"candidate": {"min_decision_edge": 5}},
            config_hash="hash",
            runtime_contract_version="2.4.0",
            decision_contract_version="2.4.0",
        ))
        db.commit()
        from app.routers.analysis_v2 import _require_ready as analysis_guard
        from app.routers.candidates_v3 import _require_ready as candidate_guard

        for guard in (analysis_guard, candidate_guard):
            with pytest.raises(HTTPException) as exc_info:
                guard(db)
            assert exc_info.value.status_code == 503
            assert "RUNTIME_NOT_READY" in str(exc_info.value.detail)
    finally:
        db.close()


def test_console_logging_redacts_and_correlates(monkeypatch):
    from app.system.logging import bind_worker_context

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        configure_logging()
        monkeypatch.setattr(settings, "ADVISOR_TOKEN", "console-supersecret")
        bind_worker_context(analysis_job_id=321, parameter_set_version="v1")
        logging.getLogger("k1_console").error("payload api_key=baresecret123 token=%s", "tokvalue")
        rendered = stream.getvalue()
        assert "baresecret123" not in rendered
        assert "console-supersecret" not in rendered
        assert "tokvalue" not in rendered
        assert "[REDACTED]" in rendered
        assert "analysis_job_id=321" in rendered
        assert "parameter_set_version=v1" in rendered
        assert "request_id=" in rendered
    finally:
        root.removeHandler(handler)
