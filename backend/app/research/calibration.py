"""Human-reviewed calibration evidence, never automatic parameter mutation."""

from __future__ import annotations

import copy
import math
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from .config import (
    CALIBRATION_ENGINE_VERSION,
    MAX_GRID_SIZE,
    MIN_CALIBRATION_CASES,
    MIN_CALIBRATION_TRADE_DATES,
    current_production_config,
)
from .metrics import summarise_values


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
    "market_risk_on_threshold": "market.regime_lower_bounds.RISK_ON",
    "market.regime_risk_on_threshold": "market.regime_lower_bounds.RISK_ON",
    "market_regime_risk_on_threshold": "market.regime_lower_bounds.RISK_ON",
}
_CASE_FIELD_ALIASES = {
    "candidate.action_opportunity_min": "opportunity_score",
    "candidate.action_entry_min": "entry_score",
    "candidate.action_fit_min": "portfolio_fit_score",
    "candidate.rr_action_min": "risk_reward_ratio",
    "candidate.min_decision_edge": "decision_edge",
    "market.regime_lower_bounds.RISK_ON": "market_score",
    "market.regime_lower_bounds.RISK_OFF": "market_score",
    "market.regime_lower_bounds.NEUTRAL": "market_score",
    "market.regime_lower_bounds.STRONG_RISK_ON": "market_score",
    "market.regime_lower_bounds.STRONG_RISK_OFF": "market_score",
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


def _parameter_kind(target_parameter: str) -> str:
    path = _normalise_parameter(target_parameter)
    if path.startswith("market.regime_lower_bounds."):
        return "MARKET_REGIME_THRESHOLD"
    if ".factor_ablation." in path or path.startswith("factor_ablation."):
        return "FACTOR_ABLATION"
    if "factor_weights" in path or "action_score_weights" in path or "portfolio_fit_weights" in path:
        return "WEIGHT_PERTURBATION"
    return "THRESHOLD"


def parameter_field(target_parameter: str) -> str:
    path = _normalise_parameter(target_parameter)
    if path in _CASE_FIELD_ALIASES:
        return _CASE_FIELD_ALIASES[path]
    if path.startswith("market."):
        # Regime labels are not historical numeric observations.  The
        # deterministic input for a regime threshold is market_score.
        return "market_score"
    return path.rsplit(".", 1)[-1]


def current_parameter_value(target_parameter: str, config: Mapping[str, Any] | None = None) -> Any:
    config = config or current_production_config()
    path = _normalise_parameter(target_parameter)
    if _parameter_kind(path) == "FACTOR_ABLATION":
        return 1.0
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
    elif _parameter_kind(target_parameter) == "FACTOR_ABLATION":
        values = [0.0, 1.0]
    elif _parameter_kind(target_parameter) == "WEIGHT_PERTURBATION":
        centre = max(0.0, min(1.0, float(current)))
        values = [
            round(max(0.0, min(1.0, centre + offset)), 4)
            for offset in (-0.20, -0.10, 0.0, 0.10, 0.20)
        ]
    elif isinstance(current, (int, float)) and not isinstance(current, bool):
        if parameter_field(target_parameter) == "rr_action_min":
            values = [round(value, 2) for value in (1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0)]
        else:
            centre = float(current)
            values = [
                int(value) if float(value).is_integer() else round(value, 4)
                for value in range(math.floor(centre - 5), math.ceil(centre + 5) + 1)
            ]
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
    original = {key: float(value) for key, value in weights.items()}
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


def _is_weight_or_ablation(target_parameter: str) -> bool:
    return _parameter_kind(target_parameter) in {"WEIGHT_PERTURBATION", "FACTOR_ABLATION"}


def _is_opportunity_calibration(target_parameter: str) -> bool:
    return "opportunity" in _normalise_parameter(target_parameter).lower()


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
    target_parameter: str | None = None,
    censored_sample: bool = False,
    challenger_expands_sample: bool = False,
    quality_reasons: Iterable[str] | None = None,
    replay_capability: str | None = None,
) -> str:
    """Apply conservative evidence rules and return only the four legal recommendations."""

    sample_counts = sample_counts or {}
    total_cases = int(sample_counts.get("case_count", max(_sample_count(train), _sample_count(validation), _sample_count(test))))
    total_dates = int(sample_counts.get("trade_date_count", 0))
    if total_cases < MIN_CALIBRATION_CASES or total_dates < MIN_CALIBRATION_TRADE_DATES:
        return "INSUFFICIENT_EVIDENCE"
    if str(quality_status).upper() not in {"FULL", "VALID"} or str(leakage_status).upper() not in {"PASS", "VALID"}:
        return "INSUFFICIENT_EVIDENCE"
    if quality_reasons or str(replay_capability or "FULL").upper() not in {"FULL", "VALID", "PRODUCTION_REPLAY"}:
        return "INSUFFICIENT_EVIDENCE"
    if censored_sample and (
        (target_parameter and (_is_weight_or_ablation(target_parameter) or _is_opportunity_calibration(target_parameter)))
        or challenger_expands_sample
    ):
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


