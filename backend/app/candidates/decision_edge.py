"""Decision Edge, no-action baseline, and transparent transaction costs."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from ..config import settings
from ..portfolio.ledger import transaction_cost_estimate
from .config import CandidateConfig, DEFAULT_CONFIG


@dataclass(frozen=True)
class TransactionCostModel:
    """Small wrapper around the Phase E transaction-cost calculation."""

    commission_bps: float | None = None
    minimum_commission: float | None = None
    sell_tax_bps: float | None = None

    @classmethod
    def from_settings(cls) -> "TransactionCostModel":
        return cls(
            commission_bps=settings.PORTFOLIO_BROKER_COMMISSION_BPS,
            minimum_commission=settings.PORTFOLIO_MINIMUM_COMMISSION,
            sell_tax_bps=settings.PORTFOLIO_SELL_TAX_BPS,
        )

    def estimate(self, *, notional: float | None, side: str = "BUY") -> float | None:
        return transaction_cost_estimate(
            side=side,
            gross_amount=notional,
            commission_bps=self.commission_bps,
            minimum_commission=self.minimum_commission,
            sell_tax_bps=self.sell_tax_bps,
        )


def calculate_action_score(
    opportunity_score: float | None,
    entry_score: float | None,
    portfolio_fit_score: float | None,
    *,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> float | None:
    if opportunity_score is None or entry_score is None or portfolio_fit_score is None:
        return None
    weights = config.action_score_weights
    return round(
        float(opportunity_score) * weights["opportunity"]
        + float(entry_score) * weights["entry"]
        + float(portfolio_fit_score) * weights["fit"],
        4,
    )


def no_action_threshold(
    regime: str | None,
    *,
    market_quality: str | None = None,
    is_frozen: bool = False,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> float:
    normalized = str(regime or "NEUTRAL").upper()
    threshold = float(config.no_action_thresholds.get(normalized, config.no_action_thresholds["NEUTRAL"]))
    if str(market_quality or "VALID").upper() == "DEGRADED":
        threshold += 5.0
    if is_frozen:
        threshold = 100.0
    return threshold


def held_opportunity_baseline(
    held_scores: list[dict[str, Any]],
    *,
    min_reliable: int = 2,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    reliable = [
        row for row in held_scores
        if isinstance(row, dict)
        and row.get("opportunity_score") is not None
        and float(row.get("confidence") or 0.0) >= config.ready_confidence_min
        and float(row.get("coverage") or row.get("data_coverage") or 0.0) >= config.ready_coverage_min
    ]
    if len(reliable) < min_reliable:
        return {
            "available": False,
            "reason": "HELD_BASELINE_INSUFFICIENT",
            "reliable_count": len(reliable),
            "median_held_opportunity_score": None,
            "weakest_reliable_held_opportunity": None,
            "weakest_reliable_keep_score": None,
        }
    opportunities = [float(row["opportunity_score"]) for row in reliable]
    keep_scores = [float(row["keep_score"]) for row in reliable if row.get("keep_score") is not None]
    return {
        "available": True,
        "reason": None,
        "reliable_count": len(reliable),
        "median_held_opportunity_score": round(float(median(opportunities)), 4),
        "weakest_reliable_held_opportunity": round(min(opportunities), 4),
        "weakest_reliable_keep_score": round(min(keep_scores), 4) if keep_scores else None,
        "rows": reliable,
    }


def calculate_decision_edge(
    *,
    opportunity_score: float | None,
    entry_score: float | None,
    portfolio_fit_score: float | None,
    market_regime: str | None,
    market_quality: str | None,
    market_frozen: bool = False,
    held_baseline: dict[str, Any] | None = None,
    total_assets: float | None = None,
    probe_weight: float | None = None,
    transaction_cost_model: TransactionCostModel | None = None,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    action_score = calculate_action_score(opportunity_score, entry_score, portfolio_fit_score, config=config)
    threshold = no_action_threshold(
        market_regime,
        market_quality=market_quality,
        is_frozen=market_frozen,
        config=config,
    )
    edge_no_action = action_score - threshold if action_score is not None else None
    edge_holdings = None
    if held_baseline and held_baseline.get("available") and opportunity_score is not None:
        edge_holdings = float(opportunity_score) - float(held_baseline["median_held_opportunity_score"])
    raw_edge = min(edge_no_action, edge_holdings) if edge_no_action is not None and edge_holdings is not None else edge_no_action

    model = transaction_cost_model or TransactionCostModel.from_settings()
    probe_notional = float(total_assets) * float(probe_weight) if total_assets is not None and probe_weight is not None else None
    estimated_cost = model.estimate(notional=probe_notional, side="BUY")
    estimated_cost_bps = estimated_cost / probe_notional * 10_000 if estimated_cost is not None and probe_notional and probe_notional > 0 else None
    cost_penalty_points = estimated_cost / total_assets * 100.0 if estimated_cost is not None and total_assets and total_assets > 0 else 0.0
    decision_edge = raw_edge - cost_penalty_points if raw_edge is not None else None
    cost_reasonable = estimated_cost is None or cost_penalty_points <= 1.0
    reason_codes: list[str] = []
    if edge_no_action is not None and edge_no_action < config.min_decision_edge:
        reason_codes.append("EDGE_VS_NO_ACTION_LOW")
    if edge_holdings is not None and edge_holdings < config.min_holding_edge:
        reason_codes.append("EDGE_VS_HOLDINGS_LOW")
    if not cost_reasonable:
        reason_codes.append("TRANSACTION_COST_HIGH")
    if market_frozen:
        reason_codes.append("MARKET_STATE_FROZEN")
    return {
        "action_score": action_score,
        "no_action_threshold": threshold,
        "edge_vs_no_action": edge_no_action,
        "edge_vs_current_holdings": edge_holdings,
        "raw_decision_edge": raw_edge,
        "decision_edge": decision_edge,
        "probe_notional": probe_notional,
        "estimated_cost": estimated_cost,
        "estimated_cost_bps": estimated_cost_bps,
        "cost_penalty_points": cost_penalty_points,
        "cost_reasonable": cost_reasonable,
        "transaction_cost_configured": model.commission_bps is not None and model.minimum_commission is not None,
        "reason_codes": reason_codes,
    }


__all__ = [
    "TransactionCostModel",
    "calculate_action_score",
    "calculate_decision_edge",
    "held_opportunity_baseline",
    "no_action_threshold",
]
