"""Deterministic pre-activation validation for immutable parameter snapshots."""

from __future__ import annotations

from typing import Any, Mapping

from ..decision_contract import CONTRACT_VERSION
from .registry import (
    REGIME_ORDER,
    REGISTRY,
    default_production_config,
    get_path,
    normalize_snapshot,
    read_current_value,
    validate_registry_value,
)


def _check(checks: list[dict[str, Any]], *, code: str, status: str, message: str) -> None:
    checks.append({"code": code, "status": status, "message": message})


def validate_parameter_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return PASS/WARNING/BLOCKED after deterministic cross-parameter checks."""

    config = normalize_snapshot(snapshot)
    checks: list[dict[str, Any]] = []

    for key, spec in REGISTRY.items():
        try:
            validate_registry_value(key, read_current_value(config, key))
            _check(checks, code="REGISTRY_TYPE_RANGE", status="PASS", message=f"{key} type/range valid")
        except Exception as exc:  # noqa: BLE001
            _check(checks, code="REGISTRY_TYPE_RANGE", status="BLOCKED", message=str(exc))

    defaults = default_production_config()
    for key in REGISTRY:
        if not REGISTRY[key].protected:
            continue
        current = read_current_value(config, key)
        expected = read_current_value(defaults, key)
        if current != expected:
            _check(
                checks,
                code="PROTECTED_INVARIANT",
                status="BLOCKED",
                message=f"protected parameter changed: {key}",
            )

    candidate = config.get("candidate") or {}

    def number(name: str) -> float:
        return float(candidate.get(name) or 0.0)

    for low_name, high_name in (
        ("watchlist_opportunity_min", "ready_opportunity_min"),
        ("ready_opportunity_min", "action_opportunity_min"),
        ("ready_entry_min", "action_entry_min"),
        ("ready_fit_min", "action_fit_min"),
        ("ready_coverage_min", "action_coverage_min"),
        ("ready_confidence_min", "action_confidence_min"),
        ("rr_ready_min", "rr_action_min"),
    ):
        low = number(low_name)
        high = number(high_name)
        if low > high:
            _check(
                checks,
                code="CANDIDATE_GATE_ORDER",
                status="BLOCKED",
                message=f"candidate.{low_name} must be <= candidate.{high_name}",
            )
        else:
            _check(checks, code="CANDIDATE_GATE_ORDER", status="PASS", message=f"{low_name} <= {high_name}")

    lower_bounds = {key: float(value) for key, value in (get_path(config, "market.regime_lower_bounds") or {}).items()}
    hysteresis = {
        key: dict(value) if isinstance(value, Mapping) else {}
        for key, value in (get_path(config, "market.regime_hysteresis") or {}).items()
    }

    missing_bounds = [regime for regime in REGIME_ORDER if regime not in lower_bounds]
    if missing_bounds:
        _check(checks, code="MARKET_BOUNDARY_ORDER", status="BLOCKED", message="missing lower bounds")
    else:
        ordered_values = [lower_bounds[regime] for regime in REGIME_ORDER]
        if ordered_values != sorted(ordered_values):
            _check(checks, code="MARKET_BOUNDARY_ORDER", status="BLOCKED", message="regime lower bounds must increase")
        else:
            _check(checks, code="MARKET_BOUNDARY_ORDER", status="PASS", message="regime lower bounds ordered")

    for index, regime in enumerate(REGIME_ORDER):
        pair = hysteresis.get(regime) or {}
        lower = lower_bounds.get(regime)
        if lower is None:
            continue
        up = pair.get("up")
        down = pair.get("down")
        if up is not None:
            if float(up) <= float(lower):
                _check(checks, code="MARKET_HYSTERESIS", status="BLOCKED", message=f"{regime} up must exceed its lower bound")
            elif index + 1 < len(REGIME_ORDER) and float(up) < lower_bounds[REGIME_ORDER[index + 1]]:
                _check(checks, code="MARKET_HYSTERESIS", status="BLOCKED", message=f"{regime} up cannot cross below next regime")
            else:
                _check(checks, code="MARKET_HYSTERESIS", status="PASS", message=f"{regime} up hysteresis valid")
        if down is not None:
            if float(down) >= float(lower):
                _check(checks, code="MARKET_HYSTERESIS", status="BLOCKED", message=f"{regime} down must stay below its lower bound")
            elif index > 0 and float(down) < lower_bounds[REGIME_ORDER[index - 1]]:
                _check(checks, code="MARKET_HYSTERESIS", status="BLOCKED", message=f"{regime} down cannot cross below previous regime")
            else:
                _check(checks, code="MARKET_HYSTERESIS", status="PASS", message=f"{regime} down hysteresis valid")
        if up is not None and down is not None and float(up) <= float(down):
            _check(checks, code="MARKET_HYSTERESIS", status="BLOCKED", message=f"{regime} up must exceed down")

    no_action_order = ("STRONG_RISK_ON", "RISK_ON", "NEUTRAL", "RISK_OFF", "STRONG_RISK_OFF")
    thresholds = get_path(config, "decision.no_action_thresholds") or {}
    ordered_thresholds = [float(thresholds[regime]) for regime in no_action_order if regime in thresholds]
    if len(ordered_thresholds) == len(no_action_order) and ordered_thresholds != sorted(ordered_thresholds):
        _check(checks, code="NO_ACTION_ORDER", status="BLOCKED", message="no-action thresholds must increase with risk aversion")
    elif len(ordered_thresholds) != len(no_action_order):
        _check(checks, code="NO_ACTION_ORDER", status="BLOCKED", message="missing no-action thresholds")
    else:
        _check(checks, code="NO_ACTION_ORDER", status="PASS", message="no-action thresholds ordered")

    if str(get_path(config, "runtime_contract_version")) != CONTRACT_VERSION:
        _check(checks, code="CONTRACT_VERSION", status="BLOCKED", message="runtime contract version mismatch")
    else:
        _check(checks, code="CONTRACT_VERSION", status="PASS", message="runtime contract version valid")
    if str(get_path(config, "decision_contract_version")) != CONTRACT_VERSION:
        _check(checks, code="CONTRACT_VERSION", status="BLOCKED", message="decision contract version mismatch")
    else:
        _check(checks, code="CONTRACT_VERSION", status="PASS", message="decision contract version valid")

    blocked = any(item["status"] == "BLOCKED" for item in checks)
    return {
        "status": "BLOCKED" if blocked else "PASS",
        "checks": checks,
        "blocked_count": sum(item["status"] == "BLOCKED" for item in checks),
    }


__all__ = ["validate_parameter_snapshot"]