def _get_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def _set_path(value: dict[str, Any], path: str, replacement: Any) -> None:
    parts = path.split(".")
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            raise ValueError(f"unsupported_calibration_parameter:{path}")
        current = child
    current[parts[-1]] = replacement


def _variant_config(base_config: Mapping[str, Any], target_parameter: str, variant: Any) -> dict[str, Any]:
    config = copy.deepcopy(dict(base_config))
    path = _normalise_parameter(target_parameter)
    kind = _parameter_kind(path)
    if kind == "FACTOR_ABLATION":
        factor = path.rsplit(".", 1)[-1]
        candidate = _get_path(config, "candidate")
        weights_path = next(
            (
                f"candidate.{name}"
                for name in ("stock_factor_weights", "etf_factor_weights")
                if isinstance(candidate.get(name), Mapping) and factor in candidate[name]
            ),
            None,
        )
        if weights_path is None:
            raise ValueError(f"unknown_factor:{factor}")
        weights = _get_path(config, weights_path)
        value = 0.0 if float(variant) <= 0.0 else float(weights.get(factor, 0.0))
        _set_path(config, weights_path, one_factor_weight_perturbations(weights, factor, [value])[0])
        return config
    if "factor_weights" in path or "action_score_weights" in path or "portfolio_fit_weights" in path:
        weights_path, factor = path.rsplit(".", 1)
        weights = _get_path(config, weights_path)
        _set_path(config, weights_path, one_factor_weight_perturbations(weights, factor, [float(variant)])[0])
        return config
    if kind == "MARKET_REGIME_THRESHOLD":
        regime = path.rsplit(".", 1)[-1]
        old = float(_get_path(config, path))
        new = float(variant)
        delta = new - old
        _set_path(config, path, new)
        lower_bounds = _get_path(config, "market.regime_lower_bounds")
        regimes = list(lower_bounds) if isinstance(lower_bounds, Mapping) else []
        if regime in regimes:
            index = regimes.index(regime)
            # A lower bound is a boundary between the previous and current
            # regime.  Move the two sides of that boundary together: the
            # previous regime's ``up`` exit and the current regime's ``down``
            # exit.  The current regime's ``up`` belongs to the next boundary.
            pair = []
            if index > 0:
                pair.append((regimes[index - 1], "up"))
            pair.append((regime, "down"))
            for pair_regime, direction in pair:
                hysteresis_path = f"market.regime_hysteresis.{pair_regime}"
                try:
                    hysteresis = dict(_get_path(config, hysteresis_path))
                except KeyError:
                    continue
                if direction in hysteresis:
                    hysteresis[direction] = float(hysteresis[direction]) + delta
                    _set_path(config, hysteresis_path, hysteresis)
        return config
    _set_path(config, path, variant)
    return config


