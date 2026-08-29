"""Deterministic date-block bootstrap utilities."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Iterable
from statistics import mean
from typing import Any


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def date_blocks(rows: Iterable[dict[str, Any]], *, date_key: str = "trade_date") -> list[list[dict[str, Any]]]:
    blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(date_key)
        if value is None:
            continue
        key = value.isoformat() if hasattr(value, "isoformat") else str(value)
        blocks[key].append(row)
    return [blocks[key] for key in sorted(blocks)]


def date_block_bootstrap(
    rows: Iterable[dict[str, Any]],
    *,
    value_key: str = "excess_return",
    statistic: Callable[[list[float]], float] | None = None,
    iterations: int = 1_000,
    seed: int = 0,
    date_key: str = "trade_date",
) -> dict[str, Any]:
    """Resample whole trade dates so cross-sectional cases are not iid samples."""

    if iterations <= 0:
        raise ValueError("bootstrap_iterations_must_be_positive")
    blocks = date_blocks(rows, date_key=date_key)
    values_by_block = [
        [float(row[value_key]) for row in block if row.get(value_key) is not None]
        for block in blocks
    ]
    values_by_block = [values for values in values_by_block if values]
    if not values_by_block:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "iterations": 0,
            "seed": seed,
            "block_count": len(blocks),
            "confidence_interval": {"lower": None, "upper": None},
            "statistic": None,
        }
    statistic = statistic or (lambda values: mean(values))
    rng = random.Random(seed)
    observed = statistic([item for block in values_by_block for item in block])
    samples: list[float] = []
    for _ in range(iterations):
        sampled = [values_by_block[index] for index in (rng.randrange(len(values_by_block)) for _ in values_by_block)]
        flattened = [item for block in sampled for item in block]
        samples.append(float(statistic(flattened)))
    return {
        "status": "COMPLETED",
        "iterations": iterations,
        "seed": seed,
        "block_count": len(values_by_block),
        "confidence_interval": {
            "lower": _percentile(samples, 0.025),
            "upper": _percentile(samples, 0.975),
            "method": "PERCENTILE_DATE_BLOCK_BOOTSTRAP",
        },
        "statistic": observed,
    }


def bootstrap_confidence_interval(*args, **kwargs) -> dict[str, Any]:
    """Compatibility alias with the explicit date-block implementation."""

    return date_block_bootstrap(*args, **kwargs)


__all__ = ["date_blocks", "date_block_bootstrap", "bootstrap_confidence_interval"]
