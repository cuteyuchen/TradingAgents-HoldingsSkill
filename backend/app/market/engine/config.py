"""Single machine-readable source for Market Engine rules and weights."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

MARKET_ENGINE_VERSION = "market-engine-v1"
UNIVERSE_RULE_VERSION = "market-universe-v1"
SCORE_CONFIG_VERSION = "market-score-config-v1"

COMPONENT_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "breadth": 0.20,
        "trend": 0.20,
        "liquidity": 0.15,
        "profitability": 0.15,
        "diffusion": 0.10,
        "crowding": 0.10,
        "tail_risk": 0.10,
    }
)
BREADTH_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "median_return": 0.25,
        "advancing_ratio": 0.20,
        "above_ma20": 0.20,
        "above_ma60": 0.15,
        "nhnl60": 0.20,
    }
)
TREND_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "ma5_gt_ma20": 0.20,
        "ma20_gt_ma60": 0.25,
        "ma60_rising": 0.20,
        "median_index_trend": 0.20,
        "index_confirmation": 0.15,
    }
)
LIQUIDITY_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "same_time_amount": 0.35,
        "projected_amount": 0.25,
        "median_amount": 0.15,
        "median_turnover": 0.15,
        "active_ratio": 0.10,
    }
)
PROFITABILITY_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "median_return": 0.25,
        "return_gt_3": 0.15,
        "return_lt_minus_3": 0.15,
        "limit_up_structure": 0.15,
        "strong_continuation": 0.15,
        "large_loss_ratio": 0.15,
    }
)
DIFFUSION_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "up_industry_ratio": 0.30,
        "median_industry_return": 0.20,
        "strong_industry_count": 0.20,
        "industry_dispersion": 0.15,
        "large_small_sync": 0.15,
    }
)
CROWDING_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "top1": 0.10,
        "top3": 0.15,
        "top5": 0.30,
        "top10": 0.20,
        "top20": 0.15,
        "interaction": 0.10,
    }
)
TAIL_RISK_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "limit_down_ratio": 0.20,
        "return_lt_minus_5": 0.20,
        "dispersion": 0.15,
        "volatility": 0.15,
        "new_low_surge": 0.20,
        "index_decline": 0.10,
    }
)

MA_WINDOWS = (5, 10, 20, 60, 120, 250)
NEW_HIGH_LOW_WINDOWS = (20, 60, 120, 250)
PERCENTILE_LOOKBACK_DAYS = MappingProxyType({"1y": 250, "3y": 750, "5y": 1250})
PERCENTILE_MIN_SAMPLES = 60

# Ratios: >=98% valid, >=95% degraded, <95% frozen.
COVERAGE_THRESHOLDS: Mapping[str, float] = MappingProxyType(
    {"valid": 0.98, "degraded": 0.95}
)
MIN_COMPONENT_WEIGHT_COVERAGE = 0.80
SMOOTHING_ALPHA = 0.70

REGIME_ORDER = (
    "STRONG_RISK_OFF",
    "RISK_OFF",
    "NEUTRAL",
    "RISK_ON",
    "STRONG_RISK_ON",
)
REGIME_LOWER_BOUNDS: Mapping[str, float] = MappingProxyType(
    {
        "STRONG_RISK_OFF": 0.0,
        "RISK_OFF": 21.0,
        "NEUTRAL": 41.0,
        "RISK_ON": 61.0,
        "STRONG_RISK_ON": 81.0,
    }
)
REGIME_HYSTERESIS: Mapping[str, Mapping[str, float]] = MappingProxyType(
    {
        "STRONG_RISK_OFF": MappingProxyType({"up": 23.0}),
        "RISK_OFF": MappingProxyType({"down": 18.0, "up": 43.0}),
        "NEUTRAL": MappingProxyType({"down": 38.0, "up": 63.0}),
        "RISK_ON": MappingProxyType({"down": 58.0, "up": 83.0}),
        "STRONG_RISK_ON": MappingProxyType({"down": 78.0}),
    }
)

CONFIDENCE_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "universe_coverage": 0.30,
        "quote_freshness": 0.15,
        "historical_coverage": 0.20,
        "component_availability": 0.20,
        "provider_quality": 0.10,
        "conflict_quality": 0.05,
    }
)

CROWDING_INTERACTION = MappingProxyType(
    {
        "concentration_rise_threshold": 0.02,
        "advance_ratio_drop_threshold": 0.03,
        "median_return_drop_threshold": 0.003,
        "deterioration_penalty": 20.0,
        "healthy_breadth_ratio": 0.55,
        "healthy_diffusion_score": 60.0,
        "healthy_relief": 10.0,
    }
)


def _assert_weights(weights: Mapping[str, float], *, name: str) -> None:
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"{name} must sum to 1.0, got {total}")
    if any(value < 0 for value in weights.values()):
        raise ValueError(f"{name} cannot contain negative weights")


def validate_config() -> bool:
    """Fail fast when an edited scoring contract is internally inconsistent."""

    for name, weights in (
        ("COMPONENT_WEIGHTS", COMPONENT_WEIGHTS),
        ("BREADTH_WEIGHTS", BREADTH_WEIGHTS),
        ("TREND_WEIGHTS", TREND_WEIGHTS),
        ("LIQUIDITY_WEIGHTS", LIQUIDITY_WEIGHTS),
        ("PROFITABILITY_WEIGHTS", PROFITABILITY_WEIGHTS),
        ("DIFFUSION_WEIGHTS", DIFFUSION_WEIGHTS),
        ("CROWDING_WEIGHTS", CROWDING_WEIGHTS),
        ("TAIL_RISK_WEIGHTS", TAIL_RISK_WEIGHTS),
        ("CONFIDENCE_WEIGHTS", CONFIDENCE_WEIGHTS),
    ):
        _assert_weights(weights, name=name)
    if not 0 <= SMOOTHING_ALPHA <= 1:
        raise ValueError("SMOOTHING_ALPHA must be between 0 and 1")
    return True


validate_config()
