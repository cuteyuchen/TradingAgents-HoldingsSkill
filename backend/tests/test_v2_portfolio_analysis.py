"""End-to-end V2 portfolio upload, confirmation, analysis, and automation tests."""
import json
import os
import sys
import uuid

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(TEST_DB_DIR, exist_ok=True)
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(TEST_DB_DIR, f"test_shared_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_ARTIFACTS_DIR", os.path.join(TEST_DB_DIR, f"test_shared_artifacts_{os.getpid()}"))
os.environ.setdefault("ADVISOR_SQLITE_JOURNAL_MODE", "MEMORY")
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

sys.path.insert(0, BACKEND_DIR)


def test_recognized_holding_without_code_can_be_corrected_manually():
    from app.services.holdings_service import parse_payload_dict

    parsed, errors = parse_payload_dict({"holdings": [{"code": None, "name": "贵州茅台", "qty": 100}]})

    assert parsed.holdings[0].name == "贵州茅台"
    assert parsed.holdings[0].code == ""
    assert errors == []


def test_analysis_model_resolves_optional_holding_code(monkeypatch):
    from app.services import analysis_engine

    monkeypatch.setattr(
        analysis_engine,
        "_call_json",
        lambda *_args, **_kwargs: {
            "matches": [{"index": 0, "code": "600519", "confidence": "high", "reason": "名称唯一匹配"}]
        },
    )
    holdings = [{"code": "", "name": "贵州茅台", "market": None}]

    resolved = analysis_engine._resolve_missing_codes(object(), holdings)

    assert resolved[0]["code"] == "600519"
    assert resolved[0]["code_source"] == "model_match"


