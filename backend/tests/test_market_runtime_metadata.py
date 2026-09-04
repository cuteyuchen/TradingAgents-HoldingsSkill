"""Tests for Phase B snapshot metadata and authenticated V3 endpoints."""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(TEST_DB_DIR, f"test_market_runtime_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)


def test_build_snapshot_filters_duplicate_invalid_and_partial_quotes():
    from app.services.market_snapshot_service import build_quote_snapshot

    snapshot = build_quote_snapshot(
        [
            {"code": "600519.SH", "price": 1600, "prev_close": 1590, "provider": "fake"},
            {"code": "600519", "price": 1601, "prev_close": 1590, "provider": "fake"},
            {"code": "not-a-code", "price": 3, "provider": "fake"},
            {"code": "000001", "price": None, "provider": "fake"},
        ],
        requested_codes=["600519", "000001", "000002"],
        expected_count=3,
        provider="fake",
        trade_date=date(2026, 8, 21),
    )

    assert snapshot["received_count"] == 1
    assert snapshot["coverage_ratio"] == pytest.approx(1 / 3, abs=1e-6)
    assert snapshot["quality_status"] == "DEGRADED"
    assert snapshot["metadata"]["duplicate_count"] == 1
    assert any("duplicate_quote" in str(error) for error in snapshot["errors"])
    assert any(item["quality_status"] == "INVALID" for item in snapshot["quotes"])


def test_build_snapshot_scales_linearly_for_5000_quotes():
    from app.services.market_snapshot_service import build_quote_snapshot

    quotes = [
        {"code": str(100000 + index), "price": 10.0, "prev_close": 9.9, "provider": "fake"}
        for index in range(5001)
    ]
    started = time.perf_counter()
    snapshot = build_quote_snapshot(quotes, expected_count=5001, provider="fake")
    elapsed = time.perf_counter() - started

    assert len(snapshot["quotes"]) == 5001
    assert snapshot["received_count"] == 5001
    assert snapshot["coverage_ratio"] == 1.0
    assert snapshot["quality_status"] == "VALID"
    assert elapsed < 5.0


def test_persist_snapshot_stores_metadata_and_snapshot_lineage_only():
    from app.database import Base
    from app.market_runtime_models import MarketSnapshot, ProviderHealth, SourceLineage
    from app.market_models import SecurityMaster, TradingCalendar  # noqa: F401
    from app.models import Archive, HoldingSnapshot, Run  # noqa: F401
    from app.services.market_snapshot_service import (
        build_quote_snapshot,
        persist_snapshot,
        record_provider_failure,
        record_provider_success,
    )
    from app.v2_models import (  # noqa: F401
        AnalysisJob,
        AnalysisRun,
        HoldingItem,
        HoldingUpload,
        ModelProfile,
        ModelProvider,
        NotificationChannel,
        NotificationDelivery,
        Portfolio,
        PortfolioSnapshot,
        RefreshToken,
        Schedule,
        User,
    )

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        snapshot = build_quote_snapshot(
            [{"code": "600519", "price": 1600, "prev_close": 1590, "provider": "fake"}],
            expected_count=1,
            provider="fake",
        )
        persist_snapshot(db, snapshot, endpoint="https://example.test/quotes")
        for _ in range(3):
            record_provider_failure(db, "fake", "quote", "timeout", failure_threshold=3)
        health = record_provider_success(db, "fake", "quote", latency_ms=12.5)
        db.commit()

        row = db.query(MarketSnapshot).one()
        lineage = db.query(SourceLineage).one()
        assert row.received_count == 1
        assert row.coverage_ratio == 1.0
        assert row.metadata_json["requested_count"] == 0
        assert lineage.entity_key == row.snapshot_id
        assert lineage.provider_endpoint == "https://example.test/quotes"
        assert health.status == "HEALTHY"
        assert health.consecutive_failures == 0
        assert inspect(engine).has_table("market_snapshots")
        assert not hasattr(row, "quotes")
        assert db.query(ProviderHealth).one().failure_count == 3
    finally:
        db.close()


def test_v3_snapshot_api_requires_jwt_and_persists_metadata():
    from fastapi.testclient import TestClient

    from app.database import init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    unauthorized = client.post("/api/v3/market/quotes/snapshot", json={})
    assert unauthorized.status_code == 401

    suffix = uuid.uuid4().hex
    email = f"market-{suffix}@example.com"
    register = client.post(
        "/api/v2/auth/register",
        json={"email": email, "username": f"market-{suffix[:12]}", "password": "password123"},
    )
    assert register.status_code == 201, register.text
    login = client.post("/api/v2/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post(
        "/api/v3/market/quotes/snapshot",
        headers=headers,
        json={"codes": ["600519", "000001"], "expected_count": 2, "provider": "test", "persist": True},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["expected_count"] == 2
    assert payload["received_count"] == 0
    assert payload["quality_status"] == "MISSING"
    assert payload["quotes"] == []
    assert any(error.get("code") == "provider_not_configured" for error in payload["errors"])

    listed = client.get("/api/v3/market/quotes/snapshots", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["snapshot_id"] == payload["snapshot_id"]
    assert listed.json()[0]["quotes"] == []

    health = client.get("/api/v3/market/providers/health", headers=headers)
    assert health.status_code == 200