def apply_parameter_variant(base_config: Mapping[str, Any], target_parameter: str, variant: Any) -> dict[str, Any]:
    """Return the production config with exactly one calibrated change applied."""

    return _variant_config(base_config, target_parameter, variant)


def _component_scores(row: Mapping[str, Any]) -> dict[str, float]:
    components = row.get("components") or row.get("opportunity_components") or {}
    result: dict[str, float] = {}
    if not isinstance(components, Mapping):
        return result
    for name, value in components.items():
        score = value.get("score") if isinstance(value, Mapping) else value
        number = _number(score)
        if number is not None:
            result[str(name)] = number
    return result


def _recomputed_opportunity(row: Mapping[str, Any], config: Mapping[str, Any]) -> float | None:
    components = _component_scores(row)
    if not components:
        # Weight studies require the persisted component scores.  Reusing the
        # already aggregated opportunity score would falsely claim that the
        # perturbation had an effect.
        return None
    candidate = config.get("candidate") if isinstance(config.get("candidate"), Mapping) else {}
    weight_map = None
    for key in ("stock_factor_weights", "etf_factor_weights"):
        if isinstance(candidate, Mapping) and isinstance(candidate.get(key), Mapping):
            if key.startswith("etf") and str(row.get("security_type") or "").upper() == "ETF":
                weight_map = candidate[key]
                break
            if key.startswith("stock") and str(row.get("security_type") or "STOCK").upper() != "ETF":
                weight_map = candidate[key]
    if not weight_map:
        return _number(row.get("opportunity_score"))
    available = {key: value for key, value in components.items() if key in weight_map}
    weights = renormalize_available_weights(weight_map, available)
    if not weights:
        return None
    return round(sum(available[key] * weight for key, weight in weights.items()), 4)


def _recomputed_action_score(row: Mapping[str, Any], config: Mapping[str, Any], opportunity: float | None) -> float | None:
    candidate = config.get("candidate") if isinstance(config.get("candidate"), Mapping) else {}
    weights = candidate.get("action_score_weights") if isinstance(candidate, Mapping) else None
    entry = _number(row.get("entry_score"))
    fit = _number(row.get("portfolio_fit_score"))
    if not isinstance(weights, Mapping) or None in (opportunity, entry, fit):
        return _number(row.get("action_score"))
    return round(
        float(opportunity) * float(weights.get("opportunity", 0.0))
        + float(entry) * float(weights.get("entry", 0.0))
        + float(fit) * float(weights.get("fit", 0.0)),
        4,
    )


def _fit_component_scores(row: Mapping[str, Any]) -> dict[str, float]:
    source: Any = row.get("portfolio_fit_components") or row.get("fit_components") or row.get("portfolio_fit")
    if isinstance(source, Mapping) and isinstance(source.get("components"), Mapping):
        source = source["components"]
    if not isinstance(source, Mapping):
        return {}
    result: dict[str, float] = {}
    for name, value in source.items():
        score = value.get("score") if isinstance(value, Mapping) else value
        number = _number(score)
        if number is not None:
            result[str(name)] = number
    return result


def _recomputed_portfolio_fit(row: Mapping[str, Any], config: Mapping[str, Any]) -> float | None:
    components = _fit_component_scores(row)
    if not components:
        # A fit-weight study without persisted fit components cannot claim
        # that a weight override changed the production predicate.
        return None
    candidate = config.get("candidate") if isinstance(config.get("candidate"), Mapping) else {}
    weight_map = candidate.get("portfolio_fit_weights") if isinstance(candidate, Mapping) else None
    if not isinstance(weight_map, Mapping):
        return None
    available = {key: value for key, value in components.items() if key in weight_map}
    weights = renormalize_available_weights(weight_map, available)
    if not weights:
        return None
    return round(sum(available[key] * weights[key] for key in weights), 4)


