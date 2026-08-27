"""Versioned, server-owned Phase I research configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

BACKTEST_ENGINE_VERSION = "historical-replay-v1"
METRICS_ENGINE_VERSION = "backtest-metrics-v1"
CALIBRATION_ENGINE_VERSION = "calibration-v1"
RESEARCH_SCHEMA_VERSION = "phase-i-research-v1"

REPLAY_MODES = (
    "PRODUCTION_REPLAY",
    "DETERMINISTIC_RECOMPUTE",
    "BAR_ONLY_DIAGNOSTIC",
)
RESEARCH_SCOPES = (
    "MARKET",
    "CANDIDATE",
    "PORTFOLIO_DECISION",
    "MEMORY_DECISION",
    "BAR_FACTOR",
)
BACKTEST_STATUSES = (
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "INSUFFICIENT_DATA",
    "INVALIDATED",
)
REPLAY_AVAILABILITY_STATUSES = (
    "FULL",
    "PARTIAL",
    "DIAGNOSTIC_ONLY",
    "UNSUPPORTED",
    "DATA_GAP",
    "LEAKAGE_BLOCKED",
)
CALIBRATION_RECOMMENDATIONS = (
    "KEEP_CURRENT",
    "CONSIDER_CHANGE",
    "INSUFFICIENT_EVIDENCE",
    "REJECT_CHANGE",
)
CALIBRATION_EXPERIMENTS = (
    "THRESHOLD_SENSITIVITY",
    "FACTOR_ABLATION",
    "WEIGHT_PERTURBATION",
    "REGIME_THRESHOLD",
    "DECISION_EDGE",
    "ENTRY_THRESHOLD",
    "RR_THRESHOLD",
)
DEFAULT_HORIZONS = (1, 5, 10, 20, 60, 120)
MARKET_HORIZONS = (1, 5, 10, 20, 60)
MIN_CALIBRATION_CASES = 20
MIN_CALIBRATION_TRADE_DATES = 10
MAX_GRID_SIZE = 200
MAX_BOOTSTRAP_ITERATIONS = 2_000
MAX_DATE_SPAN_DAYS = 3_660


class ReplayMode(StrEnum):
    PRODUCTION_REPLAY = "PRODUCTION_REPLAY"
    DETERMINISTIC_RECOMPUTE = "DETERMINISTIC_RECOMPUTE"
    BAR_ONLY_DIAGNOSTIC = "BAR_ONLY_DIAGNOSTIC"


class ResearchScope(StrEnum):
    MARKET = "MARKET"
    CANDIDATE = "CANDIDATE"
    PORTFOLIO_DECISION = "PORTFOLIO_DECISION"
    MEMORY_DECISION = "MEMORY_DECISION"
    BAR_FACTOR = "BAR_FACTOR"


@dataclass(frozen=True)
class TransactionCostModel:
    """Conservative, explicit cost assumptions used for simulated outcomes."""

    buy_fee_rate: float = 0.0003
    sell_fee_rate: float = 0.0003
    sell_tax_rate: float = 0.0005
    slippage_bps: float = 5.0
    model_version: str = "simple-cny-cost-v1"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_TRANSACTION_COST_MODEL = TransactionCostModel()


def normalise_replay_mode(value: str | ReplayMode) -> str:
    mode = str(value).strip().upper()
    if mode not in REPLAY_MODES:
        raise ValueError(f"unsupported_replay_mode:{mode}")
    return mode


def normalise_scope(value: str | ResearchScope) -> str:
    scope = str(value).strip().upper()
    if scope not in RESEARCH_SCOPES:
        raise ValueError(f"unsupported_research_scope:{scope}")
    return scope


def validate_horizons(values: list[int] | tuple[int, ...] | None, *, market: bool = False) -> tuple[int, ...]:
    horizons = tuple(sorted(set(values or (MARKET_HORIZONS if market else DEFAULT_HORIZONS))))
    allowed = set(MARKET_HORIZONS if market else DEFAULT_HORIZONS)
    if not horizons or any(item <= 0 for item in horizons):
        raise ValueError("horizons_must_be_positive")
    if any(item not in allowed for item in horizons):
        raise ValueError(f"unsupported_horizon:{sorted(set(horizons) - allowed)}")
    return horizons


def current_production_config() -> dict[str, Any]:
    """Return a JSON-safe snapshot without changing any live configuration."""

    from ..candidates.config import DEFAULT_CONFIG as candidate_config
    from ..decision_contract import CONTRACT_VERSION
    from ..market.engine import config as market_config
    from ..portfolio.config import (
        KEEP_SCORE_WEIGHTS,
        PORTFOLIO_DIFF_VERSION,
        PORTFOLIO_ENGINE_VERSION,
        PORTFOLIO_GATE_VERSION,
        PORTFOLIO_RISK_VERSION,
    )

    return {
        "candidate": candidate_config.as_dict(),
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
        },
        "runtime_contract_version": CONTRACT_VERSION,
        "decision_contract_version": CONTRACT_VERSION,
    }


def current_config_version() -> str:
    config = current_production_config()
    return str(config["candidate"]["engine_version"])


__all__ = [
    "BACKTEST_ENGINE_VERSION",
    "METRICS_ENGINE_VERSION",
    "CALIBRATION_ENGINE_VERSION",
    "RESEARCH_SCHEMA_VERSION",
    "REPLAY_MODES",
    "RESEARCH_SCOPES",
    "BACKTEST_STATUSES",
    "REPLAY_AVAILABILITY_STATUSES",
    "CALIBRATION_RECOMMENDATIONS",
    "CALIBRATION_EXPERIMENTS",
    "DEFAULT_HORIZONS",
    "MARKET_HORIZONS",
    "MIN_CALIBRATION_CASES",
    "MIN_CALIBRATION_TRADE_DATES",
    "MAX_GRID_SIZE",
    "MAX_BOOTSTRAP_ITERATIONS",
    "MAX_DATE_SPAN_DAYS",
    "ReplayMode",
    "ResearchScope",
    "TransactionCostModel",
    "DEFAULT_TRANSACTION_COST_MODEL",
    "normalise_replay_mode",
    "normalise_scope",
    "validate_horizons",
    "current_production_config",
    "current_config_version",
]
