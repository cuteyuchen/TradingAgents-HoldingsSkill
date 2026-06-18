"""Smoke test: import, create tables, upload a run, verify alpha fields exist.

Run:  cd backend && python -m pytest tests/test_smoke.py -q
or:   cd backend && python tests/test_smoke.py
"""
import os
import sys
from datetime import datetime

# Point DB at a temp file before importing app modules.
os.environ["ADVISOR_DB_PATH"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "test_advisor.db"
)
os.environ["ADVISOR_BENCHMARK_FETCH_ON_START"] = "0"
os.environ["ADVISOR_TOKEN"] = "test_token_xxx"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_upload_and_read():
    from fastapi.testclient import TestClient

    from app.database import SessionLocal, init_db
    from app.main import app
    from app.models import BenchmarkPrice

    init_db()

    # Seed two benchmark closes so alpha math can run.
    db = SessionLocal()
    db.add(BenchmarkPrice(date="2026-06-17", close=3900.0, pct_change=0.0))
    db.add(BenchmarkPrice(date="2026-06-18", close=3978.0, pct_change=0.02))  # +2%
    db.commit()
    db.close()

    client = TestClient(app)
    headers = {"Authorization": "Bearer test_token_xxx"}

    # First run: a holding at price 10.0 (no prior → no alpha yet).
    run1 = {
        "timestamp": "2026-06-17T10:00:00",
        "checkpoint": "10:00",
        "holdings_source": "screenshot",
        "data_quality_grade": "A",
        "holdings": [{"code": "600519", "name": "贵州茅台", "price": 10.0}],
    }
    r = client.post("/api/v1/runs", json=run1, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["alphas"]["600519"]["raw_return"] is None  # no prior

    # Second run: same holding now at 11.0 → +10% raw, +2% benchmark → +8% alpha.
    run2 = {
        "timestamp": "2026-06-18T10:00:00",
        "checkpoint": "10:00",
        "holdings": [{"code": "600519", "name": "贵州茅台", "price": 11.0}],
        "claims": [
            {"claim_id": "INV-1", "speaker": "bull", "claim": "测试多头",
             "confidence": 0.8, "status": "open", "round": 1},
        ],
        "research_verdict": {"rating": "Hold", "winner": "bull", "confidence": "中"},
        "candidates": [{"code": "512480", "name": "半导体ETF", "score": 7.5, "status": "待触发"}],
    }
    r = client.post("/api/v1/runs", json=run2, headers=headers)
    assert r.status_code == 201, r.text
    alpha = r.json()["alphas"]["600519"]
    assert abs(alpha["raw_return"] - 0.10) < 1e-6
    assert abs(alpha["alpha"] - 0.08) < 1e-6, alpha

    # List + detail.
    r = client.get("/api/v1/runs?code=600519")
    assert r.status_code == 200
    assert len(r.json()) == 2

    run_id = r.json()[0]["id"]
    r = client.get(f"/api/v1/runs/{run_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["holdings"][0]["alpha"] is not None
    assert len(detail["claims"]) == 1
    assert detail["research_verdict"]["rating"] == "Hold"

    # Holding timeline returns oldest-first with alpha.
    r = client.get("/api/v1/holdings/600519/timeline?limit=5")
    assert r.status_code == 200
    pts = r.json()["points"]
    assert len(pts) == 2
    assert pts[-1]["alpha"] is not None

    print("SMOKE OK: upload + alpha + timeline verified")

    # Cleanup.
    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass


if __name__ == "__main__":
    test_upload_and_read()
