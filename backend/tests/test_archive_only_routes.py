"""Route exposure tests for the archive-only API surface."""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
TEST_ARTIFACTS_DIR = os.path.join(TEST_DB_DIR, f"test_shared_artifacts_{os.getpid()}")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ["ADVISOR_DB_PATH"] = os.path.join(TEST_DB_DIR, f"test_shared_{os.getpid()}.db")
os.environ["ADVISOR_ARTIFACTS_DIR"] = TEST_ARTIFACTS_DIR
os.environ["ADVISOR_SQLITE_JOURNAL_MODE"] = "MEMORY"
os.environ["ADVISOR_TOKEN"] = "test_token_xxx"

sys.path.insert(0, BACKEND_DIR)


def test_only_archive_auth_and_health_routes_are_exposed():
    """Old persistence APIs should not remain registered after archive-only rebuild."""
    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass

    from fastapi.testclient import TestClient

    from app.database import init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    headers = {"Authorization": "Bearer test_token_xxx"}

    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/auth/verify", headers=headers).status_code == 200
    assert client.get("/api/v1/archives", headers=headers).status_code == 200
    assert client.get("/api/v1/archives/context", headers=headers).status_code == 200

    removed_routes = [
        "/api/v1/runs",
        "/api/v1/portfolio/current",
        "/api/v1/holdings/600519/timeline",
        "/api/v1/candidates",
        "/api/v1/benchmark/hs300",
        "/api/v1/watchlist",
        "/api/v1/health",
        "/api/v1/memory/context",
    ]
    for route in removed_routes:
        assert client.get(route, headers=headers).status_code == 404

    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass
