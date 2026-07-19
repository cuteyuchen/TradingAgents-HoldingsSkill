"""Smoke tests for V2 authentication and per-user model settings."""
import os
import sys
import uuid

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(TEST_DB_DIR, f"test_shared_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")

sys.path.insert(0, BACKEND_DIR)


def test_v2_auth_and_model_settings_are_user_scoped():
    from fastapi.testclient import TestClient

    from app.database import init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    suffix = uuid.uuid4().hex

    email = f"alice-{suffix}@example.com"
    response = client.post(
        "/api/v2/auth/register",
        json={"email": email, "username": f"alice-{suffix[:12]}", "password": "password123"},
    )
    assert response.status_code == 201, response.text

    login = client.post(
        "/api/v2/auth/login",
        json={"email": email, "password": "password123", "device_info": "pytest"},
    )
    assert login.status_code == 200, login.text
    tokens = login.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    me = client.get("/api/v2/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email

    provider = client.post(
        "/api/v2/model-settings/providers",
        headers=headers,
        json={
            "provider": "openai_compatible",
            "display_name": f"Local-{suffix[:8]}",
            "base_url": "http://localhost:1234/v1",
            "api_key": "never-return-this-secret",
        },
    )
    assert provider.status_code == 201, provider.text
    provider_payload = provider.json()
    assert provider_payload["has_api_key"] is True
    assert provider_payload["api_key_masked"] == "••••••••"
    assert "never-return-this-secret" not in provider.text

    provider_id = provider_payload["id"]
    first_profile = client.post(
        "/api/v2/model-settings/profiles",
        headers=headers,
        json={
            "provider_id": provider_id,
            "purpose": "vision",
            "model_name": f"vision-a-{suffix[:8]}",
            "is_default": True,
        },
    )
    assert first_profile.status_code == 201, first_profile.text

    second_profile = client.post(
        "/api/v2/model-settings/profiles",
        headers=headers,
        json={
            "provider_id": provider_id,
            "purpose": "vision",
            "model_name": f"vision-b-{suffix[:8]}",
            "is_default": True,
        },
    )
    assert second_profile.status_code == 201, second_profile.text

    profiles = client.get("/api/v2/model-settings/profiles", headers=headers)
    assert profiles.status_code == 200, profiles.text
    defaults = [item for item in profiles.json() if item["purpose"] == "vision" and item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["model_name"] == f"vision-b-{suffix[:8]}"

    refreshed = client.post(
        "/api/v2/auth/refresh",
        json={"refresh_token": tokens["refresh_token"], "device_info": "pytest-rotated"},
    )
    assert refreshed.status_code == 200, refreshed.text
    reused = client.post(
        "/api/v2/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert reused.status_code == 401

    other_email = f"bob-{suffix}@example.com"
    assert client.post(
        "/api/v2/auth/register",
        json={"email": other_email, "password": "password123"},
    ).status_code == 201
    other_tokens = client.post(
        "/api/v2/auth/login",
        json={"email": other_email, "password": "password123"},
    ).json()
    other_headers = {"Authorization": f"Bearer {other_tokens['access_token']}"}
    assert client.get("/api/v2/model-settings/providers", headers=other_headers).json() == []