def _market_eligible(row: Mapping[str, Any], variant: Any) -> bool:
    score = _number(row.get("market_score"))
    quality = str(row.get("market_quality", row.get("quality_status")) or "MISSING").upper()
    return (
        score is not None
        and quality in {"VALID", "DEGRADED"}
        and bool(row.get("market_available", True))
        and not bool(row.get("market_frozen"))
        and score >= float(variant)
    )


def production_gate_predicate(
    row: Mapping[str, Any],
    *,
    target_parameter: str,
    variant: Any,
    production_config: Mapping[str, Any] | None = None,
) -> bool:
    """Re-run the complete production eligibility predicate for one variant."""

    path = _normalise_parameter(target_parameter)
    config = _variant_config(production_config or current_production_config(), path, variant)
    if _parameter_kind(path) == "MARKET_REGIME_THRESHOLD" or path.startswith("market."):
        return _market_eligible(row, variant)

    candidate = config.get("candidate") if isinstance(config.get("candidate"), Mapping) else {}
    kind = _parameter_kind(path)
    opportunity = (
        _recomputed_opportunity(row, config)
        if kind in {"WEIGHT_PERTURBATION", "FACTOR_ABLATION"} and (
            "factor_weights" in path or ".factor_ablation." in path or path.startswith("factor_ablation.")
        )
        else _number(row.get("opportunity_score"))
    )
    fit = (
        _recomputed_portfolio_fit(row, config)
        if kind == "WEIGHT_PERTURBATION" and "portfolio_fit_weights" in path
        else _number(row.get("portfolio_fit_score"))
    )
    action_score = _recomputed_action_score(row, config, opportunity)
    entry = _number(row.get("entry_score"))
    rr = _number(row.get("risk_reward_ratio"))
    edge = _number(row.get("decision_edge"))
    coverage = _number(row.get("coverage", row.get("data_coverage")))
    confidence = _number(row.get("confidence"))
    if None in (opportunity, action_score, entry, fit, rr, edge, coverage, confidence):
        return False

    market_quality = str(row.get("market_quality") or "MISSING").upper()
    if not bool(row.get("market_available", False)) or bool(row.get("market_frozen")) or market_quality in {"MISSING", "INVALID", "STALE", "FROZEN"}:
        return False
    if str(row.get("candidate_quality_status", row.get("quality_status")) or "MISSING").upper() not in {"FULL", "VALID", "DEGRADED", "NORMAL"}:
        return False
    if str(row.get("quote_quality") or "MISSING").upper() not in {"VALID", "DEGRADED"}:
        return False
    if bool(row.get("hard_cap_violation")):
        return False
    if opportunity < float(candidate.get("action_opportunity_min", 70.0)):
        return False
    if entry < float(candidate.get("action_entry_min", 65.0)):
        return False
    if fit < float(candidate.get("action_fit_min", 65.0)):
        return False
    if rr < float(candidate.get("rr_action_min", 1.5)):
        return False
    if coverage < float(candidate.get("action_coverage_min", 0.70)):
        return False
    if confidence < float(candidate.get("action_confidence_min", 75.0)):
        return False
    if edge < float(candidate.get("min_decision_edge", 5.0)):
        return False
    if str(row.get("funding_mode") or "MISSING").upper() != "CASH_FUNDED":
        return False
    if bool(row.get("quote_is_proxy")) or bool(row.get("limit_up")) or bool(row.get("limit_down")):
        return False
    held_baseline = row.get("held_baseline")
    if isinstance(held_baseline, Mapping) and held_baseline:
        if not bool(held_baseline.get("available")):
            return False
        holding_edge = _number(row.get("edge_vs_current_holdings"))
        if holding_edge is None or holding_edge < float(candidate.get("min_holding_edge", 3.0)):
            return False
    return True


