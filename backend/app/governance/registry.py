"""Stable business parameter registry for Phase J governance.

Registry keys are the business contract.  They intentionally do not expose
Python module paths and are the only supported way to read or change a governed
production parameter.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from ..candidates.config import DEFAULT_CONFIG as CANDIDATE_DEFAULT
from ..decision_contract import (
    CONTRACT_VERSION,
    SECTOR_THEME_ETF_HARD_CAP_RATIO,
    STOCK_HARD_CAP_RATIO,
    decision_contract_payload,
)
from ..market.engine import config as market_config
from ..portfolio.config import (
    KEEP_SCORE_WEIGHTS,
    PORTFOLIO_DIFF_VERSION,
    PORTFOLIO_ENGINE_VERSION,
    PORTFOLIO_GATE_VERSION,
    PORTFOLIO_RISK_VERSION,
)

CALIBRATABLE = "CALIBRATABLE"
PROTECTED = "PROTECTED"
OPERATIONAL = "OPERATIONAL"
EXTERNAL = "EXTERNAL"
DERIVED = "DERIVED"

REGIME_ORDER = tuple(market_config.REGIME_ORDER)
NO_ACTION_REGIME_ORDER = ("STRONG_RISK_ON", "RISK_ON", "NEUTRAL", "RISK_OFF", "STRONG_RISK_OFF")


@dataclass(frozen=True)
class ParameterSpec:
    key: str
    display_name: str
    domain: str
    classification: str
    value_type: str
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: tuple[Any, ...] | None = None
    calibration_supported: bool = False
    requires_calibration_report: bool = False
    protected: bool = False
    restart_required: bool = False
    runtime_contract_relevant: bool = False
    description: str = ""


def _spec(
    key: str,
    *,
    display_name: str,
    domain: str,
    classification: str,
    value_type: str = "float",
    min_value: float | None = None,
    max_value: float | None = None,
    allowed_values: tuple[Any, ...] | None = None,
    calibration_supported: bool = False,
    requires_calibration_report: bool = False,
    protected: bool = False,
    restart_required: bool = False,
    runtime_contract_relevant: bool = False,
    description: str = "",
) -> ParameterSpec:
    return ParameterSpec(
        key=key,
        display_name=display_name,
        domain=domain,
        classification=classification,
        value_type=value_type,
        min_value=min_value,
        max_value=max_value,
        allowed_values=allowed_values,
        calibration_supported=calibration_supported,
        requires_calibration_report=requires_calibration_report,
        protected=protected,
        restart_required=restart_required,
        runtime_contract_relevant=runtime_contract_relevant,
        description=description,
    )


def _build_registry() -> dict[str, ParameterSpec]:
    specs: dict[str, ParameterSpec] = {}

    def add(
        key: str,
        *,
        display_name: str,
        domain: str,
        classification: str,
        value_type: str = "float",
        min_value: float | None = None,
        max_value: float | None = None,
        calibration_supported: bool = False,
        protected: bool = False,
        description: str = "",
    ) -> None:
        specs[key] = _spec(
            key,
            display_name=display_name,
            domain=domain,
            classification=classification,
            value_type=value_type,
            min_value=min_value,
            max_value=max_value,
            calibration_supported=calibration_supported,
            requires_calibration_report=calibration_supported,
            protected=protected,
            runtime_contract_relevant=classification == CALIBRATABLE,
            description=description,
        )

    for index, regime in enumerate(REGIME_ORDER):
        add(
            f"market.regime_lower_bounds.{regime}",
            display_name=f"{regime} lower bound",
            domain="market",
            classification=PROTECTED if regime == "STRONG_RISK_OFF" else CALIBRATABLE,
            min_value=0.0,
            max_value=100.0,
            calibration_supported=regime != "STRONG_RISK_OFF",
            protected=regime == "STRONG_RISK_OFF",
            description="Market score lower bound that assigns the regime.",
        )
        for direction in ("up", "down"):
            if direction not in market_config.REGIME_HYSTERESIS[regime]:
                continue
            add(
                f"market.regime_hysteresis.{regime}.{direction}",
                display_name=f"{regime} {direction} hysteresis",
                domain="market",
                classification=CALIBRATABLE,
                min_value=0.0,
                max_value=100.0,
                calibration_supported=True,
                description="Hysteresis exit threshold for the regime transition.",
            )

    add(
        "candidate.watchlist_opportunity_min",
        display_name="Watchlist opportunity minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.ready_opportunity_min",
        display_name="Ready opportunity minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.ready_entry_min",
        display_name="Ready entry minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.ready_fit_min",
        display_name="Ready fit minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.ready_coverage_min",
        display_name="Ready coverage minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=1.0,
        calibration_supported=True,
    )
    add(
        "candidate.ready_confidence_min",
        display_name="Ready confidence minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.action_opportunity_min",
        display_name="Action opportunity minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.action_entry_min",
        display_name="Action entry minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.action_fit_min",
        display_name="Action fit minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.action_coverage_min",
        display_name="Action coverage minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=1.0,
        calibration_supported=True,
    )
    add(
        "candidate.action_confidence_min",
        display_name="Action confidence minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.rr_ready_min",
        display_name="Ready risk/reward minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=20.0,
        calibration_supported=True,
    )
    add(
        "candidate.rr_action_min",
        display_name="Action risk/reward minimum",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=20.0,
        calibration_supported=True,
    )
    add(
        "candidate.min_decision_edge",
        display_name="Minimum decision edge",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )
    add(
        "candidate.min_holding_edge",
        display_name="Minimum holding edge",
        domain="candidate",
        classification=CALIBRATABLE,
        min_value=0.0,
        max_value=100.0,
        calibration_supported=True,
    )

    for regime in NO_ACTION_REGIME_ORDER:
        add(
            f"decision.no_action_thresholds.{regime}",
            display_name=f"No-action {regime} threshold",
            domain="decision",
            classification=CALIBRATABLE,
            min_value=0.0,
            max_value=100.0,
            calibration_supported=True,
        )

    for key, current, display_name, value_type in (
        ("candidate.watchlist_max", 30, "Watchlist maximum", int),
        ("candidate.ready_max", 10, "Ready maximum", int),
        ("candidate.action_max", 3, "Action maximum", int),
        ("portfolio.hard_caps.stock", STOCK_HARD_CAP_RATIO, "Stock hard cap", float),
        ("portfolio.hard_caps.sector_theme_etf", SECTOR_THEME_ETF_HARD_CAP_RATIO, "Sector/theme ETF hard cap", float),
        ("decision.candidate_max", 3, "Decision candidate maximum", int),
    ):
        specs[key] = _spec(
            key,
            display_name=display_name,
            domain=key.split(".", 1)[0],
            classification=PROTECTED,
            value_type="int" if value_type is int else "float",
            min_value=0.0,
            max_value=1000.0,
            calibration_supported=False,
            protected=True,
            runtime_contract_relevant=True,
            description="Protected invariant enforced by the decision contract.",
        )

    for key, _default, display_name in (
        ("decision.new_candidate_exclude_holdings", True, "Exclude held securities from new candidates"),
        ("decision.data_quality_fail_close", True, "Data quality fail-close"),
        ("decision.no_auto_trade", True, "No automatic trade execution"),
    ):
        specs[key] = _spec(
            key,
            display_name=display_name,
            domain="decision",
            classification=PROTECTED,
            value_type="bool",
            calibration_supported=False,
            protected=True,
            runtime_contract_relevant=True,
            description="Protected invariant enforced by the decision contract.",
        )

    for key, display_name, _value_type in (
        ("candidate.action_score_weights", "Action score weights", dict),
        ("candidate.stock_factor_weights", "Stock factor weights", dict),
        ("candidate.etf_factor_weights", "ETF factor weights", dict),
        ("candidate.entry_factor_weights", "Entry factor weights", dict),
        ("candidate.portfolio_fit_weights", "Portfolio fit weights", dict),
        ("portfolio.keep_score_weights", "Keep score weights", dict),
    ):
        specs[key] = _spec(
            key,
            display_name=display_name,
            domain=key.split(".", 1)[0],
            classification=PROTECTED,
            value_type="dict",
            calibration_supported=False,
            protected=True,
            runtime_contract_relevant=True,
            description="Protected weight family; research factor ablation is diagnostic only.",
        )

    return specs


REGISTRY = _build_registry()


def default_production_config() -> dict[str, Any]:
    """Return the exact JSON-safe production parameter snapshot before governance."""

    return {
        "candidate": CANDIDATE_DEFAULT.as_dict(),
        "market": {
            "engine_version": market_config.MARKET_ENGINE_VERSION,
            "universe_rule_version": market_config.UNIVERSE_RULE_VERSION,
            "score_config_version": market_config.SCORE_CONFIG_VERSION,
            "component_weights": dict(market_config.COMPONENT_WEIGHTS),
            "regime_lower_bounds": dict(market_config.REGIME_LOWER_BOUNDS),
            "regime_hysteresis": {
                key: dict(value) for key, value in market_config.REGIME_HYSTERESIS.items()
            },
        },
        "portfolio": {
            "engine_version": PORTFOLIO_ENGINE_VERSION,
            "risk_version": PORTFOLIO_RISK_VERSION,
            "diff_version": PORTFOLIO_DIFF_VERSION,
            "gate_version": PORTFOLIO_GATE_VERSION,
            "keep_score_weights": dict(KEEP_SCORE_WEIGHTS),
            "hard_caps": {
                "stock": STOCK_HARD_CAP_RATIO,
                "sector_theme_etf": SECTOR_THEME_ETF_HARD_CAP_RATIO,
            },
        },
        "decision": {
            "candidate_max": 3,
            "new_candidate_exclude_holdings": True,
            "data_quality_fail_close": True,
            "no_auto_trade": True,
            "no_action_thresholds": dict(CANDIDATE_DEFAULT.no_action_thresholds),
        },
        "runtime_contract_version": CONTRACT_VERSION,
        "decision_contract_version": CONTRACT_VERSION,
        "decision_contract": decision_contract_payload(),
    }


def _merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def normalize_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a complete normalized snapshot by merging over code defaults."""

    merged = copy.deepcopy(default_production_config())
    if isinstance(snapshot, Mapping):
        _merge(merged, snapshot)
    return merged


