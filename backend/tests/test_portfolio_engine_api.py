"""Phase E API ownership and server-owned calculation input tests."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_shared_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_ARTIFACTS_DIR", os.path.join(BACKEND_DIR, "data", f"test_shared_artifacts_{os.getpid()}"))
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)


def _headers(client, email: str) -> dict[str, str]:
    password = "password123"
    assert client.post("/api/v2/auth/register", json={"email": email, "password": password}).status_code == 201
    login = client.post("/api/v2/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_portfolio_ledger_is_user_scoped_and_risk_input_is_server_owned():
    from fastapi.testclient import TestClient

    from app.database import init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    suffix = uuid.uuid4().hex
    owner = _headers(client, f"ledger-owner-{suffix}@example.com")
    other = _headers(client, f"ledger-other-{suffix}@example.com")
    portfolio = client.post("/api/v2/portfolios", headers=owner, json={"name": f"Ledger-{suffix[:8]}"})
    assert portfolio.status_code == 201
    portfolio_id = portfolio.json()["id"]
    created = client.post(
        f"/api/v3/portfolios/{portfolio_id}/ledger",
        headers=owner,
        json={
            "entry_type": "CASH_IN",
            "gross_amount": 1000,
            "executed_at": datetime.now(UTC).isoformat(),
            "idempotency_key": f"manual:{suffix}",
        },
    )
    assert created.status_code == 201, created.text
    assert client.get(f"/api/v3/portfolios/{portfolio_id}/ledger", headers=other).status_code == 404
    assert client.post(
        f"/api/v3/portfolios/{portfolio_id}/ledger/{created.json()['id']}/void",
        headers=other,
        json={"reason": "not owner"},
    ).status_code == 404
    forbidden_input = client.post(
        f"/api/v3/portfolios/{portfolio_id}/risk/calculate",
        headers=owner,
        json={"persist": False, "weights": {"600519": 1.0}, "cash": 0, "market_score": 100},
    )
    assert forbidden_input.status_code == 422
