"""Phase M deterministic recompute contracts."""

from __future__ import annotations

from datetime import time
from enum import StrEnum

RECOMPUTE_ENGINE_VERSION = "deterministic-recompute-v1"
RECOMPUTE_SCHEMA_VERSION = "phase-m-recompute-v1"
UNIVERSE_VERSION = "pit-universe-v1"

# EOD deep historical recompute observes the 15:10 Shanghai checkpoint. The
# stored cutoff is the equivalent UTC-naive instant so persisted facts are
# compared without timezone ambiguity.
EOD_DECISION_TIME = time(15, 10)
INTRADAY_SUPPORTED = False

# Percentile components use the production 3-year lookback as the default
# warmup horizon; the engine degrades to PARTIAL when warmup is incomplete.
MARKET_HISTORY_LOOKBACK_TRADING_DAYS = 750
CANDIDATE_HISTORY_LOOKBACK_TRADING_DAYS = 130
CORRELATION_LOOKBACK_TRADING_DAYS = 62


class RecomputeCapability(StrEnum):
    FULL_PIT_EQUIVALENT = "FULL_PIT_EQUIVALENT"
    PARTIAL_PIT_RECOMPUTE = "PARTIAL_PIT_RECOMPUTE"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    DATA_GAP = "DATA_GAP"
    LEAKAGE_BLOCKED = "LEAKAGE_BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


class RecomputeScope(StrEnum):
    MARKET = "MARKET"
    CANDIDATE = "CANDIDATE"
    CANDIDATE_STOCK = "CANDIDATE_STOCK"
    CANDIDATE_ETF = "CANDIDATE_ETF"
    PORTFOLIO_DECISION = "PORTFOLIO_DECISION"


class RecomputeStage(StrEnum):
    INPUT_AUDIT = "INPUT_AUDIT"
    MATERIALIZE_PIT = "MATERIALIZE_PIT"
    MARKET_RECOMPUTE = "MARKET_RECOMPUTE"
    CANDIDATE_RECOMPUTE = "CANDIDATE_RECOMPUTE"
    PORTFOLIO_RECOMPUTE = "PORTFOLIO_RECOMPUTE"
    FINALIZING = "FINALIZING"


MARKET_REQUIRED_INPUTS = (
    "historical_security_state",
    "historical_trading_status",
    "historical_st_state",
    "daily_bars",
    "price_basis",
)

CANDIDATE_STOCK_REQUIRED_INPUTS = (
    "historical_security_state",
    "historical_trading_status",
    "historical_st_state",
    "historical_valuation",
    "fundamental_publication",
    "daily_bars",
    "price_basis",
)

CANDIDATE_ETF_REQUIRED_INPUTS = (
    "historical_security_state",
    "historical_trading_status",
    "historical_st_state",
    "historical_valuation",
    "etf_metadata",
    "daily_bars",
    "price_basis",
)

CANDIDATE_REQUIRED_INPUTS = CANDIDATE_STOCK_REQUIRED_INPUTS

PORTFOLIO_DECISION_REQUIRED_INPUTS = (
    "historical_security_state",
    "historical_trading_status",
    "historical_st_state",
)

KNOWN_MISSING_FACTORS = {
    "CANDIDATE": ("flow", "industry", "etf_constituent_breadth"),
    "CANDIDATE_STOCK": ("flow", "industry"),
    "CANDIDATE_ETF": ("etf_constituent_breadth", "flow", "industry"),
}


__all__ = [
    "CANDIDATE_ETF_REQUIRED_INPUTS",
    "CANDIDATE_HISTORY_LOOKBACK_TRADING_DAYS",
    "CANDIDATE_REQUIRED_INPUTS",
    "CANDIDATE_STOCK_REQUIRED_INPUTS",
    "CORRELATION_LOOKBACK_TRADING_DAYS",
    "EOD_DECISION_TIME",
    "INTRADAY_SUPPORTED",
    "MARKET_HISTORY_LOOKBACK_TRADING_DAYS",
    "MARKET_REQUIRED_INPUTS",
    "PORTFOLIO_DECISION_REQUIRED_INPUTS",
    "RECOMPUTE_ENGINE_VERSION",
    "RECOMPUTE_SCHEMA_VERSION",
    "RecomputeCapability",
    "RecomputeScope",
    "RecomputeStage",
    "UNIVERSE_VERSION",
    "KNOWN_MISSING_FACTORS",
]
