"""Authenticated API tests for SecurityMaster and TradingCalendar endpoints."""
from __future__ import annotations

import os
import sys
import uuid

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(TEST_DB_DIR, f"test_market_identity_api_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("SECURITY_MASTER_SYNC_ENABLED", "true")
os.environ.setdefault("CALENDAR_SYNC_ENABLED", "true")
os.environ.setdefault("MARKET_IDENTITY_SYNC_TOKEN", "test-market-identity-sync-token")
sys.path.insert(0, BACKEND_DIR)


@pytest.fixture(scope="module")
def client_and_headers():
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.database import init_db
    from app.main import app

    original = (
        settings.SECURITY_MASTER_SYNC_ENABLED,
        settings.CALENDAR_SYNC_ENABLED,
        settings.MARKET_IDENTITY_SYNC_TOKEN,
    )
    settings.SECURITY_MASTER_SYNC_ENABLED = True
    settings.CALENDAR_SYNC_ENABLED = True
    settings.MARKET_IDENTITY_SYNC_TOKEN = os.environ["MARKET_IDENTITY_SYNC_TOKEN"]
    init_db()
    client = TestClient(app)
    suffix = uuid.uuid4().hex
    email = f"identity-{suffix}@example.com"
    username = f"identity-{suffix[:12]}"
    assert client.post(
        "/api/v2/auth/register",
        json={"email": email, "username": username, "password": "password123"},
    ).status_code == 201
    login = client.post("/api/v2/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    try:
        yield client, {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Market-Identity-Sync-Token": os.environ["MARKET_IDENTITY_SYNC_TOKEN"],
        }
    finally:
        (
            settings.SECURITY_MASTER_SYNC_ENABLED,
            settings.CALENDAR_SYNC_ENABLED,
            settings.MARKET_IDENTITY_SYNC_TOKEN,
        ) = original


def test_security_sync_list_filters_and_paginates(client_and_headers):
    client, headers = client_and_headers
    response = client.post(
        "/api/v3/market/securities/sync",
        headers=headers,
        json={
            "source": "fixture",
            "rows": [
                {"code": "600519.SH", "name": "贵州茅台", "security_type": "STOCK"},
                {"code": "159915.SZ", "name": "创业板ETF", "security_type": "ETF"},
                {"code": "000001.SZ", "name": "平安银行", "security_type": "STOCK", "status": "SUSPENDED"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["synced_count"] == 3

    page = client.get(
        "/api/v3/market/securities",
        headers=headers,
        params={"security_type": "STOCK", "status": "ACTIVE", "page": 1, "page_size": 1},
    )
    assert page.status_code == 200, page.text
    payload = page.json()
    assert payload["total"] >= 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["security_type"] == "STOCK"
    assert payload["items"][0]["status"] == "ACTIVE"

    etfs = client.get(
        "/api/v3/market/securities",
        headers=headers,
        params={"type": "ETF", "exchange": "SZSE"},
    )
    assert etfs.status_code == 200, etfs.text
    assert [item["code"] for item in etfs.json()["items"]] == ["159915"]


def test_calendar_sync_and_date_range_query(client_and_headers):
    client, headers = client_and_headers
    response = client.post(
        "/api/v3/market/calendar/sync",
        headers=headers,
        json={
            "source": "fixture",
            "rows": [
                {"trade_date": "2026-08-20", "is_open": True},
                {"trade_date": "2026-08-21", "is_open": False},
                {"trade_date": "2026-08-24", "is_open": True},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["synced_count"] == 3

    listed = client.get(
        "/api/v3/market/calendar",
        headers=headers,
        params={"start_date": "2026-08-20", "end_date": "2026-08-21"},
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [row["trade_date"] for row in rows] == ["2026-08-20", "2026-08-21"]
    assert rows[0]["is_open"] is True
    assert rows[1]["is_open"] is False


def test_identity_sync_requires_authentication(client_and_headers):
    client, _headers = client_and_headers
    assert client.get("/api/v3/market/securities").status_code == 401
    assert client.post("/api/v3/market/calendar/sync", json={"rows": []}).status_code == 401


def test_identity_sync_requires_internal_operator_token(client_and_headers):
    client, headers = client_and_headers
    user_headers = {"Authorization": headers["Authorization"]}
    assert client.post(
        "/api/v3/market/securities/sync",
        headers=user_headers,
        json={"rows": []},
    ).status_code == 403
    assert client.post(
        "/api/v3/market/calendar/sync",
        headers=user_headers,
        json={"rows": []},
    ).status_code == 403


def test_calendar_status_exposes_initialized_state(client_and_headers):
    client, headers = client_and_headers
    response = client.get("/api/v3/market/calendar/status", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in {"calendar_not_initialized", "calendar_out_of_range", "ready"}
    assert payload["market"] == "CN"
