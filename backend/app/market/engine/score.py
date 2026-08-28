"""Deterministic percentile, component aggregation, regime and quality gates."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .config import (
    COMPONENT_WEIGHTS,
    CONFIDENCE_WEIGHTS,
    COVERAGE_THRESHOLDS,
    MIN_COMPONENT_WEIGHT_COVERAGE,
    MIN_SUBCOMPONENT_WEIGHT_COVERAGE,
    REGIME_HYSTERESIS,
    REGIME_LOWER_BOUNDS,
    REGIME_ORDER,
    SMOOTHING_ALPHA,
)
from .models import ComponentScore, MarketScoreSnapshot, PercentileResult, ScoreAggregation


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, ComponentScore):
        value = value.score
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def historical_percentile(
    current_value: float | int | None,
    history: Iterable[float | int | None],
    *,
    metric_name: str = "metric",
    direction: str = "positive",
    min_samples: int = 1,
) -> float | None:
    """Return ``count(history <= current) / n`` in the closed interval [0, 1]."""

    current = _number(current_value)
    samples = sorted(value for raw in history if (value := _number(raw)) is not None)
    if current is None or len(samples) < max(1, int(min_samples)):
        return None
    return max(0.0, min(1.0, sum(value <= current for value in samples) / len(samples)))


def percentile_rank(
    current_value: float | int | None,
    history: Iterable[float | int | None],
    *,
    min_samples: int = 1,
) -> float | None:
    return historical_percentile(current_value, history, min_samples=min_samples)


def normalize_percentile(percentile: float | None, *, direction: str = "positive") -> float | None:
    """Map a percentile to 0-100; inverse metrics reward low observations."""

    if percentile is None:
        return None
    value = max(0.0, min(1.0, float(percentile)))
    if str(direction).lower() in {"inverse", "negative", "lower_is_better", "risk"}:
        value = 1.0 - value
    return round(value * 100.0, 6)


def percentile_result(
    current_value: float | int | None,
    history: Iterable[float | int | None],
    *,
    metric_name: str = "metric",
    direction: str = "positive",
    min_samples: int = 1,
) -> PercentileResult:
    samples = [value for raw in history if (value := _number(raw)) is not None]
    percentile = historical_percentile(current_value, samples, min_samples=min_samples)
    reason = None if percentile is not None else (
        "missing_value" if _number(current_value) is None else "insufficient_history"
    )
    confidence = min(100.0, len(samples) / max(min_samples, 1) * 100.0)
    return PercentileResult(
        metric_name=metric_name,
        percentile=percentile,
        normalized_score=normalize_percentile(percentile, direction=direction),
        sample_count=len(samples),
        confidence=confidence,
        direction=direction,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class CoverageDecision:
    coverage: float
    status: str
    is_frozen: bool
    reason: str | None = None

    def __str__(self) -> str:
        return self.status

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.status == other.upper()
        if isinstance(other, CoverageDecision):
            return (
                self.coverage,
                self.status,
                self.is_frozen,
                self.reason,
            ) == (
                other.coverage,
                other.status,
                other.is_frozen,
                other.reason,
            )
        return NotImplemented


def coverage_gate(coverage: float | int | None) -> CoverageDecision:
    """Classify coverage: VALID (>=98%), DEGRADED (>=95%), FROZEN otherwise."""

    value = max(0.0, min(1.0, _number(coverage) or 0.0))
    if value >= COVERAGE_THRESHOLDS["valid"]:
        return CoverageDecision(value, "VALID", False)
    if value >= COVERAGE_THRESHOLDS["degraded"]:
        return CoverageDecision(value, "DEGRADED", False, "coverage_below_normal")
    return CoverageDecision(value, "FROZEN", True, "data_quality")


def coverage_status(coverage: float | int | None) -> str:
    return coverage_gate(coverage).status


def _component_value(value: Any) -> float | None:
    if isinstance(value, ComponentScore):
        return _number(value.score) if value.available else None
    if isinstance(value, Mapping):
        status = str(value.get("quality_status", "VALID")).upper()
        if value.get("available") is False or status in {"MISSING", "INVALID", "UNAVAILABLE"}:
            return None
        return _number(value.get("score"))
    return _number(value)


def aggregate_component_scores(
    components: Mapping[str, Any],
    *,
    weights: Mapping[str, float] = COMPONENT_WEIGHTS,
    minimum_weight_coverage: float = MIN_COMPONENT_WEIGHT_COVERAGE,
) -> ScoreAggregation:
    """Weight available components only, renormalising their configured weights."""

    available: dict[str, float] = {}
    missing: list[str] = []
    for name in weights:
        value = _component_value(components.get(name))
        if value is None:
            missing.append(name)
        else:
            available[name] = max(0.0, min(100.0, value))
    available_weight = sum(float(weights[name]) for name in available)
    if not available or available_weight < minimum_weight_coverage:
        return ScoreAggregation(None, available_weight, tuple(missing), {})
    contributions = {
        name: available[name] * float(weights[name]) / available_weight
        for name in available
    }
    return ScoreAggregation(
        score=round(sum(contributions.values()), 6),
        available_weight=available_weight,
        missing_components=tuple(missing),
        contributions=contributions,
    )


def calculate_market_score(
    components: Mapping[str, Any],
    *,
    weights: Mapping[str, float] = COMPONENT_WEIGHTS,
    minimum_weight_coverage: float = MIN_COMPONENT_WEIGHT_COVERAGE,
) -> float | None:
    return aggregate_component_scores(
        components,
        weights=weights,
        minimum_weight_coverage=minimum_weight_coverage,
    ).score


def score_subcomponents(
    metrics: Mapping[str, float | int | None],
    weights: Mapping[str, float],
    *,
    name: str = "component",
    minimum_weight_coverage: float = MIN_SUBCOMPONENT_WEIGHT_COVERAGE,
) -> ComponentScore:
    """Aggregate already-normalised 0-100 subcomponent values."""

    available: dict[str, float] = {}
    for metric_name in weights:
        value = _number(metrics.get(metric_name))
        if value is not None:
            available[metric_name] = max(0.0, min(100.0, value))
    eligible_weight = sum(weights[metric_name] for metric_name in available)
    if eligible_weight < minimum_weight_coverage:
        return ComponentScore(
            name=name,
            score=None,
            raw_metrics=dict(metrics),
            normalized_metrics={metric_name: None for metric_name in weights},
            eligible_count=len(available),
            denominator=len(weights),
            quality_status="UNAVAILABLE",
            unavailable_reason=(
                "no_available_subcomponents"
                if eligible_weight <= 0
                else "insufficient_subcomponent_coverage"
            ),
            subcomponent_available_weight=round(eligible_weight, 6),
            confidence=round(eligible_weight * 100.0, 2),
        )
    score = sum(available[metric_name] * weights[metric_name] for metric_name in available) / eligible_weight
    return ComponentScore(
        name=name,
        score=round(score, 6),
        raw_metrics=dict(metrics),
        normalized_metrics={metric_name: available.get(metric_name) for metric_name in weights},
        eligible_count=len(available),
        denominator=len(weights),
        quality_status="VALID" if len(available) == len(weights) else "DEGRADED",
        unavailable_reason=None if len(available) == len(weights) else "missing_subcomponents",
        subcomponent_available_weight=round(eligible_weight, 6),
        confidence=round(eligible_weight * 100.0, 2),
    )


def classify_regime_with_bounds(
    score: float | int | None,
    *,
    lower_bounds: Mapping[str, float],
    order: Iterable[str] = REGIME_ORDER,
) -> str | None:
    """Apply score bands against an explicit lower-bound mapping."""

    ordered = tuple(dict.fromkeys(order))
    value = _number(score)
    if value is None:
        return None
    bounded = max(0.0, min(100.0, value))
    for regime in reversed(ordered):
        if bounded >= float(lower_bounds[regime]):
            return regime
    return ordered[0]


def classify_regime(score: float | int | None) -> str | None:
    """Apply the initial five fixed score bands."""

    return classify_regime_with_bounds(
        score,
        lower_bounds=REGIME_LOWER_BOUNDS,
        order=REGIME_ORDER,
    )


def apply_regime_hysteresis_with_bounds(
    score: float | int | None,
    previous_regime: str | None,
    *,
    lower_bounds: Mapping[str, float],
    hysteresis: Mapping[str, Mapping[str, float]],
    order: Iterable[str] = REGIME_ORDER,
) -> str | None:
    """Keep a regime until an explicitly configured exit threshold is crossed."""

    ordered = tuple(dict.fromkeys(order))
    value = _number(score)
    if value is None:
        return previous_regime
    candidate = classify_regime_with_bounds(value, lower_bounds=lower_bounds, order=ordered)
    previous = str(previous_regime or "").upper()
    if previous not in ordered:
        return candidate
    previous_index = ordered.index(previous)
    candidate_index = ordered.index(candidate or previous)
    if candidate_index == previous_index:
        return previous
    thresholds = hysteresis[previous]
    if candidate_index > previous_index:
        return candidate if value >= thresholds.get("up", 101.0) else previous
    return candidate if value < thresholds.get("down", -1.0) else previous


def apply_regime_hysteresis(
    score: float | int | None,
    previous_regime: str | None,
) -> str | None:
    """Keep a regime until its configured exit threshold is crossed."""

    return apply_regime_hysteresis_with_bounds(
        score,
        previous_regime,
        lower_bounds=REGIME_LOWER_BOUNDS,
        hysteresis=REGIME_HYSTERESIS,
        order=REGIME_ORDER,
    )


def smooth_score(
    previous_display_score: float | int | None,
    current_raw_score: float | int | None,
    *,
    alpha: float = SMOOTHING_ALPHA,
) -> float | None:
    """Return alpha*previous + (1-alpha)*current, preserving the first value."""

    current = _number(current_raw_score)
    previous = _number(previous_display_score)
    if current is None:
        return previous
    if previous is None:
        return round(current, 6)
    bounded_alpha = max(0.0, min(1.0, float(alpha)))
    return round(bounded_alpha * previous + (1.0 - bounded_alpha) * current, 6)


smooth_display_score = smooth_score
apply_hysteresis = apply_regime_hysteresis


def calculate_confidence(
    *,
    universe_coverage: float | None = None,
    quote_freshness: float | None = None,
    historical_coverage: float | None = None,
    component_availability: float | None = None,
    provider_quality: float | None = None,
    conflict_quality: float | None = None,
) -> float:
    """Combine available quality dimensions into a bounded 0-100 confidence."""

    raw_values = {
        "universe_coverage": universe_coverage,
        "quote_freshness": quote_freshness,
        "historical_coverage": historical_coverage,
        "component_availability": component_availability,
        "provider_quality": provider_quality,
        "conflict_quality": conflict_quality,
    }
    available = {
        metric_name: max(0.0, min(100.0, value))
        for metric_name, raw in raw_values.items()
        if (value := _number(raw)) is not None
    }
    weight = sum(CONFIDENCE_WEIGHTS[metric_name] for metric_name in available)
    if weight <= 0:
        return 0.0
    score = sum(available[metric_name] * CONFIDENCE_WEIGHTS[metric_name] for metric_name in available) / weight
    return round(score, 2)


def build_market_score_snapshot(
    components: Mapping[str, Any],
    *,
    trade_date: Any = None,
    previous_display_score: float | None = None,
    previous_regime: str | None = None,
    coverage: float | None = None,
    quality_status: str | None = None,
    confidence: float | None = None,
    last_reliable_score: float | None = None,
) -> MarketScoreSnapshot:
    """Assemble a score result with deterministic degraded/frozen behavior."""

    aggregation = aggregate_component_scores(components)
    gate = coverage_gate(coverage) if coverage is not None else CoverageDecision(1.0, "VALID", False)
    source_quality = str(getattr(quality_status, "value", quality_status) or "").upper()
    if source_quality and source_quality not in {"VALID", "DEGRADED"}:
        gate = CoverageDecision(gate.coverage, "FROZEN", True, "data_quality")
    elif source_quality == "DEGRADED" and not gate.is_frozen and gate.status == "VALID":
        gate = CoverageDecision(gate.coverage, "DEGRADED", False, "provider_quality_degraded")
    # A coverage failure is the more fundamental signal: do not replace the
    # provider/data-quality freeze reason with a secondary component failure.
    if aggregation.score is None and not gate.is_frozen:
        gate = CoverageDecision(gate.coverage, "FROZEN", True, "insufficient_component_coverage")
    if gate.is_frozen:
        raw_score = None
        display_score = _number(last_reliable_score)
        regime = apply_regime_hysteresis(display_score, previous_regime) if display_score is not None else previous_regime
    else:
        raw_score = aggregation.score
        display_score = smooth_score(previous_display_score, raw_score)
        regime = apply_regime_hysteresis(display_score, previous_regime)
    component_values = {
        component_name: value if isinstance(value, ComponentScore) else ComponentScore(
            name=component_name,
            score=_component_value(value),
        )
        for component_name, value in components.items()
    }
    default_confidence = min(gate.coverage * 100.0, aggregation.available_weight * 100.0)
    return MarketScoreSnapshot(
        trade_date=trade_date,
        raw_score=raw_score,
        display_score=display_score,
        regime=regime,
        confidence=round(float(confidence if confidence is not None else default_confidence), 2),
        quality_status="FROZEN" if gate.is_frozen else gate.status,
        is_frozen=gate.is_frozen,
        freeze_reason=gate.reason,
        components=component_values,
        previous_display_score=previous_display_score,
        available_component_weight=aggregation.available_weight,
    )
