"""Deterministic aggregate metrics for research cases."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from statistics import mean, median
from typing import Any

from .bootstrap import date_block_bootstrap


def _numeric(values: Iterable[Any]) -> list[float]:
    result = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    ratio = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * ratio


def score_bucket(score: float | None, *, width: int = 20) -> str | None:
    if score is None:
        return None
    value = float(score)
    if not math.isfinite(value):
        return None
    if width == 10:
        lower = max(0, min(90, int(value // 10) * 10))
        return f"{lower}-{min(100, lower + 10)}"
    boundaries = ((0, 20), (21, 40), (41, 60), (61, 80), (81, 100))
    for lower, upper in boundaries:
        if lower <= value <= upper:
            return f"{lower}-{upper}"
    return "<0" if value < 0 else ">100"


def _trade_dates(rows: Iterable[dict[str, Any]]) -> set[str]:
    result = set()
    for row in rows:
        value = row.get("trade_date")
        if value is not None:
            result.add(value.isoformat() if hasattr(value, "isoformat") else str(value)[:10])
    return result


def summarise_values(
    rows: Iterable[dict[str, Any]],
    *,
    value_key: str = "excess_return",
    bootstrap_iterations: int = 500,
    seed: int = 0,
) -> dict[str, Any]:
    materialised = list(rows)
    values = _numeric(row.get(value_key) for row in materialised)
    mfe = _numeric(row.get("mfe") for row in materialised)
    mae = _numeric(row.get("mae") for row in materialised)
    directional = _numeric(row.get("directional_return") for row in materialised)
    positive = sum(value > 0 for value in values)
    output: dict[str, Any] = {
        "sample_count": len(values),
        "case_count": len(materialised),
        "unique_trade_dates": len(_trade_dates(materialised)),
        "mean": mean(values) if values else None,
        "median": median(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "p05": _percentile(values, 0.05),
        "p25": _percentile(values, 0.25),
        "p75": _percentile(values, 0.75),
        "p95": _percentile(values, 0.95),
        "negative_tail_frequency": sum(value < 0 for value in values) / len(values) if values else None,
        "positive_frequency": positive / len(values) if values else None,
        "median_mfe": median(mfe) if mfe else None,
        "median_mae": median(mae) if mae else None,
        "median_directional_return": median(directional) if directional else None,
        "coverage": mean(_numeric([row.get("coverage") for row in materialised])) if any(row.get("coverage") is not None for row in materialised) else None,
    }
    if values and materialised:
        output["confidence_interval"] = date_block_bootstrap(
            materialised,
            value_key=value_key,
            iterations=bootstrap_iterations,
            seed=seed,
        )["confidence_interval"]
    else:
        output["confidence_interval"] = {"lower": None, "upper": None}
    return output


def aggregate_by(
    rows: Iterable[dict[str, Any]],
    *,
    dimensions: tuple[str, ...] = (),
    value_key: str = "excess_return",
    bootstrap_iterations: int = 500,
    seed: int = 0,
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(dimension) for dimension in dimensions)
        groups[key].append(row)
    result = []
    for key, group in sorted(groups.items(), key=lambda item: tuple(str(value) for value in item[0])):
        payload = {dimension: value for dimension, value in zip(dimensions, key)}
        payload["metrics"] = summarise_values(
            group, value_key=value_key, bootstrap_iterations=bootstrap_iterations, seed=seed
        )
        result.append(payload)
    return result


def market_metric_slices(
    cases: Iterable[dict[str, Any]],
    *,
    horizons: Iterable[int],
    bootstrap_iterations: int = 500,
    seed: int = 0,
) -> list[dict[str, Any]]:
    rows = list(cases)
    output: list[dict[str, Any]] = []
    for horizon in horizons:
        horizon_rows = [row for row in rows if row.get("horizon") == horizon]
        for row in horizon_rows:
            row.setdefault("score_bucket", score_bucket(row.get("market_score")))
        output.extend({
            "metric_family": "MARKET_SCORE_BUCKET",
            "horizon": horizon,
            "score_bucket": item.get("score_bucket"),
            "market_regime": item.get("market_regime"),
            "metrics": item["metrics"],
        } for item in aggregate_by(
            horizon_rows,
            dimensions=("score_bucket", "market_regime"),
            bootstrap_iterations=bootstrap_iterations,
            seed=seed,
        ))
    return output


def candidate_metric_slices(
    cases: Iterable[dict[str, Any]],
    *,
    value_key: str = "excess_return",
    dimensions: tuple[str, ...] = ("security_type", "stage", "score_bucket", "market_regime", "horizon"),
    bootstrap_iterations: int = 500,
    seed: int = 0,
) -> list[dict[str, Any]]:
    rows = list(cases)
    for row in rows:
        row.setdefault("score_bucket", score_bucket(row.get("score")))
    return [
        {"metric_family": "CANDIDATE_FORWARD_OUTCOME", **item}
        for item in aggregate_by(
            rows,
            dimensions=dimensions,
            value_key=value_key,
            bootstrap_iterations=bootstrap_iterations,
            seed=seed,
        )
    ]


def action_frequency(rows: Iterable[dict[str, Any]], *, threshold: float, field: str = "decision_edge") -> dict[str, Any]:
    materialised = list(rows)
    eligible = [row for row in materialised if row.get(field) is not None]
    action = [row for row in eligible if float(row[field]) >= threshold]
    return {
        "threshold": threshold,
        "field": field,
        "sample_count": len(eligible),
        "action_count": len(action),
        "action_frequency": len(action) / len(eligible) if eligible else None,
        "missed_count": len(eligible) - len(action),
    }


__all__ = [
    "score_bucket",
    "summarise_values",
    "aggregate_by",
    "market_metric_slices",
    "candidate_metric_slices",
    "action_frequency",
]
