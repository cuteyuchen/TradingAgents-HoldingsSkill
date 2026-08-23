from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.market.engine import (
    ComponentScore,
    aggregate_component_scores,
    apply_regime_hysteresis,
    build_market_score_universe,
    calculate_cross_section_metrics,
    calculate_ma_breadth,
    calculate_median_index,
    calculate_new_high_low,
    coverage_gate,
    historical_percentile,
    is_price_limit,
    median_return,
    normalize_percentile,
    top_concentration,
    top_concentrations,
    smooth_display_score,
)


def _calendar(start: date, days: int) -> list[dict]:
    return [
        {"trade_date": start + timedelta(days=index), "is_open": True}
        for index in range(days)
    ]


def _bar(code: str, day: date, close: float, *, prev_close: float | None = None, **extra) -> dict:
    return {
        "code": code,
        "trade_date": day,
        "close": close,
        "prev_close": prev_close,
        "adjustment": "QFQ",
        "quality_status": "VALID",
        **extra,
    }


def test_market_score_universe_exclusions_are_auditable() -> None:
    as_of = date(2026, 8, 21)
    rows = [
        {"code": "600519", "exchange": "SSE", "security_type": "STOCK", "listing_date": date(2026, 1, 1)},
        {"code": "000001", "exchange": "SZSE", "security_type": "STOCK", "listing_date": date(2020, 1, 1)},
        {"code": "510300", "exchange": "SSE", "security_type": "ETF", "listing_date": date(2020, 1, 1)},
        {"code": "920001", "exchange": "BSE", "security_type": "STOCK", "listing_date": date(2020, 1, 1)},
        {"code": "600000", "exchange": "SSE", "security_type": "STOCK", "name": "*ST测试", "listing_date": date(2020, 1, 1)},
        {"code": "300750", "exchange": "SZSE", "security_type": "STOCK", "is_suspended": True, "listing_date": date(2020, 1, 1)},
        {"code": "600001", "exchange": "SSE", "security_type": "STOCK", "status": "DELISTED", "listing_date": date(2020, 1, 1)},
        {"code": "600002", "exchange": "SSE", "security_type": "STOCK", "listing_date": date(2026, 8, 10)},
    ]
    result = build_market_score_universe(rows, trade_date=as_of, trading_calendar=_calendar(date(2026, 1, 1), 240))
    assert result.included_codes == ["000001", "600519"]
    assert result.exclusion_counts["excluded_etf"] == 1
    assert result.exclusion_counts["excluded_bse"] == 1
    assert result.exclusion_counts["excluded_st"] == 1
    assert result.exclusion_counts["excluded_suspended"] == 1
    assert result.exclusion_counts["excluded_delisting"] == 1
    assert result.exclusion_counts["excluded_new_listing"] == 1


def test_median_is_not_average() -> None:
    assert median_return([10, 2, 1, -1, -8]) == 1.0


def test_median_index_compounds() -> None:
    assert calculate_median_index([0.01, -0.02]) == [1010.0, 989.8]


def test_top5_uses_ceil_and_same_coherent_snapshot() -> None:
    amounts = [100, 90, 80, 70, 60] + [600 / 95] * 95
    assert top_concentration(amounts, fraction=0.05, universe_size=100)["ratio"] == pytest.approx(0.4)
    rows = [
        {"code": f"600{index:03d}", "amount": amount, "captured_at": "2026-08-21T10:00:00+00:00", "quality_status": "VALID"}
        for index, amount in enumerate(amounts[:10])
    ]
    rows.append({"code": "000001", "amount": 999999, "captured_at": "2026-08-21T11:00:00+00:00", "quality_status": "VALID"})
    result = top_concentrations(rows, captured_at="2026-08-21T10:00:00+00:00", universe_size=10)
    assert result["coherent_count"] == 10
    # For a ten-name universe, ceil(10 * 5%) == 1.
    assert result["top5_concentration"] == pytest.approx(max(amounts[:10]) / sum(amounts[:10]))
    assert top_concentration(range(1, 102), fraction=0.05, universe_size=101)["top_count"] == 6


def test_ma_breadth_excludes_history_insufficient_from_denominator() -> None:
    day = date(2026, 8, 21)
    long_history = [_bar("600519", day - timedelta(days=offset), 100 + (60 - offset)) for offset in range(60, -1, -1)]
    short_history = [_bar("000001", day - timedelta(days=offset), 100 + offset) for offset in range(10, -1, -1)]
    metrics = calculate_ma_breadth(long_history + short_history, as_of=day, universe_codes=["600519", "000001"])
    assert metrics["ma60_eligible_count"] == 1
    assert metrics["above_ma60_count"] == 1


def test_new_high_low_uses_prior_valid_closes() -> None:
    day = date(2026, 8, 21)
    bars = [_bar("600519", day - timedelta(days=index), 100 + (21 - index)) for index in range(21, -1, -1)]
    metrics = calculate_new_high_low(bars, as_of=day, universe_codes=["600519"], windows=[20])
    assert metrics["new_high_20_count"] == 1
    assert metrics["new_low_20_count"] == 0


def test_percentile_positive_and_inverse() -> None:
    history = list(range(1, 101))
    assert historical_percentile(90, history) == pytest.approx(0.9)
    assert normalize_percentile(0.9) == pytest.approx(90)
    assert normalize_percentile(0.9, direction="inverse") == pytest.approx(10)


def test_coverage_and_frozen_contract() -> None:
    assert coverage_gate(0.99).status == "VALID"
    assert coverage_gate(0.96).status == "DEGRADED"
    decision = coverage_gate(0.94)
    assert decision.status == "FROZEN"
    assert decision.is_frozen is True


def test_smoothing_and_hysteresis() -> None:
    assert smooth_display_score(70, 50, alpha=0.7) == pytest.approx(64)
    assert apply_regime_hysteresis(60, "RISK_ON") == "RISK_ON"
    assert apply_regime_hysteresis(57, "RISK_ON") == "NEUTRAL"
    assert apply_regime_hysteresis(62, "NEUTRAL") == "NEUTRAL"
    assert apply_regime_hysteresis(63, "NEUTRAL") == "RISK_ON"


def test_missing_component_renormalises_available_weight() -> None:
    result = aggregate_component_scores(
        {
            "breadth": 80,
            "trend": 70,
            "liquidity": 60,
            "profitability": 50,
            "diffusion": ComponentScore(name="diffusion", score=None, quality_status="UNAVAILABLE"),
            "crowding": 40,
            "tail_risk": 90,
        }
    )
    assert result.score == pytest.approx((80 * .2 + 70 * .2 + 60 * .15 + 50 * .15 + 40 * .1 + 90 * .1) / .9)
    assert "diffusion" in result.missing_components


def test_board_specific_limit_rules() -> None:
    assert is_price_limit(9.95, "600519")
    assert is_price_limit(19.9, "300750")
    assert not is_price_limit(10.0, "300750")
    assert is_price_limit(-9.95, "600519", direction="down")
