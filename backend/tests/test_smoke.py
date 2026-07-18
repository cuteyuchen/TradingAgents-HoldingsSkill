"""Smoke test for the V1 archive compatibility API."""
import json
import os
import sys
import uuid

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
TEST_ARTIFACTS_DIR = os.path.join(TEST_DB_DIR, f"test_shared_artifacts_{os.getpid()}")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(TEST_DB_DIR, f"test_shared_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_ARTIFACTS_DIR", TEST_ARTIFACTS_DIR)
os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

sys.path.insert(0, BACKEND_DIR)


def test_archive_api_smoke():
    """Verify auth, archive upload, listing, detail and cleanup."""
    from fastapi.testclient import TestClient

    from app.database import init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    headers = {"Authorization": "Bearer test_token_xxx"}
    suffix = uuid.uuid4().hex[:8]

    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/auth/verify").status_code == 401
    assert client.get("/api/v1/auth/verify", headers=headers).status_code == 200
    assert client.get("/api/v1/archives").status_code == 401

    holdings = [
        {"code": "600519", "name": "贵州茅台", "qty": 1000, "available_qty": 0, "price": 1680}
    ]
    response = client.post(
        "/api/v1/archives",
        headers=headers,
        data={
            "meta": json.dumps(
                {"checkpoint": "10:00", "data_quality_grade": "A", "title": f"Smoke-{suffix}"},
                ensure_ascii=False,
            )
        },
        files={
            "screenshot": ("holdings.png", b"\x89PNG\r\n\x1a\n", "image/png"),
            "holdings_json": (
                "holdings.json",
                json.dumps(holdings, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
            "advice_md": ("advice.md", "# Smoke\n\n## 建议\n\n保持质量门控。".encode("utf-8"), "text/markdown"),
        },
    )
    assert response.status_code == 201, response.text
    archive_id = response.json()["id"]

    listing = client.get("/api/v1/archives", headers=headers)
    assert listing.status_code == 200, listing.text
    assert any(item["id"] == archive_id for item in listing.json())

    detail = client.get(f"/api/v1/archives/{archive_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["title"] == f"Smoke-{suffix}"
    assert payload["holdings"][0]["unavailable_qty"] == 1000
    assert payload["screenshot"]["data_url"].startswith("data:image/png;base64,")
    assert "保持质量门控" in payload["advice_md"]

    assert client.delete(f"/api/v1/archives/{archive_id}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/archives/{archive_id}", headers=headers).status_code == 404


if __name__ == "__main__":
    test_archive_api_smoke()