def test_v2_portfolio_flow(monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import init_db
    from app.main import app
    from app.services import analysis_engine

    init_db()
    client = TestClient(app)
    suffix = uuid.uuid4().hex
    email = f"portfolio-{suffix}@example.com"
    password = "password123"

    assert client.post("/api/v2/auth/register", json={"email": email, "password": password}).status_code == 201
    login = client.post("/api/v2/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    portfolio = client.post("/api/v2/portfolios", headers=headers, json={"name": f"主账户-{suffix[:8]}"})
    assert portfolio.status_code == 201, portfolio.text
    portfolio_id = portfolio.json()["id"]

    provider = client.post(
        "/api/v2/model-settings/providers",
        headers=headers,
        json={"provider": "openai_compatible", "display_name": f"test-{suffix[:8]}", "base_url": "http://model.invalid/v1"},
    )
    assert provider.status_code == 201, provider.text
    profile = client.post(
        "/api/v2/model-settings/profiles",
        headers=headers,
        json={
            "provider_id": provider.json()["id"],
            "purpose": "analysis",
            "model_name": "test-model",
            "parameters": {},
            "is_default": True,
        },
    )
    assert profile.status_code == 201, profile.text

    holdings = {
        "holdings": [
            {
                "code": "600519",
                "name": "贵州茅台",
                "qty": 100,
                "available_qty": 80,
                "cost": 1500,
                "price": 1600,
                "market_value": 160000,
                "pnl": 0.0667,
                "pnl_amount": 10000,
            },
            {"code": None, "name": "中证证券", "qty": 26000, "available_qty": 26000},
            {"code": None, "name": "通信ETF", "qty": 42000, "available_qty": 38000},
        ],
        "total_assets": 200000,
        "total_market_value": 160000,
        "broker_available_cash": 38000,
        "excluded_items": [],
        "notes": [],
    }
    upload = client.post(
        f"/api/v2/portfolios/{portfolio_id}/uploads",
        headers=headers,
        data={"holdings_json": json.dumps(holdings, ensure_ascii=False)},
        files={"screenshot": ("holdings.png", b"\x89PNG\r\n\x1a\n" + b"test-image", "image/png")},
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["parsing_status"] == "waiting_confirmation"
    assert upload.json()["parsed"]["corrected_unused_funds"] == 40000

    snapshot = client.post(f"/api/v2/uploads/{upload.json()['id']}/confirm", headers=headers)
    assert snapshot.status_code == 201, snapshot.text
    snapshot_payload = snapshot.json()
    assert snapshot_payload["holdings"][0]["available_qty"] == 80
    assert snapshot_payload["holdings"][0]["extra"]["unavailable_qty"] == 20
    assert [item["code"] for item in snapshot_payload["holdings"]] == ["600519", "", ""]

    monkeypatch.setattr(
        analysis_engine,
        "collect_market_snapshot",
        lambda codes: {
            "captured_at": "2026-07-19T10:00:00+08:00",
            "quotes": {"600519": {"code": "600519", "price": 1601, "source": "test"}},
            "technicals": {"600519": {"trend": "up", "source": "test"}},
            "indices": {},
            "quality_grade": "A",
            "errors": [],
            "source_chain": ["test-source"],
        },
    )

    def fake_call(_profile, _system, _payload, instruction):
        if "匹配六位证券代码" in instruction:
            return {
                "matches": [
                    {"index": 1, "code": "512880", "confidence": "high", "reason": "名称匹配"},
                    {"index": 2, "code": "515880", "confidence": "high", "reason": "名称匹配"},
                ]
            }
        if "多空辩论" in instruction:
            return {"bull_case": ["趋势向上"], "bear_case": ["估值较高"], "unresolved_claims": [], "manager_verdict": "谨慎持有"}
        if "证据包" in instruction:
            return {"market_read": "市场平稳", "holding_evidence": [], "portfolio_risks": [], "data_gaps": [], "quality_grade": "A"}
        return {
            "data_quality_grade": "A",
            "market_read": "市场平稳",
            "portfolio_conclusion": "继续持有，等待触发条件。",
            "final_rating": "hold",
            "cash_target": "20%-30%",
            "confidence": "medium",
            "holdings": [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "action": "hold",
                    "reason": "趋势与风险平衡",
                    "trigger": "跌破关键支撑再复核",
                    "quantity": None,
                    "stop_loss": "趋势失效",
                    "take_profit": "放量突破",
                    "risk": "估值风险",
                }
            ],
            "candidates": [],
            "history_consistency": "首次分析",
            "bull_case": ["趋势向上"],
            "bear_case": ["估值较高"],
            "unresolved_claims": [],
            "risk_warnings": ["不追高"],
            "evidence": ["test-source"],
        }

    monkeypatch.setattr(analysis_engine, "_call_json", fake_call)
    created_job = client.post(
        "/api/v2/analysis/jobs",
        headers=headers,
        json={"snapshot_id": snapshot_payload["id"], "mode": "deep", "checkpoint": "10:00", "notify": False},
    )
    assert created_job.status_code == 202, created_job.text
    job = client.get(f"/api/v2/analysis/jobs/{created_job.json()['id']}", headers=headers)
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded", job.text
    assert job.json()["run_id"] is not None

    report = client.get(f"/api/v2/analysis/runs/{job.json()['run_id']}", headers=headers)
    assert report.status_code == 200, report.text
    assert report.json()["final_rating"] == "hold"
    assert "今日持仓操作" in report.json()["markdown"]
    structured = report.json()["structured_result"]
    assert structured["result"]["holdings"][0]["max_sellable_qty"] == 80
    assert structured["workflow"]["investment_debate_state"]["bull_claims"][0]["claim_id"].startswith("INV-")
    risk_claims = sum(
        (
            structured["workflow"]["risk_debate_state"][key]
            for key in ("aggressive_claims", "neutral_claims", "conservative_claims")
        ),
        [],
    )
    assert [claim["claim_id"] for claim in risk_claims] == ["RISK-1", "RISK-2", "RISK-3"]
    assert "buy_candidate_selection" in structured["skill_execution"]["phases_completed"]
    assert "今日买入/轮动候选" in report.json()["markdown"]

    schedule = client.post(
        "/api/v2/schedules",
        headers=headers,
        json={"portfolio_id": portfolio_id, "name": f"open-{suffix[:6]}", "hour": 9, "minute": 35},
    )
    assert schedule.status_code == 201, schedule.text
    assert schedule.json()["checkpoint"] == "09:35"

    bad_webhook = client.post(
        "/api/v2/notifications",
        headers=headers,
        json={"type": "dingtalk", "name": "bad", "webhook": "https://example.com/hook"},
    )
    assert bad_webhook.status_code == 422
