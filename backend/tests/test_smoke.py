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
    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass

    from fastapi.testclient import TestClient

    from app.database import SessionLocal, init_db
    from app.main import app
    from app.models import BenchmarkPrice

    init_db()

    # Seed two benchmark closes so alpha math can run.
    db = SessionLocal()
    db.add(BenchmarkPrice(date="2026-06-16", close=3861.0, pct_change=-0.01))
    db.add(BenchmarkPrice(date="2026-06-17", close=3900.0, pct_change=0.0))
    db.add(BenchmarkPrice(date="2026-06-18", close=3978.0, pct_change=0.02))  # +2%
    db.add(BenchmarkPrice(date="2026-06-19", close=4017.78, pct_change=0.01))
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
        "checkpoint": "14:30",
        "data_quality_grade": "C",
        "transcript": "完整8段 transcript",
        "sections": {
            "evidence": "证据包",
            "quality_gate": "质量门",
            "investment_debate": "多空辩论",
            "research_verdict": "研究裁决",
            "trader_proposal": "交易方案",
            "risk_debate": "风控辩论",
            "pm_final": "组合结论",
            "candidates": "候选",
        },
        "quality_gates": [
            {"analyst": "技术分析", "hard_check": "fail", "grade": "C", "gaps": "缺少资金流"}
        ],
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

    # List + detail + new filters.
    r = client.get("/api/v1/runs?code=600519")
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get("/api/v1/runs?code=600519&from=2026-06-18&to=2026-06-18&checkpoint=14:30&grade=C")
    assert r.status_code == 200
    assert len(r.json()) == 1
    run_id = r.json()[0]["id"]

    r = client.get(f"/api/v1/runs/{run_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["transcript"] == "完整8段 transcript"
    assert detail["sections"]["risk_debate"] == "风控辩论"
    assert detail["holdings"][0]["alpha"] is not None
    assert len(detail["claims"]) == 1
    assert detail["research_verdict"]["rating"] == "Hold"

    # Holding timeline returns oldest-first with alpha.
    r = client.get("/api/v1/holdings/600519/timeline?limit=5")
    assert r.status_code == 200
    pts = r.json()["points"]
    assert len(pts) == 2
    assert pts[-1]["alpha"] is not None

    # Out-of-order backfill must not use future snapshots for alpha.
    run0 = {
        "timestamp": "2026-06-16T10:00:00",
        "checkpoint": "10:00",
        "holdings": [{"code": "600519", "name": "贵州茅台", "price": 9.0}],
    }
    r = client.post("/api/v1/runs", json=run0, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["alphas"]["600519"]["raw_return"] is None

    # Cross-ticker lesson source for memory context.
    run_cross = {
        "timestamp": "2026-06-19T10:00:00",
        "checkpoint": "10:00",
        "holdings": [{"code": "000001", "name": "平安银行", "price": 10.0}],
        "pm_final": {"rating": "Hold", "priority_notes": "跨标的经验：强势市场先减弱势仓"},
    }
    r = client.post("/api/v1/runs", json=run_cross, headers=headers)
    assert r.status_code == 201, r.text

    r = client.get("/api/v1/memory/context?code=600519&same_limit=5&cross_limit=3")
    assert r.status_code == 200
    memory = r.json()
    assert len(memory["same_ticker"]) == 3
    assert memory["cross_ticker_lessons"][0]["code"] == "000001"
    assert "跨标的经验" in memory["cross_ticker_lessons"][0]["lesson"]

    # Health failures disable the matching watchlist item; success does not re-enable it.
    r = client.post(
        "/api/v1/watchlist",
        json={"code": "600519", "name": "贵州茅台", "cadence": "10:00", "enabled": True},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    for _ in range(3):
        r = client.post(
            "/api/v1/health/outcome",
            json={"code": "600519", "checkpoint": "10:00", "success": False, "note": "东财封禁"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
    assert r.json()["degraded"] is True

    r = client.get("/api/v1/watchlist")
    assert r.status_code == 200
    assert r.json()[0]["enabled"] is False

    r = client.post(
        "/api/v1/health/outcome",
        json={"code": "600519", "checkpoint": "10:00", "success": True},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["consecutive_failures"] == 0
    r = client.get("/api/v1/watchlist")
    assert r.json()[0]["enabled"] is False

    print("SMOKE OK: upload + alpha + timeline verified")

    # Cleanup.
    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass


if __name__ == "__main__":
    test_upload_and_read()
