"""Human-reviewed calibration evidence, never automatic parameter mutation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .config import (
    CALIBRATION_ENGINE_VERSION,
    CALIBRATION_RECOMMENDATIONS,
    MAX_GRID_SIZE,
    MIN_CALIBRATION_CASES,
    MIN_CALIBRATION_TRADE_DATES,
    current_production_config,
)
from .metrics import action_frequency, summarise_values


_PARAMETER_ALIASES = {
    "opportunity": "candidate.action_opportunity_min",
    "opportunity_threshold": "candidate.action_opportunity_min",
    "entry": "candidate.action_entry_min",
    "entry_threshold": "candidate.action_entry_min",
    "portfolio_fit": "candidate.action_fit_min",
    "portfolio_fit_threshold": "candidate.action_fit_min",
    "rr": "candidate.rr_action_min",
    "rr_threshold": "candidate.rr_action_min",
    "decision_edge": "candidate.min_decision_edge",
    "decision_edge_threshold": "candidate.min_decision_edge",
}
_CASE_FIELD_ALIASES = {
    "candidate.action_opportunity_min": "opportunity_score",
    "candidate.action_entry_min": "entry_score",
    "candidate.action_fit_min": "portfolio_fit_score",
    "candidate.rr_action_min": "risk_reward_ratio",
    "candidate.min_decision_edge": "decision_edge",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _normalise_parameter(target_parameter: str) -> str:
    value = str(target_parameter or "").strip()
    return _PARAMETER_ALIASES.get(value, value)


def parameter_field(target_parameter: str) -> str:
    path = _normalise_parameter(target_parameter)
    return _CASE_FIELD_ALIASES.get(path, path.rsplit(".", 1)[-1])


def current_parameter_value(target_parameter: str, config: Mapping[str, Any] | None = None) -> Any:
    config = config or current_production_config()
    path = _normalise_parameter(target_parameter)
    value: Any = config
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise ValueError(f"unsupported_calibration_parameter:{target_parameter}")
        value = value[part]
    return value


def build_parameter_grid(
    target_parameter: str,
    *,
    current_value: Any | None = None,
    requested: Iterable[Any] | None = None,
    max_size: int = MAX_GRID_SIZE,
) -> list[Any]:
    """Create a bounded safe grid; arbitrary callables and infinite grids are impossible."""

    if max_size <= 0 or max_size > MAX_GRID_SIZE:
        raise ValueError("invalid_grid_cap")
    current = current_parameter_value(target_parameter) if current_value is None else current_value
    if requested is not None:
        values = list(requested)
        if len(values) > max_size:
            raise ValueError("calibration_grid_too_large")
    elif isinstance(current, (int, float)) and not isinstance(current, bool):
        if parameter_field(target_parameter) == "rr_action_min":
            values = [round(value, 2) for value in (1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0)]
        else:
            centre = float(current)
            values = [int(value) if float(value).is_integer() else round(value, 4) for value in range(math.floor(centre - 5), math.ceil(centre + 5) + 1)]
    else:
        raise ValueError("calibration_parameter_requires_explicit_safe_grid")
    if not values:
        raise ValueError("calibration_grid_empty")
    if len(values) > max_size:
        raise ValueError("calibration_grid_too_large")
    if any(isinstance(value, (dict, list, tuple, set)) for value in values):
        raise ValueError("calibration_grid_values_must_be_scalars")
    try:
        return sorted(set(values), key=lambda value: float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("calibration_grid_values_must_be_orderable") from exc


def renormalize_available_weights(weights: Mapping[str, float], available: Iterable[str]) -> dict[str, float]:
    """Omit missing factors and reweight available factors; never replace missing with zero."""

    available_set = set(available)
    selected = {key: float(value) for key, value in weights.items() if key in available_set and float(value) > 0}
    total = sum(selected.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in selected.items()}


def one_factor_weight_perturbations(weights: Mapping[str, float], factor: str, values: Iterable[float]) -> list[dict[str, float]]:
    if factor not in weights:
        raise ValueError(f"unknown_factor:{factor}")
    original = dict((key, float(value)) for key, value in weights.items())
    result = []
    for value in values:
        candidate = dict(original)
        candidate[factor] = max(0.0, float(value))
        other_total = sum(item for key, item in original.items() if key != factor)
        remaining = max(0.0, 1.0 - candidate[factor])
        if other_total <= 0 and remaining > 0:
            raise ValueError("cannot_renormalize_weight_perturbation")
        for key in candidate:
            if key != factor:
                candidate[key] = original[key] / other_total * remaining if other_total else 0.0
        result.append(candidate)
    return result


def _metric_value(value: Any) -> float | None:
    if isinstance(value, Mapping):
        for key in ("median_excess_return", "median", "mean_excess_return", "mean"):
            if key in value:
                return _number(value[key])
        nested = value.get("metrics")
        if nested is not value:
            return _metric_value(nested)
    return _number(value)


def assess_robustness(variant_results: Mapping[Any, Any] | Iterable[Mapping[str, Any]], *, current_value: Any | None = None) -> dict[str, Any]:
    """Classify a local plateau versus a sharp peak, without selecting production config."""

    pairs: list[tuple[float, float]] = []
    if isinstance(variant_results, Mapping):
        iterator = variant_results.items()
    else:
        iterator = ((item.get("parameter_variant", item.get("value")), item) for item in variant_results)
    for variant, result in iterator:
        x = _number(variant)
        y = _metric_value(result)
        if x is not None and y is not None:
            pairs.append((x, y))
    pairs.sort()
    if len(pairs) < 3:
        return {"status": "INSUFFICIENT_EVIDENCE", "variant_count": len(pairs), "plateau": False}
    best_index, (best_x, best_y) = max(enumerate(pairs), key=lambda item: (item[1][1], -abs(item[1][0])))
    tolerance = max(0.002, abs(best_y) * 0.20)
    near_best = [x for x, y in pairs if best_y - y <= tolerance]
    gaps = [right - left for (left, _), (right, _) in zip(pairs, pairs[1:]) if right > left]
    step = min(gaps) if gaps else 1.0
    contiguous = 1
    longest = 1
    for left, right in zip(near_best, near_best[1:]):
        if abs(right - left) <= step * 1.5:
            contiguous += 1
            longest = max(longest, contiguous)
        else:
            contiguous = 1
    status = "ROBUST_PLATEAU" if longest >= 3 else "FRAGILE_PEAK" if best_index not in {0, len(pairs) - 1} else "EDGE_SENSITIVE"
    return {
        "status": status,
        "variant_count": len(pairs),
        "plateau": status == "ROBUST_PLATEAU",
        "selected_variant_for_evidence": best_x,
        "best_observed_metric": best_y,
        "near_best_variants": near_best,
        "current_value": _number(current_value) if current_value is not None else None,
        "tolerance": tolerance,
    }


def _sample_count(metrics: Mapping[str, Any] | None) -> int:
    if not metrics:
        return 0
    return int(metrics.get("sample_count") or metrics.get("case_count") or 0)


def recommend_calibration(
    *,
    baseline: Mapping[str, Any],
    challenger: Mapping[str, Any],
    train: Mapping[str, Any],
    validation: Mapping[str, Any],
    test: Mapping[str, Any],
    robustness: Mapping[str, Any],
    sample_counts: Mapping[str, Any] | None = None,
    quality_status: str = "FULL",
    leakage_status: str = "PASS",
    fold_directions: Iterable[bool] | None = None,
) -> str:
    """Apply conservative evidence rules and return only the four legal recommendations."""

    sample_counts = sample_counts or {}
    total_cases = int(sample_counts.get("case_count", max(_sample_count(train), _sample_count(validation), _sample_count(test))))
    total_dates = int(sample_counts.get("trade_date_count", 0))
    if total_cases < MIN_CALIBRATION_CASES or total_dates < MIN_CALIBRATION_TRADE_DATES:
        return "INSUFFICIENT_EVIDENCE"
    if str(quality_status).upper() not in {"FULL", "VALID"} or str(leakage_status).upper() not in {"PASS", "VALID"}:
        return "INSUFFICIENT_EVIDENCE"
    if robustness.get("status") != "ROBUST_PLATEAU":
        return "INSUFFICIENT_EVIDENCE" if robustness.get("status") == "INSUFFICIENT_EVIDENCE" else "KEEP_CURRENT"
    baseline_validation = _metric_value(validation.get("baseline", baseline))
    challenger_validation = _metric_value(validation.get("challenger", challenger))
    baseline_test = _metric_value(test.get("baseline", baseline))
    challenger_test = _metric_value(test.get("challenger", challenger))
    if None in (baseline_validation, challenger_validation, baseline_test, challenger_test):
        return "INSUFFICIENT_EVIDENCE"
    validation_improvement = challenger_validation - baseline_validation
    test_delta = challenger_test - baseline_test
    if validation_improvement <= 0.0:
        return "KEEP_CURRENT"
    if test_delta < -0.01:
        return "REJECT_CHANGE"
    baseline_tail = _number((test.get("baseline", baseline) or {}).get("p05")) if isinstance(test.get("baseline", baseline), Mapping) else None
    challenger_tail = _number((test.get("challenger", challenger) or {}).get("p05")) if isinstance(test.get("challenger", challenger), Mapping) else None
    if baseline_tail is not None and challenger_tail is not None and challenger_tail < baseline_tail - 0.05:
        return "REJECT_CHANGE"
    directions = list(fold_directions or [True])
    if not directions or sum(bool(value) for value in directions) < math.ceil(len(directions) * 0.60):
        return "KEEP_CURRENT"
    return "CONSIDER_CHANGE"


def evaluate_threshold_variants(
    cases: Iterable[Mapping[str, Any]],
    *,
    field: str,
    variants: Iterable[float],
    value_key: str = "excess_return",
    bootstrap_iterations: int = 500,
    seed: int = 0,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in cases]
    result = []
    for value in variants:
        selected = [row for row in rows if _number(row.get(field)) is not None and float(row[field]) >= float(value)]
        metrics = summarise_values(selected, value_key=value_key, bootstrap_iterations=bootstrap_iterations, seed=seed)
        result.append({
            "parameter_variant": value,
            "selected_case_count": len(selected),
            "metrics": metrics,
            "action_frequency": action_frequency(rows, threshold=float(value), field=field),
        })
    return result


def build_calibration_evidence(
    cases: Iterable[Mapping[str, Any]],
    *,
    target_parameter: str,
    parameter_grid: Iterable[Any] | None = None,
    splits: Iterable[Any] | None = None,
    seed: int = 0,
    bootstrap_iterations: int = 500,
) -> dict[str, Any]:
    """Produce baseline/challenger evidence using train+validation only for selection."""

    rows = [dict(row) for row in cases]
    field = parameter_field(target_parameter)
    current = current_parameter_value(target_parameter)
    grid = build_parameter_grid(target_parameter, current_value=current, requested=parameter_grid)
    dates = sorted({row.get("trade_date") for row in rows if row.get("trade_date") is not None})
    if splits is None:
        from .splits import chronological_splits

        splits = chronological_splits(dates)
    split_list = list(splits)
    split = split_list[0] if split_list else None
    if split is not None and hasattr(split, "train_dates"):
        train_dates = set(split.train_dates)
        validation_dates = set(split.validation_dates)
        test_dates = set(split.test_dates)
        train_rows = [row for row in rows if row.get("trade_date") in train_dates]
        validation_rows = [row for row in rows if row.get("trade_date") in validation_dates]
        test_rows = [row for row in rows if row.get("trade_date") in test_dates]
    else:
        train_rows, validation_rows, test_rows = rows, [], []
    train_variants = evaluate_threshold_variants(train_rows, field=field, variants=grid, seed=seed, bootstrap_iterations=bootstrap_iterations)
    validation_variants = evaluate_threshold_variants(validation_rows, field=field, variants=grid, seed=seed, bootstrap_iterations=bootstrap_iterations)
    # The candidate is selected before test data is ever evaluated.
    selection_pool = validation_variants if validation_rows else train_variants
    selected = max(selection_pool, key=lambda item: (_metric_value(item["metrics"]) if _metric_value(item["metrics"]) is not None else -float("inf"), -abs(float(item["parameter_variant"]) - float(current)))) if selection_pool else {"parameter_variant": current, "metrics": {}}
    challenger_value = selected["parameter_variant"]
    test_variants = evaluate_threshold_variants(test_rows, field=field, variants=[current, challenger_value], seed=seed, bootstrap_iterations=bootstrap_iterations)
    def by_value(items: list[dict[str, Any]], value: Any) -> dict[str, Any]:
        return next((item["metrics"] for item in items if item["parameter_variant"] == value), {"sample_count": 0})

    baseline_train = by_value(train_variants, current)
    challenger_train = by_value(train_variants, challenger_value)
    baseline_validation = by_value(validation_variants, current)
    challenger_validation = by_value(validation_variants, challenger_value)
    baseline_test = by_value(test_variants, current)
    challenger_test = by_value(test_variants, challenger_value)
    robustness = assess_robustness(validation_variants or train_variants, current_value=current)
    sample_counts = {
        "case_count": len(rows),
        "trade_date_count": len(dates),
        "train_case_count": len(train_rows),
        "validation_case_count": len(validation_rows),
        "test_case_count": len(test_rows),
        "train_trade_date_count": len({row.get("trade_date") for row in train_rows}),
        "validation_trade_date_count": len({row.get("trade_date") for row in validation_rows}),
        "test_trade_date_count": len({row.get("trade_date") for row in test_rows}),
    }
    recommendation = recommend_calibration(
        baseline=baseline_train,
        challenger=challenger_train,
        train={"baseline": baseline_train, "challenger": challenger_train},
        validation={"baseline": baseline_validation, "challenger": challenger_validation},
        test={"baseline": baseline_test, "challenger": challenger_test},
        robustness=robustness,
        sample_counts=sample_counts,
        quality_status="FULL" if len(dates) >= 252 else "DIAGNOSTIC_ONLY",
        leakage_status="PASS",
    )
    if len(dates) < 252:
        recommendation = "INSUFFICIENT_EVIDENCE"
    return {
        "calibration_version": CALIBRATION_ENGINE_VERSION,
        "target_parameter": target_parameter,
        "current_value": current,
        "challenger_value": challenger_value,
        "parameter_grid": grid,
        "baseline": {"train": baseline_train, "validation": baseline_validation, "test": baseline_test},
        "challenger": {"train": challenger_train, "validation": challenger_validation, "test": challenger_test},
        "train": {"baseline": baseline_train, "challenger": challenger_train},
        "validation": {"baseline": baseline_validation, "challenger": challenger_validation},
        "test": {"baseline": baseline_test, "challenger": challenger_test},
        "robustness": robustness,
        "sample_counts": sample_counts,
        "recommendation": recommendation,
        "selection_rule": "TRAIN_VALIDATION_ONLY; TEST_READ_AFTER_CHALLENGER_SELECTION",
        "no_auto_apply": True,
        "known_limitations": ["Calibration is evidence for human review, not an optimizer or production mutation."],
    }


__all__ = [
    "parameter_field",
    "current_parameter_value",
    "build_parameter_grid",
    "renormalize_available_weights",
    "one_factor_weight_perturbations",
    "assess_robustness",
    "recommend_calibration",
    "evaluate_threshold_variants",
    "build_calibration_evidence",
]
