from __future__ import annotations

from datetime import date, timedelta
import os
import sys
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.market.engine import (
    ComponentScore,
    aggregate_component_scores,
    apply_regime_hysteresis,
    build_market_score_universe,
    build_market_score_snapshot,
    calculate_breadth_component,
    calculate_crowding_component,
    calculate_cross_section_metrics,
    calculate_liquidity_component,
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


def test_component_percentile_requires_sixty_daily_samples() -> None:
    metrics = {
        "all_a_median_return": 0.0105,
        "advance_ratio": 0.55,
        "above_ma20_ratio": 0.55,
        "above_ma60_ratio": 0.50,
        "new_high_60_ratio": 0.04,
        "new_low_60_ratio": 0.02,
    }
    samples = [-0.03 + index * 0.001 for index in range(60)]

    insufficient = calculate_breadth_component(
        metrics,
        {"all_a_median_return": samples[:59]},
    )
    ready = calculate_breadth_component(
        metrics,
        {"all_a_median_return": samples},
    )

    insufficient_audit = insufficient.raw_metrics["historical_scoring"]["all_a_median_return"]
    ready_audit = ready.raw_metrics["historical_scoring"]["all_a_median_return"]
    assert insufficient_audit["used_historical_percentile"] is False
    assert insufficient_audit["used_fallback"] is True
    assert insufficient.confidence < ready.confidence
    assert ready_audit["used_historical_percentile"] is True
    assert ready.normalized_metrics["median_return"] == pytest.approx(41 / 60 * 100)


def test_liquidity_active_ratio_alone_is_not_a_full_component() -> None:
    component = calculate_liquidity_component({"active_ratio": 1.0})
    assert component.available is False
    assert component.score is None
    assert component.subcomponent_available_weight == pytest.approx(0.10)
    assert component.unavailable_reason == "insufficient_subcomponent_coverage"


def test_crowding_interaction_is_unavailable_without_change_metrics() -> None:
    component = calculate_crowding_component(
        {
            "top1_concentration": 0.10,
            "top3_concentration": 0.20,
            "top5_concentration": 0.30,
            "top10_concentration": 0.40,
            "top20_concentration": 0.50,
        }
    )
    assert component.normalized_metrics["interaction"] is None
    assert component.subcomponent_available_weight == pytest.approx(0.90)


def test_intraday_ma_and_new_high_low_use_live_price() -> None:
    day = date(2026, 8, 23)
    history = [_bar("600519", day - timedelta(days=offset), 100.0) for offset in range(20, 0, -1)]

    strong_ma = calculate_ma_breadth(
        history,
        as_of=day,
        universe_codes=["600519"],
        windows=[20],
        current_prices={"600519": 110.0},
    )
    weak_ma = calculate_ma_breadth(
        history,
        as_of=day,
        universe_codes=["600519"],
        windows=[20],
        current_prices={"600519": 90.0},
    )
    strong_nhnl = calculate_new_high_low(
        history,
        as_of=day,
        universe_codes=["600519"],
        windows=[20],
        current_prices={"600519": 110.0},
    )
    weak_nhnl = calculate_new_high_low(
        history,
        as_of=day,
        universe_codes=["600519"],
        windows=[20],
        current_prices={"600519": 90.0},
    )

    assert strong_ma["above_ma20_count"] == 1
    assert weak_ma["above_ma20_count"] == 0
    assert strong_nhnl["new_high_20_count"] == 1
    assert weak_nhnl["new_low_20_count"] == 1


def test_limit_counts_use_rounded_theoretical_price() -> None:
    metrics = calculate_cross_section_metrics(
        [
            {
                "code": "600519",
                "price": 1.10,
                "prev_close": 1.00,
                # A percentage-only tolerance would also classify 1.09 as a
                # limit-up, but the exchange price is exactly 1.10.
                "pct_change": 9.0,
                "quality_status": "VALID",
            },
            {
                "code": "600520",
                "price": 1.09,
                "prev_close": 1.00,
                "pct_change": 9.9,
                "quality_status": "VALID",
            },
        ],
        universe_codes=["600519", "600520"],
    )
    assert metrics["limit_up_count"] == 1


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


def test_data_quality_freeze_reason_wins_over_missing_components() -> None:
    result = build_market_score_snapshot(
        {"breadth": ComponentScore(name="breadth", score=None, quality_status="UNAVAILABLE")},
        coverage=0.0,
        last_reliable_score=68,
    )
    assert result.is_frozen is True
    assert result.quality_status == "FROZEN"
    assert result.freeze_reason == "data_quality"
    assert result.display_score == pytest.approx(68)


@pytest.mark.parametrize("quality_status", ["STALE", "CONFLICT"])
def test_non_consumable_quote_quality_freezes_score(quality_status: str) -> None:
    components = {
        "breadth": 60,
        "trend": 60,
        "liquidity": 60,
        "profitability": 60,
        "diffusion": 60,
        "crowding": 60,
        "tail_risk": 60,
    }
    result = build_market_score_snapshot(
        components,
        coverage=1.0,
        quality_status=quality_status,
        last_reliable_score=68,
    )
    assert result.raw_score is None
    assert result.display_score == pytest.approx(68)
    assert result.quality_status == "FROZEN"
    assert result.is_frozen is True
    assert result.freeze_reason == "data_quality"


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


def test_component_weights_sum_to_one() -> None:
    from app.market.engine.config import validate_config

    assert validate_config() is True


@pytest.mark.parametrize("quality_status", ["INVALID", "MISSING"])
def test_invalid_or_missing_quote_quality_freezes_score(quality_status: str) -> None:
    result = build_market_score_snapshot(
        {
            "breadth": 60,
            "trend": 60,
            "liquidity": 60,
            "profitability": 60,
            "diffusion": 60,
            "crowding": 60,
            "tail_risk": 60,
        },
        coverage=1.0,
        quality_status=quality_status,
        last_reliable_score=68,
    )
    assert result.raw_score is None
    assert result.display_score == pytest.approx(68)
    assert result.quality_status == "FROZEN"
    assert result.is_frozen is True
    assert result.freeze_reason == "data_quality"


def test_component_failure_does_not_crash_aggregation(monkeypatch) -> None:
    from app.market.engine import components as component_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("component exploded")

    monkeypatch.setattr(component_mod, "calculate_diffusion_component", boom)
    result = component_mod.calculate_all_components(
        {
            "all_a_median_return": 0.01,
            "advance_ratio": 0.6,
            "above_ma20_ratio": 0.55,
            "above_ma60_ratio": 0.5,
            "new_high_60_ratio": 0.04,
            "new_low_60_ratio": 0.01,
        }
    )
    assert result["diffusion"].available is False
    assert result["diffusion"].unavailable_reason == "component_failure"
    assert result["breadth"].available is True


def test_5000_name_cross_section_completes_quickly() -> None:
    import time

    captured_at = "2026-08-21T01:35:00+00:00"
    codes = [f"{600000 + index:06d}" for index in range(5000)]
    rows = [
        {
            "code": code,
            "price": 10 + (index % 50) * 0.01,
            "prev_close": 10,
            "amount": 1_000 + index,
            "captured_at": captured_at,
            "quality_status": "VALID",
        }
        for index, code in enumerate(codes)
    ]
    as_of = date(2026, 8, 21)
    history = []
    for code in codes[:5000]:
        # 60 closes is enough for MA60 breadth without making fixture setup dominate the test.
        history.extend(
            _bar(code, as_of - timedelta(days=offset), 100 + (60 - offset) * 0.01)
            for offset in range(60, -1, -1)
        )
    started = time.perf_counter()
    metrics = calculate_cross_section_metrics(rows, universe_codes=codes, captured_at=captured_at)
    ma = calculate_ma_breadth(history, as_of=as_of, universe_codes=codes)
    nhnl = calculate_new_high_low(history, as_of=as_of, universe_codes=codes, windows=[20])
    from app.market.engine import calculate_all_components

    components = calculate_all_components(dict(metrics) | ma | nhnl)
    elapsed = time.perf_counter() - started
    assert metrics["coherent_count"] == 5000
    assert metrics["top5_count"] == 250
    assert ma["ma60_eligible_count"] == 5000
    assert components["breadth"].available is True
    assert elapsed < 20
