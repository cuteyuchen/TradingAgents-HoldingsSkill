"""Versioned, deterministic Candidate Engine configuration."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class CandidateConfig:
    engine_version: str = "candidate-engine-v1"
    stock_score_version: str = "stock-opportunity-v1"
    etf_score_version: str = "etf-opportunity-v1"
    entry_score_version: str = "entry-score-v1"
    portfolio_fit_version: str = "portfolio-fit-v1"
    decision_edge_version: str = "decision-edge-v1"

    min_listing_trading_days: int = 60
    min_history_bars: int = 60
    stock_prefilter_limit: int = 500
    etf_prefilter_limit: int = 150
    watchlist_max: int = 30
    ready_max: int = 10
    action_max: int = 3
    stock_liquidity_percentile_min: float = 20.0
    etf_liquidity_percentile_min: float = 20.0
    etf_min_median_amount: float = 1_000_000.0
    percentile_min_samples: int = 50
    percentile_winsorize_low: float = 0.01
    percentile_winsorize_high: float = 0.99

    watchlist_opportunity_min: float = 60.0
    ready_opportunity_min: float = 70.0
    ready_entry_min: float = 60.0
    ready_fit_min: float = 60.0
    ready_coverage_min: float = 0.65
    ready_confidence_min: float = 70.0
    action_opportunity_min: float = 70.0
    action_entry_min: float = 65.0
    action_fit_min: float = 65.0
    action_coverage_min: float = 0.70
    action_confidence_min: float = 75.0
    rr_ready_min: float = 1.3
    rr_action_min: float = 1.5
    min_decision_edge: float = 5.0
    min_holding_edge: float = 3.0
    min_spendable_cash_ratio: float = 0.01
    portfolio_probe_weight: float = 0.05
    replacement_opportunity_edge: float = 8.0
    replacement_keep_score_max: float = 55.0
    replacement_fit_min: float = 65.0
    action_score_weights: dict[str, float] = field(
        default_factory=lambda: {"opportunity": 0.45, "entry": 0.25, "fit": 0.30}
    )
    stock_factor_weights: dict[str, float] = field(
        default_factory=lambda: {
            "trend": 0.20,
            "momentum": 0.15,
            "fundamental": 0.20,
            "valuation": 0.10,
            "flow": 0.15,
            "industry": 0.10,
            "risk": 0.10,
        }
    )
    etf_factor_weights: dict[str, float] = field(
        default_factory=lambda: {
            "underlying_trend": 0.25,
            "relative_strength": 0.20,
            "liquidity": 0.15,
            "valuation": 0.15,
            "constituent_breadth": 0.15,
            "risk": 0.10,
        }
    )
    entry_factor_weights: dict[str, float] = field(
        default_factory=lambda: {
            "trend_alignment": 0.20,
            "extension": 0.25,
            "volume_confirmation": 0.15,
            "risk_reward_structure": 0.30,
            "quote_liquidity": 0.10,
        }
    )
    portfolio_fit_weights: dict[str, float] = field(
        default_factory=lambda: {
            "diversification": 0.35,
            "marginal_risk": 0.30,
            "concentration": 0.20,
            "exposure": 0.15,
        }
    )
    no_action_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "STRONG_RISK_ON": 72.0,
            "RISK_ON": 75.0,
            "NEUTRAL": 78.0,
            "RISK_OFF": 83.0,
            "STRONG_RISK_OFF": 90.0,
        }
    )

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_CONFIG = CandidateConfig()


def _weight_total(weights: dict[str, float]) -> float:
    return round(sum(weights.values()), 10)


assert _weight_total(DEFAULT_CONFIG.stock_factor_weights) == 1.0
assert _weight_total(DEFAULT_CONFIG.etf_factor_weights) == 1.0
assert _weight_total(DEFAULT_CONFIG.entry_factor_weights) == 1.0
assert _weight_total(DEFAULT_CONFIG.portfolio_fit_weights) == 1.0
assert _weight_total(DEFAULT_CONFIG.action_score_weights) == 1.0


CANDIDATE_ENGINE_VERSION = DEFAULT_CONFIG.engine_version
STOCK_SCORE_VERSION = DEFAULT_CONFIG.stock_score_version
ETF_SCORE_VERSION = DEFAULT_CONFIG.etf_score_version
ENTRY_SCORE_VERSION = DEFAULT_CONFIG.entry_score_version
PORTFOLIO_FIT_VERSION = DEFAULT_CONFIG.portfolio_fit_version
DECISION_EDGE_VERSION = DEFAULT_CONFIG.decision_edge_version
STOCK_FACTOR_WEIGHTS = {key: int(value * 100) for key, value in DEFAULT_CONFIG.stock_factor_weights.items()}
ETF_FACTOR_WEIGHTS = {key: int(value * 100) for key, value in DEFAULT_CONFIG.etf_factor_weights.items()}


__all__ = [
    "CandidateConfig",
    "DEFAULT_CONFIG",
    "CANDIDATE_ENGINE_VERSION",
    "STOCK_SCORE_VERSION",
    "ETF_SCORE_VERSION",
    "ENTRY_SCORE_VERSION",
    "PORTFOLIO_FIT_VERSION",
    "DECISION_EDGE_VERSION",
    "STOCK_FACTOR_WEIGHTS",
    "ETF_FACTOR_WEIGHTS",
]
