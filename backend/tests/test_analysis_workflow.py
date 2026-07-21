"""Focused tests for the V2 Skill workflow transcript and safety normalisers."""
import os
import sys
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ADVISOR_DB_PATH", os.path.join(BACKEND_DIR, "data", f"test_workflow_{os.getpid()}.db"))
os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
sys.path.insert(0, BACKEND_DIR)


def test_required_skill_phase_rejects_provider_failures(monkeypatch):
    from app.services import analysis_engine

    def fail(*_args, **_kwargs):
        raise ValueError("provider unavailable")

    monkeypatch.setattr(analysis_engine, "_call_json", fail)
    with pytest.raises(ValueError, match="provider unavailable"):
        analysis_engine._required_call_json(object(), "system", {}, "instruction", "investment_debate")

    monkeypatch.setattr(analysis_engine, "_call_json", lambda *_args, **_kwargs: {})
    with pytest.raises(RuntimeError, match="investment_debate_empty_result"):
        analysis_engine._required_call_json(object(), "system", {}, "instruction", "investment_debate")


def test_claim_states_are_structured_and_risk_ids_are_stable():
    from app.services.analysis_engine import _normalise_investment_debate, _normalise_risk_debate

    investment = _normalise_investment_debate(
        {"bull_case": ["资金流改善"], "bear_case": ["估值偏高"], "unresolved_claims": ["估值"]},
        {"market_read": "震荡"},
        [{"code": "600519"}],
    )
    assert investment["bull_claims"][0]["claim_id"].startswith("INV-")
    assert investment["bear_claims"][0]["claim_id"].startswith("INV-")
    assert investment["round_summaries"]

    risk = _normalise_risk_debate({}, [{"code": "600519"}], {"grade": "C"})
    claims = risk["aggressive_claims"] + risk["neutral_claims"] + risk["conservative_claims"]
    assert [(item["claim_id"], item["speaker"]) for item in claims] == [
        ("RISK-1", "aggressive"),
        ("RISK-2", "neutral"),
        ("RISK-3", "conservative"),
    ]


def test_final_normalizer_caps_sales_and_exposes_today_modules():
    from app.services.analysis_engine import _normalize_final

    holdings = [{"code": "600519", "name": "贵州茅台", "available_qty": 80}]
    workflow = {
        "quality_gate": {"grade": "A", "status": "pass"},
        "candidates": [
            {
                "code": "512480",
                "name": "半导体ETF",
                "candidate_type": "new_position",
                "reason_detail": {"catalyst": "产业政策", "capital_flow": "资金净流入", "sector_position": "日内前五"},
                "score": 8,
            }
        ],
    }
    result = _normalize_final(
        {
            "data_quality_grade": "A",
            "holdings": [{"code": "600519", "action": "sell", "quantity": "200"}],
            "risk_warnings": [],
        },
        holdings,
        "A",
        workflow,
    )

    assert result["holdings"][0]["quantity"] == "80"
    assert result["today_actions"] == result["holdings"]
    assert result["buy_candidates"] == result["candidates"]
    assert result["buy_candidates"][0]["buyable"] is True


def test_markdown_contains_complete_skill_transcript():
    from app.services.analysis_engine import render_markdown

    result = {
        "data_quality_grade": "A",
        "confidence": "medium",
        "market_read": "震荡偏强",
        "portfolio_conclusion": "持有并等待触发",
        "final_rating": "hold",
        "cash_target": "20%-30%",
        "history_consistency": "首次分析",
        "holdings": [],
        "buy_candidates": [],
        "candidate_blocked_reason": "无满足条件的候选",
        "risk_warnings": [],
        "unresolved_claims": [],
        "evidence": [],
        "quality_gate": {"grade": "A", "status": "pass", "action_bias": "normal", "mandatory_checks": {"quote_coverage": True}},
        "investment_debate_state": {"bull_claims": [], "bear_claims": [], "unresolved_claim_ids": [], "round_summaries": []},
        "research_manager_verdict": {"rating": "Hold"},
        "trader_proposal": {"orders": []},
        "risk_revision": {"decision": "pass"},
        "risk_debate_state": {"aggressive_claims": [], "neutral_claims": [], "conservative_claims": []},
        "portfolio_manager_final": {"portfolio_rating": "hold"},
    }
    markdown = render_markdown(
        result,
        {"source_chain": ["test"], "final_quote_refresh_status": "ok"},
        {"id": 1, "holdings": []},
        SimpleNamespace(id=7, checkpoint="10:00"),
    )

    for heading in (
        "## 证据包",
        "## 质量门控",
        "## 多空辩论",
        "## 研究总监裁决",
        "## 交易员方案",
        "## 风控修正循环",
        "## 三方风控辩论",
        "## 组合经理最终决策",
        "## 今日持仓操作",
        "## 今日买入/轮动候选",
    ):
        assert heading in markdown
