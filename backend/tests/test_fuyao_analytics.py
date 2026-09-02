"""Deterministic evidence analytics tests; no score mutation is allowed."""
from __future__ import annotations

from app.market.fuyao_analytics import (
    calculate_portfolio_contributions,
    fundamental_summary,
    summarize_financials,
    summarize_valuation,
)
from app.market.models import NormalizedQuote


def test_valuation_missing_is_not_called_cheap_or_expensive():
    result = summarize_valuation({"item": [{"thscode": "600519.SH", "pe_ttm": None, "pb_mrq": None}]})
    assert result["status"] == "MISSING"
    assert result["label"] == "数据不足"
    assert result["historical_position"] == "历史样本不足"
    assert result["historical_pit_status"] == "HISTORICAL_PIT_NOT_PROVEN"


def test_financial_summary_is_missing_aware_and_pit_unknown():
    result = summarize_financials(
        {
            "income": {"item": [{"period_end_ms": 2, "operating_income": 120, "net_profit": 12}, {"period_end_ms": 1, "operating_income": 100, "net_profit": 10}]},
            "balance": {"item": [{"period_end_ms": 2, "assets_total": 200, "total_debt": 50}]},
            "cash_flow": {"item": [{"period_end_ms": 2, "act_cash_flow_net": 20}]},
        }
    )
    assert result["growth"]["label"] == "改善"
    assert result["profitability"]["label"] == "改善"
    assert result["cash_flow"]["label"] == "正常"
    assert result["historical_pit_status"] == "HISTORICAL_PIT_NOT_PROVEN"
    assert result["evidence_status"] == "NON_PIT_CONTEXT"


def test_fundamental_summary_is_context_not_production_score_input():
    summary = fundamental_summary(
        {"income": {"item": [{"period_end_ms": 1, "operating_income": 100, "net_profit": 10}]}},
        {},
        {"status": "AVAILABLE", "label": "中性"},
    )
    assert summary["growth"] == "数据不足"
    assert summary["valuation"] == "中性"
    assert summary["pit_status"] == "CURRENT_ANALYSIS_ALLOWED"


def test_portfolio_contribution_is_freshness_and_missing_aware_without_fake_zero():
    holdings = [
        {"code": "600519", "name": "贵州茅台", "qty": 10, "market_value": 15000},
        {"code": "159915", "name": "创业板ETF", "qty": 100, "market_value": 250},
    ]
    quotes = {
        "600519": NormalizedQuote(code="600519", price=1600, pct_change=2.0, provider="fuyao"),
        "159915": NormalizedQuote(code="159915", price=None, pct_change=None, provider="fuyao"),
    }
    result = calculate_portfolio_contributions(holdings, quotes)
    assert result["quality_status"] == "DEGRADED"
    assert result["missing_quote_count"] == 1
    assert result["items"][0]["contribution_pct"] == 2.0
    assert result["items"][1]["contribution_pct"] is None
    assert result["items"][1]["current_price"] is None


def test_stale_conflicting_or_invalid_quotes_are_not_used_for_contribution():
    holdings = [{"code": "600519", "qty": 10, "market_value": 15000}]
    quotes = {
        "600519": NormalizedQuote(code="600519", price=1600, pct_change=2.0, provider="fuyao", quality_status="STALE"),
    }
    result = calculate_portfolio_contributions(holdings, quotes)
    assert result["quoted_count"] == 0
    assert result["items"][0]["current_price"] is None
    assert result["items"][0]["contribution_pct"] is None


def test_context_does_not_change_frozen_score_value():
    frozen = {"display_score": 61.5, "components": {"breadth": 20, "trend": 10}}
    before = dict(frozen)
    _ = fundamental_summary({}, {}, summarize_valuation({}))
    assert frozen == before
