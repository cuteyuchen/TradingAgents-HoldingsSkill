"""Centralized Phase E calculation versions and deterministic constants."""
from __future__ import annotations

from ..decision_contract import SECTOR_THEME_ETF_HARD_CAP_RATIO, STOCK_HARD_CAP_RATIO

PORTFOLIO_ENGINE_VERSION = "portfolio-engine-v1"
PORTFOLIO_RISK_VERSION = "portfolio-risk-v1"
PORTFOLIO_DIFF_VERSION = "portfolio-diff-v1"
PORTFOLIO_GATE_VERSION = "portfolio-decision-gate-v1"

# Keep Score is diagnostic only. It never overrides a deterministic constraint.
KEEP_SCORE_WEIGHTS = {
    "trend_health": 0.25,
    "relative_strength": 0.20,
    "risk_quality": 0.20,
    "diversification_contribution": 0.20,
}

# T+1 availability describes whether an existing position can be executed
# today.  It is deliberately not a long-horizon security-quality signal.
KEEP_SCORE_MIN_AVAILABLE_WEIGHT = 0.60

assert 0 < KEEP_SCORE_MIN_AVAILABLE_WEIGHT <= sum(KEEP_SCORE_WEIGHTS.values()) <= 1.0

__all__ = [
    "KEEP_SCORE_WEIGHTS",
    "KEEP_SCORE_MIN_AVAILABLE_WEIGHT",
    "PORTFOLIO_DIFF_VERSION",
    "PORTFOLIO_ENGINE_VERSION",
    "PORTFOLIO_GATE_VERSION",
    "PORTFOLIO_RISK_VERSION",
    "SECTOR_THEME_ETF_HARD_CAP_RATIO",
    "STOCK_HARD_CAP_RATIO",
]
