"""Archive API tests for screenshot + holdings JSON + advice Markdown uploads."""
import json
import os
import shutil
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
TEST_ARTIFACTS_DIR = os.path.join(TEST_DB_DIR, f"test_artifacts_{os.getpid()}")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ["ADVISOR_DB_PATH"] = os.path.join(TEST_DB_DIR, f"test_archives_{os.getpid()}.db")
os.environ["ADVISOR_ARTIFACTS_DIR"] = TEST_ARTIFACTS_DIR
os.environ["ADVISOR_SQLITE_JOURNAL_MODE"] = "MEMORY"
os.environ["ADVISOR_BENCHMARK_FETCH_ON_START"] = "0"
os.environ["ADVISOR_TOKEN"] = "test_token_xxx"

sys.path.insert(0, BACKEND_DIR)


def test_archive_upload_detail_and_delete():
    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass
    shutil.rmtree(TEST_ARTIFACTS_DIR, ignore_errors=True)

    from fastapi.testclient import TestClient

    from app.database import init_db
    from app.main import app

    init_db()
    client = TestClient(app)
    headers = {"Authorization": "Bearer test_token_xxx"}

    holdings = [
        {
            "code": "600519",
            "name": "贵州茅台",
            "qty": 1000,
            "available_qty": 0,
            "cost": 1700,
            "price": 1680,
        }
    ]
    advice_md = "# 今日结论\n\n## 持仓解析\n\n- 可用数量为 0，不代表已经减仓。\n"
    meta = {"checkpoint": "10:00", "data_quality_grade": "A", "holdings_source": "screenshot"}

    response = client.post(
        "/api/v1/archives",
        headers=headers,
        data={"meta": json.dumps(meta, ensure_ascii=False)},
        files={
            "screenshot": ("holdings.png", b"\x89PNG\r\n\x1a\n", "image/png"),
            "holdings_json": (
                "holdings.json",
                json.dumps(holdings, ensure_ascii=False).encode("utf-8"),
                "application/json",
            ),
            "advice_md": ("advice.md", advice_md.encode("utf-8"), "text/markdown"),
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    archive_id = created["id"]
    archive_dir = os.path.join(TEST_ARTIFACTS_DIR, str(archive_id))
    assert os.path.isdir(archive_dir)
    assert sorted(os.listdir(archive_dir)) == ["advice.md", "holdings.json", "screenshot.png"]

    response = client.get(f"/api/v1/archives/{archive_id}", headers=headers)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["advice_md"] == advice_md
    assert detail["screenshot"]["data_url"].startswith("data:image/png;base64,")
    assert detail["holdings"][0]["qty"] == 1000
    assert detail["holdings"][0]["available_qty"] == 0
    assert detail["holdings"][0]["unavailable_qty"] == 1000
    assert "不可用" in detail["holdings"][0]["availability_note"]
    assert "减仓" not in detail["holdings"][0]["availability_note"]

    response = client.get("/api/v1/archives", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()[0]["id"] == archive_id
    assert response.json()[0]["has_screenshot"] is True
    assert response.json()[0]["holdings_count"] == 1

    response = client.delete(f"/api/v1/archives/{archive_id}", headers=headers)
    assert response.status_code == 204, response.text
    assert not os.path.exists(archive_dir)
    assert client.get(f"/api/v1/archives/{archive_id}", headers=headers).status_code == 404

    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass
    shutil.rmtree(TEST_ARTIFACTS_DIR, ignore_errors=True)
