"""Historical EOD Market Score deterministic recompute."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date
from statistics import fmean
from typing import Any, Iterable, Mapping

from ..governance.registry import market_regime_settings
from ..history.universe import resolve_equity_universe_from_facts
from ..market.engine.components import calculate_all_components
from ..market.engine.config import (
    MARKET_ENGINE_VERSION,
    PERCENTILE_MIN_SAMPLES,
    SCORE_CONFIG_VERSION,
    UNIVERSE_RULE_VERSION,
)
from ..market.engine.metrics import (
    calculate_cross_section_metrics,
    calculate_ma_breadth,
    calculate_ma_trend_metrics,
    calculate_new_high_low,
    next_median_index,
)
from ..market.engine.score import build_market_score_snapshot, calculate_confidence
from ..market.codes import normalize_security_code
from ..research.replay import ReplayCase
from ..services.market_engine import _history_coverage_codes, _included_quality
from .capability import combine_capabilities
from .config import (
    MARKET_HISTORY_LOOKBACK_TRADING_DAYS,
    RECOMPUTE_ENGINE_VERSION,
    UNIVERSE_VERSION,
    RecomputeCapability,
)
from .dataset import RecomputePitDataset, eod_cutoff


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _round_score(value: Any, digits: int = 1) -> float | None:
    number = None if value is None else float(value)
    return round(number, digits) if number is not None else None


def _component_history_payload(common: Mapping[str, Any], component: Any) -> dict[str, Any]:
    payload = dict(common)
    raw = dict(component.raw_metrics or {}) if hasattr(component, "raw_metrics") else {}
    payload.update(raw)
    return payload


def _identity_map(states: Mapping[str, Mapping[str, Any]], classification: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for code, state in states.items():
        result[code] = {
            "code": code,
            "board": None,
            "is_st": bool(
                (classification.get(code) or {}).get("status") in {"ST", "STAR_ST", "DELIST_RISK"}
            ),
        }
    return result


def _median_index_trend_metrics(
    current_index: float | None,
    previous_index_values: list[float],
) -> dict[str, Any]:
    result: dict[str, Any] = {"median_index": current_index}
    if current_index is None or not previous_index_values:
        return result
    result["median_index_prev"] = previous_index_values[-1]
    oldest = previous_index_values[-20] if len(previous_index_values) >= 20 else previous_index_values[0]
    if oldest:
        result["median_index_return_20"] = current_index / float(oldest) - 1.0
    return result


@dataclass
class HistoricalMarketRecomputeResult:
    trade_date: date
    raw_score: float | None
    display_score: float | None
    regime: str | None
    confidence: float
    quality_status: str
    is_frozen: bool
    freeze_reason: str | None
    universe: dict[str, Any]
    metrics: dict[str, Any]
    components: dict[str, dict[str, Any]]
    coverage: float
    history_coverage: float
    median_index: float | None
    warmup_start: date
    warmup_days: int
    warmup_complete: bool
    capability: str
    calculation_version: str = MARKET_ENGINE_VERSION
    score_config_version: str = SCORE_CONFIG_VERSION
    universe_rule_version: str = UNIVERSE_RULE_VERSION
    source_ids: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trade_date"] = self.trade_date.isoformat()
        payload["warmup_start"] = self.warmup_start.isoformat()
        payload["as_of"] = eod_cutoff(self.trade_date).isoformat()
        return payload

    def to_replay_case(self) -> ReplayCase:
        return ReplayCase(
            trade_date=self.trade_date,
            as_of=eod_cutoff(self.trade_date),
            scope="MARKET",
            replay_mode="DETERMINISTIC_RECOMPUTE",
            entity_id=f"recompute-market:{self.trade_date.isoformat()}",
            facts={
                "market_score": self.display_score if self.display_score is not None else self.raw_score,
                "raw_score": self.raw_score,
                "display_score": self.display_score,
                "market_regime": self.regime,
                "confidence": self.confidence,
                "quality_status": self.quality_status,
                "benchmark_index": self.median_index,
                "benchmark_id": None,
                "score_config_version": self.score_config_version,
                "calculation_version": RECOMPUTE_ENGINE_VERSION,
                "canonical_observation": "EOD_15_10",
                "recomputed": True,
            },
            quality_status=self.quality_status,
            reason_codes=(),
            source_ids=self.source_ids,
            coverage=self.coverage,
        )


def recompute_market_dates(
    dataset: RecomputePitDataset,
    *,
    dates: Iterable[date],
    parameter_snapshot: Mapping[str, Any] | None,
    capability_ceiling: str | None = None,
) -> list[HistoricalMarketRecomputeResult]:
    """Sequentially recompute Market Score with production smoothing/hysteresis.

    Every date from the dataset warmup window through the end date is executed
    in order so component percentile history, smoothing, hysteresis and median
    index carry real prior state into the requested window. Warmup outputs are
    discarded; only ``dates`` are returned.
    """

    lower_bounds, hysteresis = market_regime_settings(parameter_snapshot)
    previous_display_score: float | None = None
    previous_regime: str | None = None
    previous_index: float | None = None
    index_values: list[float] = []
    history_samples: dict[str, dict[str, list[float]]] = {}
    results: list[HistoricalMarketRecomputeResult] = []
    processed_days = 0
    requested_dates = list(dates)
    requested_set = set(requested_dates)
    internal_dates = [
        day for day in dataset.calendar_dates
        if dataset.warmup_start_date <= day <= dataset.end_date
    ]
    for day in requested_dates:
        if day not in internal_dates:
            internal_dates.append(day)

    for day in internal_dates:
        cutoff = eod_cutoff(day)
        available_cutoff = cutoff.replace(tzinfo=UTC)
        states = dataset.lifecycle_states(day, cutoff)
        classification = dataset.classification_by_code(day, cutoff)
        trading = dataset.trading_status_by_code(day, cutoff)
        calendar = {value for value in dataset.calendar_dates if value <= day}
        universe = resolve_equity_universe_from_facts(
            day,
            purpose="MARKET_SCORE",
            states=states,
            classification=classification,
            trading=trading,
            calendar_dates=calendar,
            held=set(),
            minimum_trading_days=20,
        )
        code_set = set(universe.eligible_codes)
        quote_rows = dataset.quote_rows(day, cutoff)
        history_rows: list[Any] = []
        for values in dataset.bars_by_code(day, cutoff).values():
            history_rows.extend(values)
        cross = calculate_cross_section_metrics(
            quote_rows,
            universe_codes=code_set,
            identity_by_code=_identity_map(states, classification),
        )
        ma = calculate_ma_breadth(
            history_rows,
            as_of=day,
            available_at=available_cutoff,
            universe_codes=code_set,
            current_prices=quote_rows,
        )
        trend = calculate_ma_trend_metrics(
            history_rows,
            as_of=day,
            available_at=available_cutoff,
            universe_codes=code_set,
        )
        nhnl = calculate_new_high_low(
            history_rows,
            as_of=day,
            available_at=available_cutoff,
            universe_codes=code_set,
            current_prices=quote_rows,
        )
        metrics: dict[str, Any] = dict(cross) | ma | trend | nhnl
        metrics["active_ratio"] = (cross.get("return_eligible_count") or 0) / max(cross.get("coherent_count") or 1, 1)
        metrics["market_volatility"] = cross.get("cross_section_return_std")
        metrics["new_low_60_ratio"] = nhnl.get("new_low_60_ratio")
        metrics["above_ma20_ratio"] = ma.get("above_ma20_ratio")
        metrics["above_ma60_ratio"] = ma.get("above_ma60_ratio")
        metrics["median_return"] = cross.get("all_a_median_return")
        median_return = _number(cross.get("all_a_median_return"))
        median_index = next_median_index(previous_index, median_return) if median_return is not None else None
        if median_index is not None:
            metrics.update(_median_index_trend_metrics(median_index, index_values))

        components = calculate_all_components(metrics, histories=history_samples)
        for component_name, component in components.items():
            samples = history_samples.get(component_name, {})
            component.historical_sample_count = max(
                (len(values) for values in samples.values()),
                default=0,
            )

        expected = len(universe.eligible_codes)
        received = int(cross.get("coherent_count") or 0)
        coverage = received / expected if expected else 0.0
        quality = _included_quality(quote_rows, code_set)
        if coverage < 0.95:
            quality = "MISSING"
        elif coverage < 0.98 and quality in {"VALID", "DEGRADED"}:
            quality = "DEGRADED"
        history_codes = _history_coverage_codes(
            history_rows,
            universe_codes=code_set,
            trade_date=day,
            available_at=available_cutoff,
        )
        history_coverage = len(history_codes) / max(len(code_set), 1) * 100
        confidence = calculate_confidence(
            universe_coverage=coverage * 100,
            quote_freshness=100 if quality in {"VALID", "DEGRADED"} else 0,
            historical_coverage=min(100.0, history_coverage),
            component_availability=round(
                sum(
                    100.0 * component.confidence
                    for component in components.values()
                ) / max(len(components), 1),
                2,
            ),
            provider_quality=100 if quality == "VALID" else 60 if quality == "DEGRADED" else 0,
            conflict_quality=100 if quality != "CONFLICT" else 0,
        )
        score = build_market_score_snapshot(
            components,
            trade_date=day,
            previous_display_score=previous_display_score,
            previous_regime=previous_regime,
            coverage=coverage,
            quality_status=quality,
            confidence=confidence,
            last_reliable_score=previous_display_score,
            lower_bounds=lower_bounds,
            hysteresis=hysteresis,
        )
        previous_display_score = score.display_score
        previous_regime = score.regime
        if median_index is not None:
            previous_index = median_index
            index_values.append(median_index)

        for component_name, component in components.items():
            samples = history_samples.setdefault(component_name, {})
            payload = _component_history_payload(metrics, component)
            for metric_name, value in payload.items():
                number = _number(value)
                if number is None:
                    continue
                samples.setdefault(str(metric_name), []).append(number)

        sample_counts = [
            len(values)
            for component_samples in history_samples.values()
            for values in component_samples.values()
        ]
        warmup_complete = bool(
            processed_days >= PERCENTILE_MIN_SAMPLES
            and sample_counts
            and min(sample_counts) >= PERCENTILE_MIN_SAMPLES
        )
        capability = (
            RecomputeCapability.FULL_PIT_EQUIVALENT
            if warmup_complete
            and components.get("diffusion") is not None
            and components["diffusion"].score is not None
            and quality in {"VALID", "DEGRADED"}
            else RecomputeCapability.PARTIAL_PIT_RECOMPUTE
        )
        if capability_ceiling is not None:
            capability = combine_capabilities(capability, capability_ceiling)
        if day in requested_set:
            results.append(HistoricalMarketRecomputeResult(
                trade_date=day,
                raw_score=_round_score(score.raw_score),
                display_score=_round_score(score.display_score),
                regime=score.regime,
                confidence=round(float(score.confidence or 0.0), 2),
                quality_status=score.quality_status,
                is_frozen=score.is_frozen,
                freeze_reason=score.freeze_reason,
                universe=universe.as_dict(),
                metrics=metrics,
                components={name: component.to_dict() for name, component in components.items()},
                coverage=coverage,
                history_coverage=history_coverage,
                median_index=median_index,
                warmup_start=dataset.warmup_start_date,
                warmup_days=processed_days,
                warmup_complete=warmup_complete,
                capability=str(capability),
                source_ids=tuple(dataset.source_ids()),
            ))
        processed_days += 1
    return results


def market_recompute_capability(results: Iterable[HistoricalMarketRecomputeResult]) -> str:
    """Return the worst capability across the requested date cohort."""

    capabilities = {str(result.capability) for result in results}
    if RecomputeCapability.FULL_PIT_EQUIVALENT in capabilities and len(capabilities) == 1:
        return RecomputeCapability.FULL_PIT_EQUIVALENT
    if RecomputeCapability.DATA_GAP in capabilities:
        return RecomputeCapability.DATA_GAP
    if RecomputeCapability.LEAKAGE_BLOCKED in capabilities:
        return RecomputeCapability.LEAKAGE_BLOCKED
    return RecomputeCapability.PARTIAL_PIT_RECOMPUTE


__all__ = [
    "HistoricalMarketRecomputeResult",
    "market_recompute_capability",
    "recompute_market_dates",
]