def evaluate_threshold_variants(
    cases: Iterable[Mapping[str, Any]],
    *,
    field: str | None = None,
    variants: Iterable[float],
    target_parameter: str | None = None,
    production_config: Mapping[str, Any] | None = None,
    value_key: str = "excess_return",
    bootstrap_iterations: int = 500,
    seed: int = 0,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in cases]
    target = target_parameter or field or ""
    use_production_gate = target_parameter is not None or production_config is not None
    result = []
    for value in variants:
        if use_production_gate:
            selected = [
                row for row in rows
                if production_gate_predicate(
                    row,
                    target_parameter=target,
                    variant=value,
                    production_config=production_config,
                )
            ]
        else:
            selected = [row for row in rows if _number(row.get(field or "")) is not None and float(row[field or ""]) >= float(value)]
        metrics = summarise_values(selected, value_key=value_key, bootstrap_iterations=bootstrap_iterations, seed=seed)
        result.append({
            "parameter_variant": value,
            "selected_case_count": len(selected),
            "eligible_case_ids": [str(row.get("entity_id") or row.get("id") or index) for index, row in enumerate(selected)],
            "metrics": metrics,
            "gate_predicate": "CURRENT_PRODUCTION_GATE_WITH_ONE_PARAMETER_OVERRIDE" if use_production_gate else "FIELD_THRESHOLD",
        })
    return result


def _date_value(value: Any) -> date | str | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)[:10]


def _select_variant(variants: list[dict[str, Any]], current: Any) -> dict[str, Any]:
    if not variants:
        return {"parameter_variant": current, "metrics": {}}
    return max(
        variants,
        key=lambda item: (
            _metric_value(item.get("metrics")) if _metric_value(item.get("metrics")) is not None else -float("inf"),
            -abs(float(item.get("parameter_variant")) - float(current)),
        ),
    )


def _metrics_for_variant(items: Iterable[dict[str, Any]], value: Any) -> dict[str, Any]:
    for item in items:
        if item.get("parameter_variant") == value:
            return item.get("metrics") or {}
    return {"sample_count": 0, "case_count": 0}


def _manifest_censored(manifest: Mapping[str, Any] | None) -> bool:
    if not manifest:
        return False
    scores = manifest.get("candidate_scores")
    if not isinstance(scores, Mapping):
        return False
    capabilities = scores.get("capabilities")
    return isinstance(capabilities, Mapping) and any(
        str(value).upper() == "CENSORED_PRODUCTION_SAMPLE" for value in capabilities.values()
    )