def get_path(snapshot: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    node: Any = snapshot
    for part in str(key).split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def set_path(snapshot: Mapping[str, Any] | None, key: str, value: Any) -> dict[str, Any]:
    result = copy.deepcopy(dict(snapshot or {}))
    node: Any = result
    parts = str(key).split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value
    return result


def read_current_value(snapshot: Mapping[str, Any] | None, key: str) -> Any:
    value = get_path(snapshot, key, _MISSING)
    if value is _MISSING:
        raise KeyError(f"unknown_parameter:{key}")
    return value


_MISSING = object()


def canonical_config_hash(snapshot: Mapping[str, Any] | None) -> str:
    """Stable canonical SHA-256 for logically identical snapshots."""

    normalized = normalize_snapshot(snapshot)
    payload = json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_spec(key: str) -> ParameterSpec:
    if key not in REGISTRY:
        raise ValueError(f"unknown_parameter:{key}")
    return REGISTRY[key]


def validate_registry_value(key: str, value: Any) -> Any:
    spec = get_spec(key)
    if spec.value_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"invalid_parameter_type:{key}")
        number = float(value)
        if spec.min_value is not None and number < spec.min_value:
            raise ValueError(f"parameter_below_min:{key}")
        if spec.max_value is not None and number > spec.max_value:
            raise ValueError(f"parameter_above_max:{key}")
        return number
    if spec.value_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"invalid_parameter_type:{key}")
        if spec.min_value is not None and value < int(spec.min_value):
            raise ValueError(f"parameter_below_min:{key}")
        if spec.max_value is not None and value > int(spec.max_value):
            raise ValueError(f"parameter_above_max:{key}")
        return value
    if spec.value_type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"invalid_parameter_type:{key}")
        return value
    if spec.value_type == "dict":
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid_parameter_type:{key}")
        return dict(value)
    if spec.value_type == "str":
        if not isinstance(value, str):
            raise ValueError(f"invalid_parameter_type:{key}")
        return value
    raise ValueError(f"unsupported_parameter_type:{spec.value_type}")


