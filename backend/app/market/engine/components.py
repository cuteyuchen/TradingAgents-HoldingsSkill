"""Seven deterministic Market Score component calculators."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .config import (
    BREADTH_WEIGHTS,
    CROWDING_INTERACTION,
    CROWDING_WEIGHTS,
    DIFFUSION_WEIGHTS,
    LIQUIDITY_WEIGHTS,
    PROFITABILITY_WEIGHTS,
    TAIL_RISK_WEIGHTS,
    TREND_WEIGHTS,
)
from .models import ComponentScore
from .score import historical_percentile, normalize_percentile, score_subcomponents


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _ratio_score(value: Any, *, inverse: bool = False) -> float | None:
    number = _number(value)
    if number is None:
        return None
    score = _clamp(number * 100.0)
    return 100.0 - score if inverse else score


def _linear_score(value: Any, *, lower: float, upper: float, inverse: bool = False) -> float | None:
    number = _number(value)
    if number is None or upper <= lower:
        return None
    score = _clamp((number - lower) / (upper - lower) * 100.0)
    return 100.0 - score if inverse else score


def _history_score(
    metric_name: str,
    value: Any,
    history: Mapping[str, Iterable[float | int | None]] | None,
    *,
    inverse: bool = False,
    fallback: float | None = None,
) -> float | None:
    samples = list((history or {}).get(metric_name, []))
    if samples:
        percentile = historical_percentile(value, samples)
        if percentile is not None:
            return normalize_percentile(percentile, direction="inverse" if inverse else "positive")
    return fallback


def _finish(
    name: str,
    raw_metrics: Mapping[str, Any],
    normalized: Mapping[str, float | None],
    weights: Mapping[str, float],
) -> ComponentScore:
    result = score_subcomponents(normalized, weights, name=name)
    result.raw_metrics = dict(raw_metrics)
    return result


def calculate_breadth_component(
    metrics: Mapping[str, Any],
    history: Mapping[str, Iterable[float | int | None]] | None = None,
) -> ComponentScore:
    median_return = metrics.get("all_a_median_return", metrics.get("median_return"))
    median_fallback = _linear_score(median_return, lower=-0.03, upper=0.03)
    high_ratio = _number(metrics.get("new_high_60_ratio"))
    low_ratio = _number(metrics.get("new_low_60_ratio"))
    nhnl = None
    if high_ratio is not None and low_ratio is not None:
        total = high_ratio + low_ratio
        nhnl = 50.0 if total == 0 else _clamp(high_ratio / total * 100.0)
    normalized = {
        "median_return": _history_score(
            "all_a_median_return", median_return, history, fallback=median_fallback
        ),
        "advancing_ratio": _ratio_score(metrics.get("advance_ratio")),
        "above_ma20": _ratio_score(metrics.get("above_ma20_ratio")),
        "above_ma60": _ratio_score(metrics.get("above_ma60_ratio")),
        "nhnl60": nhnl,
    }
    return _finish("breadth", metrics, normalized, BREADTH_WEIGHTS)


def calculate_trend_component(
    metrics: Mapping[str, Any],
    history: Mapping[str, Iterable[float | int | None]] | None = None,
) -> ComponentScore:
    median_trend = _number(metrics.get("median_index_trend_score"))
    if median_trend is None:
        median_return_20 = metrics.get("median_index_return_20")
        median_trend = _history_score(
            "median_index_return_20",
            median_return_20,
            history,
            fallback=_linear_score(median_return_20, lower=-0.10, upper=0.10),
        )
    index_confirmation = metrics.get("index_confirmation_score")
    if _number(index_confirmation) is None:
        index_confirmation = _ratio_score(metrics.get("index_confirmation_ratio"))
    normalized = {
        "ma5_gt_ma20": _ratio_score(metrics.get("ma5_gt_ma20_ratio")),
        "ma20_gt_ma60": _ratio_score(metrics.get("ma20_gt_ma60_ratio")),
        "ma60_rising": _ratio_score(metrics.get("ma60_rising_ratio")),
        "median_index_trend": _number(median_trend),
        "index_confirmation": _number(index_confirmation),
    }
    return _finish("trend", metrics, normalized, TREND_WEIGHTS)


def calculate_liquidity_component(
    metrics: Mapping[str, Any],
    history: Mapping[str, Iterable[float | int | None]] | None = None,
) -> ComponentScore:
    same_time = metrics.get("same_time_amount_ratio", metrics.get("same_time_turnover_ratio"))
    projected = metrics.get("projected_full_day_amount_ratio")
    normalized = {
        "same_time_amount": _history_score(
            "same_time_amount_ratio", same_time, history, fallback=_linear_score(same_time, lower=0.0, upper=2.0)
        ),
        "projected_amount": _history_score(
            "projected_full_day_amount_ratio", projected, history, fallback=_linear_score(projected, lower=0.0, upper=2.0)
        ),
        "median_amount": _history_score("median_amount", metrics.get("median_amount"), history),
        "median_turnover": _history_score(
            "median_turnover_rate", metrics.get("median_turnover_rate"), history
        ),
        "active_ratio": _ratio_score(metrics.get("active_ratio")),
    }
    return _finish("liquidity", metrics, normalized, LIQUIDITY_WEIGHTS)


def calculate_profitability_component(
    metrics: Mapping[str, Any],
    history: Mapping[str, Iterable[float | int | None]] | None = None,
) -> ComponentScore:
    median_return = metrics.get("all_a_median_return", metrics.get("median_return"))
    up = _number(metrics.get("limit_up_ratio"))
    down = _number(metrics.get("limit_down_ratio"))
    limit_structure = None
    if up is not None and down is not None:
        total = up + down
        limit_structure = 50.0 if total == 0 else _clamp(up / total * 100.0)
    normalized = {
        "median_return": _history_score(
            "all_a_median_return",
            median_return,
            history,
            fallback=_linear_score(median_return, lower=-0.03, upper=0.03),
        ),
        "return_gt_3": _ratio_score(metrics.get("return_gt_3_ratio")),
        "return_lt_minus_3": _ratio_score(metrics.get("return_lt_minus3_ratio"), inverse=True),
        "limit_up_structure": limit_structure,
        "strong_continuation": _ratio_score(metrics.get("strong_continuation_ratio")),
        "large_loss_ratio": _ratio_score(
            metrics.get("large_loss_ratio", metrics.get("return_lt_minus5_ratio")), inverse=True
        ),
    }
    return _finish("profitability", metrics, normalized, PROFITABILITY_WEIGHTS)


def calculate_diffusion_component(
    metrics: Mapping[str, Any],
    history: Mapping[str, Iterable[float | int | None]] | None = None,
) -> ComponentScore:
    industry_count = _number(metrics.get("industry_count"))
    if industry_count is None or industry_count <= 0:
        return ComponentScore(
            name="diffusion",
            score=None,
            raw_metrics=dict(metrics),
            quality_status="UNAVAILABLE",
            unavailable_reason="industry_data_unavailable",
            confidence=0.0,
        )
    strong_ratio = _number(metrics.get("strong_industry_ratio"))
    if strong_ratio is None:
        strong_count = _number(metrics.get("strong_industry_count"))
        strong_ratio = strong_count / industry_count if strong_count is not None else None
    normalized = {
        "up_industry_ratio": _ratio_score(metrics.get("up_industry_ratio")),
        "median_industry_return": _history_score(
            "median_industry_return",
            metrics.get("median_industry_return"),
            history,
            fallback=_linear_score(metrics.get("median_industry_return"), lower=-0.03, upper=0.03),
        ),
        "strong_industry_count": _ratio_score(strong_ratio),
        "industry_dispersion": _history_score(
            "industry_return_dispersion",
            metrics.get("industry_return_dispersion"),
            history,
            inverse=True,
        ),
        "large_small_sync": _ratio_score(metrics.get("large_small_sync_ratio")),
    }
    return _finish("diffusion", metrics, normalized, DIFFUSION_WEIGHTS)


def calculate_crowding_component(
    metrics: Mapping[str, Any],
    history: Mapping[str, Iterable[float | int | None]] | None = None,
) -> ComponentScore:
    normalized: dict[str, float | None] = {}
    for level in (1, 3, 5, 10, 20):
        metric_name = f"top{level}_concentration"
        value = metrics.get(metric_name)
        normalized[f"top{level}"] = _history_score(
            metric_name,
            value,
            history,
            inverse=True,
            fallback=_linear_score(value, lower=0.0, upper=0.80, inverse=True),
        )

    interaction_score = 100.0
    concentration_change = _number(metrics.get("top5_concentration_change"))
    advance_change = _number(metrics.get("advance_ratio_change"))
    median_change = _number(metrics.get("median_return_change"))
    if (
        concentration_change is not None
        and advance_change is not None
        and median_change is not None
        and concentration_change >= CROWDING_INTERACTION["concentration_rise_threshold"]
        and advance_change <= -CROWDING_INTERACTION["advance_ratio_drop_threshold"]
        and median_change <= -CROWDING_INTERACTION["median_return_drop_threshold"]
    ):
        interaction_score -= CROWDING_INTERACTION["deterioration_penalty"]
    if (
        (_number(metrics.get("advance_ratio")) or 0.0) >= CROWDING_INTERACTION["healthy_breadth_ratio"]
        and (_number(metrics.get("all_a_median_return")) or 0.0) > 0
        and (_number(metrics.get("diffusion_score")) or 0.0) >= CROWDING_INTERACTION["healthy_diffusion_score"]
    ):
        interaction_score += CROWDING_INTERACTION["healthy_relief"]
    normalized["interaction"] = _clamp(interaction_score)
    return _finish("crowding", metrics, normalized, CROWDING_WEIGHTS)


def calculate_tail_risk_component(
    metrics: Mapping[str, Any],
    history: Mapping[str, Iterable[float | int | None]] | None = None,
) -> ComponentScore:
    index_return = _number(metrics.get("major_index_return"))
    index_decline = max(0.0, -index_return) if index_return is not None else None
    normalized = {
        "limit_down_ratio": _history_score(
            "limit_down_ratio", metrics.get("limit_down_ratio"), history, inverse=True,
            fallback=_ratio_score(metrics.get("limit_down_ratio"), inverse=True),
        ),
        "return_lt_minus_5": _history_score(
            "return_lt_minus5_ratio", metrics.get("return_lt_minus5_ratio"), history, inverse=True,
            fallback=_ratio_score(metrics.get("return_lt_minus5_ratio"), inverse=True),
        ),
        "dispersion": _history_score(
            "cross_section_return_iqr", metrics.get("cross_section_return_iqr"), history, inverse=True
        ),
        "volatility": _history_score(
            "market_volatility", metrics.get("market_volatility"), history, inverse=True
        ),
        "new_low_surge": _history_score(
            "new_low_60_ratio", metrics.get("new_low_60_ratio"), history, inverse=True,
            fallback=_ratio_score(metrics.get("new_low_60_ratio"), inverse=True),
        ),
        "index_decline": _history_score(
            "major_index_decline", index_decline, history, inverse=True,
            fallback=_linear_score(index_decline, lower=0.0, upper=0.05, inverse=True),
        ),
    }
    result = _finish("tail_risk", metrics, normalized, TAIL_RISK_WEIGHTS)
    result.raw_metrics["tail_risk_features_available"] = [
        name for name, value in normalized.items() if value is not None
    ]
    return result


def calculate_all_components(
    metrics: Mapping[str, Any],
    histories: Mapping[str, Mapping[str, Iterable[float | int | None]]] | None = None,
) -> dict[str, ComponentScore]:
    history = histories or {}
    breadth = calculate_breadth_component(metrics, history.get("breadth"))
    trend = calculate_trend_component(metrics, history.get("trend"))
    liquidity = calculate_liquidity_component(metrics, history.get("liquidity"))
    profitability = calculate_profitability_component(metrics, history.get("profitability"))
    diffusion = calculate_diffusion_component(metrics, history.get("diffusion"))
    metrics_with_diffusion = dict(metrics) | {"diffusion_score": diffusion.score}
    crowding = calculate_crowding_component(metrics_with_diffusion, history.get("crowding"))
    tail_risk = calculate_tail_risk_component(metrics, history.get("tail_risk"))
    return {
        "breadth": breadth,
        "trend": trend,
        "liquidity": liquidity,
        "profitability": profitability,
        "diffusion": diffusion,
        "crowding": crowding,
        "tail_risk": tail_risk,
    }
