"""Smoke test: import, create tables, upload a run, verify alpha fields exist.

Run:  cd backend && python -m pytest tests/test_smoke.py -q
or:   cd backend && python tests/test_smoke.py
"""
import os
import sys
from datetime import datetime

# Point DB at a per-process backend/data file before importing app modules.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ["ADVISOR_DB_PATH"] = os.path.join(TEST_DB_DIR, f"test_advisor_{os.getpid()}.db")
os.environ["ADVISOR_SQLITE_JOURNAL_MODE"] = "MEMORY"
os.environ["ADVISOR_BENCHMARK_FETCH_ON_START"] = "0"
os.environ["ADVISOR_TOKEN"] = "test_token_xxx"

sys.path.insert(0, BACKEND_DIR)


def test_upload_and_read():
    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass

    from fastapi.testclient import TestClient

    from app.database import SessionLocal, init_db
    from app.main import app
    from app.models import BenchmarkPrice, HoldingSnapshot, Run

    init_db()

    # Seed two benchmark closes so alpha math can run.
    db = SessionLocal()
    db.add(BenchmarkPrice(date="2026-06-16", close=3861.0, pct_change=-0.01))
    db.add(BenchmarkPrice(date="2026-06-17", close=3900.0, pct_change=0.0))
    db.add(BenchmarkPrice(date="2026-06-18", close=3978.0, pct_change=0.02))  # +2%
    db.add(BenchmarkPrice(date="2026-06-19", close=4017.78, pct_change=0.01))
    db.commit()
    db.close()

    # Legacy broker/OCR records may have amount-like values in pnl. Startup repair fixes them once.
    legacy_cases = [
        ("000158", "常山北明", 14.52, 20.092, -19490.87, -0.2773243081823611),
        ("600693", "东百集团", 8.38, 16.304, -43555.62, -0.48601570166830216),
        ("159915", "创业板ETF", 4.269, 3.676, 5336.64, 0.16131664853101196),
        ("588080", "科创50ETF易方达", 1.953, 1.952, 4.57, 0.0005122950819672705),
        ("515980", "人工智能ETF华富", 1.23, 1.23, 2.94, 0.0),
        ("000858", "五粮液", 75.85, 155.565, -7966.83, -0.5124224600649246),
    ]
    db = SessionLocal()
    legacy_run = Run(
        timestamp=datetime.fromisoformat("2026-06-15T10:00:00"),
        checkpoint="10:00",
        holdings_source="screenshot",
        data_quality_grade="B",
    )
    db.add(legacy_run)
    db.flush()
    for code, name, price, cost, bad_pnl, _expected in legacy_cases:
        db.add(HoldingSnapshot(
            run_id=legacy_run.id, code=code, name=name,
            price=price, cost=cost, pnl=bad_pnl,
        ))
    db.commit()
    legacy_run_id = legacy_run.id
    db.close()

    init_db()
    db = SessionLocal()
    repaired = (
        db.query(HoldingSnapshot)
        .filter(HoldingSnapshot.run_id == legacy_run_id)
        .order_by(HoldingSnapshot.id)
        .all()
    )
    for snap, (_code, _name, _price, _cost, bad_pnl, expected_pnl) in zip(repaired, legacy_cases):
        assert abs(snap.pnl - expected_pnl) < 1e-9
        assert snap.pnl_amount == bad_pnl
    correction_count = len(db.get(Run, legacy_run_id).evidence_pack["pnl_corrections"])
    db.close()

    init_db()
    db = SessionLocal()
    assert len(db.get(Run, legacy_run_id).evidence_pack["pnl_corrections"]) == correction_count
    db.close()

    client = TestClient(app)
    headers = {"Authorization": "Bearer test_token_xxx"}

    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/auth/verify").status_code == 401
    assert client.get("/api/v1/auth/verify", headers=headers).status_code == 200
    assert client.get("/api/v1/runs").status_code == 401

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
        "screenshot": {
            "filename": "holdings.png",
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,AAAA",
            "captured_at": "2026-06-18T10:00:00+08:00",
            "source": "user_upload",
        },
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
        "holdings": [{
            "code": "600519",
            "name": "贵州茅台",
            "cost": 10.0,
            "price": 11.0,
            "pnl": 5048.62,
            "indicators": {
                "quote": {
                    "price": 11.0,
                    "source": "Tencent qt.gtimg.cn",
                    "quote_time": "2026-06-18 10:00:03",
                    "market_session": "trading",
                }
            },
        }],
        "claims": [
            {"claim_id": "INV-1", "speaker": "bull", "claim": "测试多头",
             "confidence": 0.8, "status": "open", "round": 1},
            {"claim_id": "RISK-1", "speaker": "aggressive", "stance": "risk_accept",
             "claim": "测试激进风控", "confidence": 0.6, "status": "addressed", "round": 1},
            {"claim_id": "RISK-2", "speaker": "neutral", "stance": "risk_balance",
             "claim": "测试中性风控", "confidence": 0.7, "status": "addressed", "round": 1},
            {"claim_id": "RISK-3", "speaker": "conservative", "stance": "risk_avoid",
             "claim": "测试保守风控", "confidence": 0.8, "status": "resolved", "round": 1},
        ],
        "research_verdict": {"rating": "Hold", "winner": "bull", "confidence": "中"},
        "candidates": [
            {"code": "512480", "name": "半导体ETF", "score": 7.5, "status": "待触发"},
            {"code": "600519", "name": "贵州茅台", "score": 8.0, "status": "待触发"},
        ],
    }
    r = client.post("/api/v1/runs", json=run2, headers=headers)
    assert r.status_code == 201, r.text
    alpha = r.json()["alphas"]["600519"]
    assert abs(alpha["raw_return"] - 0.10) < 1e-6
    assert abs(alpha["alpha"] - 0.08) < 1e-6, alpha

    # List + detail + new filters.
    r = client.get("/api/v1/runs?code=600519", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get(
        "/api/v1/runs?code=600519&from=2026-06-18&to=2026-06-18&checkpoint=14:30&grade=C",
        headers=headers,
    )
    assert r.status_code == 200
    assert len(r.json()) == 1
    run_id = r.json()[0]["id"]

    r = client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["transcript"] == "完整8段 transcript"
    assert detail["sections"]["risk_debate"] == "风控辩论"
    assert detail["screenshot"]["filename"] == "holdings.png"
    assert detail["holdings"][0]["alpha"] is not None
    assert abs(detail["holdings"][0]["pnl"] - 0.1) < 1e-6
    assert detail["holdings"][0]["pnl_amount"] == 5048.62
    assert detail["evidence_pack"]["pnl_corrections"][0]["code"] == "600519"
    assert detail["evidence_pack"]["pnl_corrections"][0]["reason"] == "extreme_pnl_recomputed_from_price_cost"
    assert detail["holdings"][0]["indicators"]["quote"]["source"] == "Tencent qt.gtimg.cn"
    assert len(detail["claims"]) == 4
    assert len([c for c in detail["claims"] if c["claim_id"].startswith("RISK-")]) == 3
    assert detail["research_verdict"]["rating"] == "Hold"
    assert [c["code"] for c in detail["candidates"]] == ["512480"]
    assert detail["evidence_pack"]["candidate_conflicts_removed"][0]["code"] == "600519"

    # Holding timeline returns oldest-first with alpha.
    assert client.get("/api/v1/holdings/600519/timeline?limit=5").status_code == 401
    r = client.get("/api/v1/holdings/600519/timeline?limit=5", headers=headers)
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
    cross_run_id = r.json()["run_id"]

    assert client.get("/api/v1/memory/context?code=600519&same_limit=5&cross_limit=3").status_code == 401
    r = client.get("/api/v1/memory/context?code=600519&same_limit=5&cross_limit=3", headers=headers)
    assert r.status_code == 200
    memory = r.json()
    assert len(memory["same_ticker"]) == 3
    assert memory["cross_ticker_lessons"][0]["code"] == "000001"
    assert "跨标的经验" in memory["cross_ticker_lessons"][0]["lesson"]

    # Runs can be deleted from the dashboard, with auth and cascading children.
    assert client.delete(f"/api/v1/runs/{cross_run_id}").status_code == 401
    r = client.delete(f"/api/v1/runs/{cross_run_id}", headers=headers)
    assert r.status_code == 204, r.text
    assert client.get(f"/api/v1/runs/{cross_run_id}", headers=headers).status_code == 404
    assert client.delete(f"/api/v1/runs/{cross_run_id}", headers=headers).status_code == 404
    r = client.get("/api/v1/runs?code=000001", headers=headers)
    assert r.status_code == 200
    assert r.json() == []

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

    assert client.get("/api/v1/watchlist").status_code == 401
    r = client.get("/api/v1/watchlist", headers=headers)
    assert r.status_code == 200
    assert r.json()[0]["enabled"] is False

    r = client.post(
        "/api/v1/health/outcome",
        json={"code": "600519", "checkpoint": "10:00", "success": True},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["consecutive_failures"] == 0
    r = client.get("/api/v1/watchlist", headers=headers)
    assert r.json()[0]["enabled"] is False

    print("SMOKE OK: upload + alpha + timeline verified")

    # Cleanup.
    try:
        os.remove(os.environ["ADVISOR_DB_PATH"])
    except OSError:
        pass


if __name__ == "__main__":
    test_upload_and_read()