def is_protected(key: str) -> bool:
    return get_spec(key).protected


def candidate_config_from_snapshot(snapshot: Mapping[str, Any] | None):
    from dataclasses import replace

    from ..candidates.config import CandidateConfig

    values = get_path(snapshot, "candidate")
    allowed = {name for name in CandidateConfig.__dataclass_fields__}
    config = CandidateConfig(**{key: value for key, value in (values or {}).items() if key in allowed})
    no_action_thresholds = get_path(snapshot, "decision.no_action_thresholds")
    if isinstance(no_action_thresholds, Mapping):
        config = replace(config, no_action_thresholds=dict(no_action_thresholds))
    return config


def market_regime_settings(snapshot: Mapping[str, Any] | None) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    market = get_path(snapshot, "market", {}) or {}
    lower_bounds = dict(market.get("regime_lower_bounds") or market_config.REGIME_LOWER_BOUNDS)
    raw_hysteresis = market.get("regime_hysteresis") or market_config.REGIME_HYSTERESIS
    hysteresis = {
        str(regime): {
            str(direction): float(value)
            for direction, value in (dict(item) if isinstance(item, Mapping) else {}).items()
        }
        for regime, item in raw_hysteresis.items()
    }
    return lower_bounds, hysteresis


__all__ = [
    "CALIBRATABLE",
    "DERIVED",
    "EXTERNAL",
    "OPERATIONAL",
    "PROTECTED",
    "REGIME_ORDER",
    "REGISTRY",
    "ParameterSpec",
    "canonical_config_hash",
    "candidate_config_from_snapshot",
    "default_production_config",
    "get_path",
    "get_spec",
    "is_protected",
    "market_regime_settings",
    "normalize_snapshot",
    "read_current_value",
    "set_path",
    "validate_registry_value",
]