def build_calibration_evidence(
    cases: Iterable[Mapping[str, Any]],
    *,
    target_parameter: str,
    parameter_grid: Iterable[Any] | None = None,
    splits: Iterable[Any] | None = None,
    seed: int = 0,
    bootstrap_iterations: int = 500,
    production_config: Mapping[str, Any] | None = None,
    quality_status: str = "FULL",
    leakage_status: str = "PASS",
    availability_manifest: Mapping[str, Any] | None = None,
    replay_mode: str | None = None,
    replay_capability: str | None = None,
    scope: str | None = None,
    censored_sample: bool | None = None,
) -> dict[str, Any]:
    """Run every walk-forward fold before reading any fold test rows."""

    rows = [dict(row) for row in cases]
    config = production_config or current_production_config()
    path = _normalise_parameter(target_parameter)
    field = parameter_field(path)
    current = current_parameter_value(path, config)
    grid = build_parameter_grid(path, current_value=current, requested=parameter_grid)
    dates = sorted({value for row in rows if (value := _date_value(row.get("trade_date"))) is not None})
    if splits is None:
        from .splits import chronological_splits

        splits = chronological_splits([value for value in dates if isinstance(value, date)])
    split_list = list(splits)
    fold_records: list[dict[str, Any]] = []
    all_train_rows: list[dict[str, Any]] = []
    all_validation_rows: list[dict[str, Any]] = []
    all_test_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    if split_list and hasattr(split_list[0], "train_dates"):
        for split in split_list:
            train_dates = set(split.train_dates)
            validation_dates = set(split.validation_dates)
            test_dates = set(split.test_dates)
            train_rows = [row for row in rows if _date_value(row.get("trade_date")) in train_dates]
            validation_rows = [row for row in rows if _date_value(row.get("trade_date")) in validation_dates]
            train_variants = evaluate_threshold_variants(
                train_rows,
                field=field,
                target_parameter=path,
                variants=grid,
                production_config=config,
                seed=seed + int(split.fold),
                bootstrap_iterations=bootstrap_iterations,
            )
            validation_variants = evaluate_threshold_variants(
                validation_rows,
                field=field,
                target_parameter=path,
                variants=grid,
                production_config=config,
                seed=seed + int(split.fold),
                bootstrap_iterations=bootstrap_iterations,
            )
            # Each fold chooses its local challenger from the historical
            # training window.  Validation is evidence for that choice, not
            # the selection source; a single final challenger is fixed only
            # after all folds have supplied their validation evidence.
            selected = _select_variant(train_variants if train_rows else validation_variants, current)
            baseline_validation = _metrics_for_variant(validation_variants, current)
            selected_validation = _metrics_for_variant(validation_variants, selected["parameter_variant"])
            direction = bool(
                validation_rows
                and _metric_value(selected_validation) is not None
                and _metric_value(baseline_validation) is not None
                and _metric_value(selected_validation) > _metric_value(baseline_validation)
            )
            fold_records.append({
                "fold": split.fold,
                "status": getattr(split, "status", "COMPLETED"),
                "train_range": split.train_range,
                "validation_range": split.validation_range,
                "test_range": split.test_range,
                "train": {"variants": train_variants},
                "validation": {"variants": validation_variants},
                "selected_challenger": selected["parameter_variant"],
                "selection_source": "TRAIN" if train_rows else "VALIDATION_DIAGNOSTIC_FALLBACK",
                "selected_validation": selected_validation,
                "direction": direction,
                "final_challenger_direction": None,
                "test": None,
            })
            all_train_rows.extend(train_rows)
            all_validation_rows.extend(validation_rows)
            selection_rows.extend(validation_rows or train_rows)
    else:
        all_train_rows = rows
        selection_rows = rows
        fold_records.append({
            "fold": 0,
            "status": "DIAGNOSTIC_ONLY",
            "train": {"variants": evaluate_threshold_variants(rows, field=field, target_parameter=path, variants=grid, production_config=config, seed=seed, bootstrap_iterations=bootstrap_iterations)},
            "validation": {"variants": []},
            "selected_challenger": current,
            "selection_source": "NONE",
            "selected_validation": {},
            "direction": False,
            "final_challenger_direction": False,
            "test": None,
        })

    # Selection uses all validation evidence; test data is not read before the
    # final challenger is fixed.
    selection_variants = evaluate_threshold_variants(
        selection_rows,
        field=field,
        target_parameter=path,
        variants=grid,
        production_config=config,
        seed=seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    selected = _select_variant(selection_variants, current)
    challenger_value = selected["parameter_variant"]

    for fold in fold_records:
        validation_variants = (fold.get("validation") or {}).get("variants") or []
        baseline_fold = _metrics_for_variant(validation_variants, current)
        challenger_fold = _metrics_for_variant(validation_variants, challenger_value)
        baseline_value = _metric_value(baseline_fold)
        challenger_metric = _metric_value(challenger_fold)
        fold["final_challenger_direction"] = bool(
            baseline_value is not None
            and challenger_metric is not None
            and challenger_metric > baseline_value
        )

    # The fixed challenger is now applied to every held-out test fold.  Test
    # rows are not materialised until this point, so no held-out evidence can
    # influence local or final challenger selection.
    for index, split in enumerate(split_list):
        if not hasattr(split, "test_dates"):
            continue
        test_dates = set(split.test_dates)
        test_rows = [row for row in rows if _date_value(row.get("trade_date")) in test_dates]
        all_test_rows.extend(test_rows)
        test_variants = evaluate_threshold_variants(
            test_rows,
            field=field,
            target_parameter=path,
            variants=[current, challenger_value],
            production_config=config,
            seed=seed + int(split.fold),
            bootstrap_iterations=bootstrap_iterations,
        )
        if index < len(fold_records):
            fold_records[index]["test"] = {"variants": test_variants}

    aggregate_train = evaluate_threshold_variants(
        all_train_rows,
        field=field,
        target_parameter=path,
        variants=[current, challenger_value],
        production_config=config,
        seed=seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    aggregate_validation = evaluate_threshold_variants(
        all_validation_rows,
        field=field,
        target_parameter=path,
        variants=[current, challenger_value],
        production_config=config,
        seed=seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    aggregate_test = evaluate_threshold_variants(
        all_test_rows,
        field=field,
        target_parameter=path,
        variants=[current, challenger_value],
        production_config=config,
        seed=seed,
        bootstrap_iterations=bootstrap_iterations,
    )

    baseline_train = _metrics_for_variant(aggregate_train, current)
    challenger_train = _metrics_for_variant(aggregate_train, challenger_value)
    baseline_validation = _metrics_for_variant(aggregate_validation, current)
    challenger_validation = _metrics_for_variant(aggregate_validation, challenger_value)
    baseline_test = _metrics_for_variant(aggregate_test, current)
    challenger_test = _metrics_for_variant(aggregate_test, challenger_value)
    robustness = assess_robustness(selection_variants, current_value=current)
    is_censored = bool(censored_sample) if censored_sample is not None else _manifest_censored(availability_manifest) or any(bool(row.get("censored_sample")) for row in rows)
    challenger_expands = _number(challenger_value) is not None and _number(current) is not None and float(challenger_value) < float(current) and _parameter_kind(path) == "THRESHOLD"
    expanded_range_case_count = 0
    if challenger_expands:
        low = float(challenger_value)
        high = float(current)
        expanded_range_case_count = sum(
            1
            for row in rows
            if (number := _number(row.get(field))) is not None and low <= number < high
        )
    quality_reasons: list[str] = []
    if len(dates) < 252:
        quality_reasons.append("INSUFFICIENT_HISTORICAL_TRADE_DATES")
    if replay_mode and str(replay_mode).upper() == "DETERMINISTIC_RECOMPUTE":
        quality_reasons.append("DETERMINISTIC_RECOMPUTE_REQUIRES_EXPLICIT_PIT_DATASET")
    if challenger_expands and expanded_range_case_count == 0:
        quality_reasons.append("EXPANDED_THRESHOLD_RANGE_NOT_COVERED")
    if availability_manifest:
        survivorship = availability_manifest.get("survivorship")
        if isinstance(survivorship, Mapping) and str(survivorship.get("status", "PASS")).upper() == "LEAKAGE_BLOCKED" and scope == "CANDIDATE":
            quality_reasons.append("SURVIVORSHIP_LEAKAGE_BLOCKED")
        candidate_scores = availability_manifest.get("candidate_scores")
        if scope == "CANDIDATE" and isinstance(candidate_scores, Mapping) and str(candidate_scores.get("status", "FULL")).upper() not in {"FULL", "PARTIAL"}:
            quality_reasons.append("CANDIDATE_REPLAY_UNAVAILABLE")
    quality_gate = {
        "quality_status": str(quality_status).upper(),
        "leakage_status": str(leakage_status).upper(),
        "replay_mode": replay_mode,
        "replay_capability": replay_capability or "FULL",
        "censored_sample": is_censored,
        "challenger_expands_sample": challenger_expands,
        "expanded_range_case_count": expanded_range_case_count,
        "quality_reasons": sorted(set(quality_reasons)),
        "availability_manifest_hash": availability_manifest.get("data_hash") if availability_manifest else None,
    }
    sample_counts = {
        "case_count": len(rows),
        "trade_date_count": len(dates),
        "fold_count": len(fold_records),
        "tested_fold_count": sum(1 for fold in fold_records if fold.get("test") is not None),
        "train_case_count": len(all_train_rows),
        "validation_case_count": len(all_validation_rows),
        "test_case_count": len(all_test_rows),
        "train_trade_date_count": len({_date_value(row.get("trade_date")) for row in all_train_rows}),
        "validation_trade_date_count": len({_date_value(row.get("trade_date")) for row in all_validation_rows}),
        "test_trade_date_count": len({_date_value(row.get("trade_date")) for row in all_test_rows}),
    }
    fold_directions = [bool(item.get("direction")) for item in fold_records]
    final_fold_directions = [bool(item.get("final_challenger_direction")) for item in fold_records]
    recommendation = recommend_calibration(
        baseline=baseline_train,
        challenger=challenger_train,
        train={"baseline": baseline_train, "challenger": challenger_train},
        validation={"baseline": baseline_validation, "challenger": challenger_validation},
        test={"baseline": baseline_test, "challenger": challenger_test},
        robustness=robustness,
        sample_counts=sample_counts,
        quality_status=quality_status if len(dates) >= 252 else "DIAGNOSTIC_ONLY",
        leakage_status=leakage_status,
        fold_directions=final_fold_directions,
        target_parameter=path,
        censored_sample=is_censored,
        challenger_expands_sample=challenger_expands,
        quality_reasons=quality_reasons,
        replay_capability=replay_capability,
    )
    if len(dates) < 252:
        recommendation = "INSUFFICIENT_EVIDENCE"
    return {
        "calibration_version": CALIBRATION_ENGINE_VERSION,
        "target_parameter": target_parameter,
        "normalised_target_parameter": path,
        "parameter_field": field,
        "experiment": "FACTOR_ABLATION" if _parameter_kind(path) == "FACTOR_ABLATION" else "WEIGHT_PERTURBATION" if _parameter_kind(path) == "WEIGHT_PERTURBATION" else "REGIME_THRESHOLD" if _parameter_kind(path) == "MARKET_REGIME_THRESHOLD" else "THRESHOLD_SENSITIVITY",
        "current_value": current,
        "challenger_value": challenger_value,
        "parameter_grid": grid,
        "baseline": {"train": baseline_train, "validation": baseline_validation, "test": baseline_test},
        "challenger": {"train": challenger_train, "validation": challenger_validation, "test": challenger_test},
        "train": {"baseline": baseline_train, "challenger": challenger_train},
        "validation": {"baseline": baseline_validation, "challenger": challenger_validation},
        "test": {"baseline": baseline_test, "challenger": challenger_test},
        "robustness": robustness,
        "folds": fold_records,
        "fold_directions": fold_directions,
        "final_fold_directions": final_fold_directions,
        "quality_gate": quality_gate,
        "sample_counts": sample_counts,
        "recommendation": recommendation,
        "selection_rule": "EVERY_FOLD_TRAIN_SELECTS; EVERY_FOLD_VALIDATION_EVALUATES; AGGREGATE_VALIDATION_EVIDENCE; FIXED_CHALLENGER; FOLD_TEST_READ_AFTER_SELECTION",
        "no_auto_apply": True,
        "known_limitations": [
            "Calibration is evidence for human review, not an optimizer or production mutation.",
            "Walk-forward train rows overlap by design; fold-level direction and date counts remain explicit.",
            "Local fold challengers are selected from train windows; final challenger validation directions are reported separately.",
        ],
    }


__all__ = [
    "parameter_field",
    "current_parameter_value",
    "build_parameter_grid",
    "renormalize_available_weights",
    "one_factor_weight_perturbations",
    "assess_robustness",
    "recommend_calibration",
    "production_gate_predicate",
    "apply_parameter_variant",
    "evaluate_threshold_variants",
    "build_calibration_evidence",
]
