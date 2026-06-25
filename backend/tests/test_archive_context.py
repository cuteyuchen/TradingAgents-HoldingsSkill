"""Archive context tests for recent holdings and advice consistency inputs."""
import json
import os
import shutil
import sys
from datetime import UTC, datetime

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
TEST_ARTIFACTS_DIR = os.path.join(TEST_DB_DIR, f"test_shared_artifacts_{os.getpid()}")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ["ADVISOR_DB_PATH"] = os.path.join(TEST_DB_DIR, f"test_shared_{os.getpid()}.db")
os.environ["ADVISOR_ARTIFACTS_DIR"] = TEST_ARTIFACTS_DIR
os.environ["ADVISOR_SQLITE_JOURNAL_MODE"] = "MEMORY"
os.environ["ADVISOR_TOKEN"] = "test_token_xxx"

sys.path.insert(0, BACKEND_DIR)

TODAY = datetime.now(UTC).date().isoformat()


def _reset_storage() -> None:
    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass
    shutil.rmtree(TEST_ARTIFACTS_DIR, ignore_errors=True)


def _upload_archive(client, headers, idx: int, holdings) -> int:
    response = client.post(
        "/api/v1/archives",
        headers=headers,
        data={
            "meta": json.dumps(
                {
                    "timestamp": f"{TODAY}T{9 + idx:02d}:00:00+00:00",
                    "checkpoint": f"{9 + idx:02d}:00",
                    "data_quality_grade": "A" if idx % 2 else "B",
                    "holdings_source": "screenshot",
                    "title": f"Archive {idx}",
                },
                ensure_ascii=False,
            )
        },
        files={
            "screenshot": (f"holdings-{idx}.png", b"\x89PNG\r\n\x1a\n", "image/png"),
            "holdings_json": (
                "holdings.json",
                json.dumps(holdings, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
            "advice_md": (
                "advice.md",
                (
                    f"# 建议 {idx}\n\n- 贵州茅台（600519）持有，触发位 {1600 + idx}。\n"
                    f"- 候选标的观察，避免同日无依据反向建议。\n"
                ).encode("utf-8"),
                "text/markdown",
            ),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_archive_context_returns_recent_holdings_and_advice():
    _reset_storage()

    from fastapi.testclient import TestClient

    from app.database import init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    headers = {"Authorization": "Bearer test_token_xxx"}

    for idx in range(6):
        if idx == 2:
            holdings = {
                "holdings": [
                    {
                        "code": "600519.SH",
                        "name": "贵州茅台",
                        "qty": 100 + idx,
                        "available_qty": 80 + idx,
                        "cost": 1600,
                        "price": 1610 + idx,
                        "pnl": 0.01,
                    }
                ]
            }
        else:
            holdings = [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "qty": 100 + idx,
                    "available_qty": 80 + idx,
                    "cost": 1600,
                    "price": 1610 + idx,
                    "pnl": 0.01,
                },
                {
                    "code": "000858",
                    "name": "五粮液",
                    "qty": 10 + idx,
                    "available_qty": 5 + idx,
                    "cost": 150,
                    "price": 140 + idx,
                    "pnl": -0.06,
                },
            ]
        _upload_archive(client, headers, idx, holdings)

    assert client.get("/api/v1/archives/context").status_code == 401

    response = client.get("/api/v1/archives/context?limit=5", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["archives"]) == 5
    assert "advice_md" not in payload["archives"][0]
    assert len(payload["timeline_by_code"]["600519"]) == 5
    assert payload["latest_by_code"]["600519"]["qty"] == 105
    assert payload["latest_by_code"]["600519"]["archive_id"] == payload["archives"][0]["id"]
    assert payload["same_day_advice"][0]["archive_id"] == payload["archives"][0]["id"]
    assert "持有" in payload["same_day_advice"][0]["advice_excerpt"]

    response = client.get(
        "/api/v1/archives/context?codes=000858&limit=5&include_advice=true",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    filtered = response.json()
    assert len(filtered["archives"]) == 5
    assert set(filtered["timeline_by_code"]) == {"000858"}
    assert filtered["latest_by_code"]["000858"]["name"] == "五粮液"
    assert "advice_md" in filtered["archives"][0]

    response = client.get("/api/v1/archives/context?codes=600519&limit=20", headers=headers)
    assert response.status_code == 200, response.text
    filtered = response.json()
    assert len(filtered["archives"]) == 6
    assert len(filtered["timeline_by_code"]["600519"]) == 6

    _reset_storage()
