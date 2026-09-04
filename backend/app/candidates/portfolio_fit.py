"""Portfolio-fit simulation using the existing Phase E portfolio facts."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from statistics import mean, stdev
from typing import Any

from ..portfolio.constraints import hard_cap_for_security
from .config import CandidateConfig, DEFAULT_CONFIG
from .factors import normalize_bars, returns_from_closes


CORRELATION_LOOKBACK_DAYS = 60
CORRELATION_MIN_SAMPLES = 40


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _returns(bars: Iterable[Any], *, lookback: int = CORRELATION_LOOKBACK_DAYS) -> dict[Any, float]:
    rows = normalize_bars(bars)
    rows = rows[-(lookback + 1) :]
    closes = [row["close"] for row in rows]
    dates = [row["trade_date"] for row in rows]
    values = returns_from_closes(closes)
    return {day: value for day, value in zip(dates[1:], values)}


def _correlation(
    first: dict[Any, float],
    second: dict[Any, float],
    min_samples: int = CORRELATION_MIN_SAMPLES,
) -> tuple[float | None, int]:
    dates = sorted(set(first) & set(second))
    if len(dates) < min_samples:
        return None, len(dates)
    left = [first[day] for day in dates]
    right = [second[day] for day in dates]
    left_mean, right_mean = mean(left), mean(right)
    left_var = sum((value - left_mean) ** 2 for value in left)
    right_var = sum((value - right_mean) ** 2 for value in right)
    if left_var <= 0 or right_var <= 0:
        return None, len(dates)
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    return covariance / math.sqrt(left_var * right_var), len(dates)


def _portfolio_positions(portfolio_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    positions = portfolio_context.get("positions")
    if isinstance(positions, list):
        return [dict(row) for row in positions if isinstance(row, Mapping) and row.get("code")]
    constraints = portfolio_context.get("position_constraints")
    if isinstance(constraints, list):
        return [dict(row) for row in constraints if isinstance(row, Mapping) and row.get("code")]
    return []


def _candidate_cap(candidate: Mapping[str, Any]) -> float | None:
    cap, _ = hard_cap_for_security(candidate.get("security_type"), candidate.get("etf_category"))
    return cap


def _replacement_source(
    held_opportunity_scores: Iterable[Mapping[str, Any]],
    positions: Iterable[Mapping[str, Any]],
    *,
    config: CandidateConfig,
) -> tuple[str | None, float | None, float | None]:
    """Choose one weak but auditable held position for simulation only."""

    weights = {
        str(row.get("code")): max(0.0, _number(row.get("weight")) or 0.0)
        for row in positions
        if row.get("code")
    }
    candidates: list[tuple[float, float, str, float]] = []
    for raw in held_opportunity_scores:
        if not isinstance(raw, Mapping):
            continue
        code = str(raw.get("code") or "")
        weight = weights.get(code, 0.0)
        if not code or weight <= 0:
            continue
        opportunity = _number(raw.get("opportunity_score"))
        keep = _number(raw.get("keep_score"))
        if opportunity is None and keep is None:
            continue
        confidence = _number(raw.get("confidence"))
        coverage = _number(raw.get("coverage"))
        if confidence is not None and confidence < 50.0:
            continue
        if coverage is not None and coverage < 0.50:
            continue
        weakness = min(value for value in (keep, opportunity) if value is not None)
        if weakness > config.replacement_keep_score_max:
            continue
        candidates.append((weakness, opportunity if opportunity is not None else weakness, code, weight))
    if not candidates:
        return None, None, None
    weakness, opportunity, code, weight = min(candidates, key=lambda item: (item[0], item[1], item[2]))
    return code, weight, opportunity


def calculate_portfolio_fit(
    candidate: Mapping[str, Any],
    portfolio_context: Mapping[str, Any],
    *,
    holding_bars: Mapping[str, Iterable[Any]] | None = None,
    candidate_bars: Iterable[Any] | None = None,
    held_opportunity_scores: Iterable[Mapping[str, Any]] = (),
    config: CandidateConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Simulate a small probe; ``probe_weight`` is never a recommended size."""

    positions = _portfolio_positions(portfolio_context)
    holding_bars = holding_bars or {}
    candidate_code = str(candidate.get("code") or "")
    candidate_returns = _returns(candidate_bars or [])
    correlations: list[dict[str, Any]] = []
    weighted_sum = weighted_denominator = 0.0
    max_corr: float | None = None
    for position in positions:
        code = str(position.get("code") or "")
        if not code or code not in holding_bars:
            continue
        corr, samples = _correlation(candidate_returns, _returns(holding_bars[code]))
        if corr is None:
            continue
        weight = max(0.0, _number(position.get("weight")) or 0.0)
        correlations.append({"code": code, "correlation": corr, "samples": samples, "weight": weight})
        weighted_sum += corr * weight
        weighted_denominator += weight
        max_corr = corr if max_corr is None else max(max_corr, corr)
    weighted_corr = weighted_sum / weighted_denominator if weighted_denominator > 0 else None
    high_corr_positions = [row["code"] for row in correlations if row["correlation"] >= 0.80]
    diversification_score = 100.0 if not positions else 100.0 * (1.0 - max(0.0, min(1.0, weighted_corr))) if weighted_corr is not None else None

    cash_ratio = _number(portfolio_context.get("cash_ratio"))
    spendable_cash = _number(portfolio_context.get("spendable_cash", portfolio_context.get("cash")))
    total_assets = _number(portfolio_context.get("total_assets") or portfolio_context.get("current_estimated_total_assets"))
    if cash_ratio is None and spendable_cash is not None and total_assets and total_assets > 0:
        cash_ratio = spendable_cash / total_assets
    cap = _candidate_cap(candidate)
    existing_weight = max(0.0, _number(candidate.get("current_weight")) or 0.0)
    headroom = None if cap is None else max(0.0, cap - existing_weight)
    cash_probe = min(config.portfolio_probe_weight, cash_ratio) if cash_ratio is not None else None
    if headroom is not None and cash_probe is not None:
        cash_probe = min(cash_probe, headroom)
    if cash_probe is not None and cash_probe >= config.min_spendable_cash_ratio:
        probe_weight = cash_probe
        funding_mode = "CASH_FUNDED"
        probe_source_code = None
        replacement_opportunity_score = None
    else:
        probe_source_code, source_weight, replacement_opportunity_score = _replacement_source(
            held_opportunity_scores,
            positions,
            config=config,
        )
        if probe_source_code is not None and source_weight is not None:
            probe_weight = min(config.portfolio_probe_weight, source_weight)
            if headroom is not None:
                probe_weight = min(probe_weight, headroom)
            funding_mode = "REPLACEMENT_REVIEW" if probe_weight >= config.min_spendable_cash_ratio else "UNFUNDED"
            if funding_mode == "UNFUNDED":
                probe_weight = 0.0
                probe_source_code = None
        else:
            probe_weight = 0.0
            funding_mode = "UNFUNDED"
            replacement_opportunity_score = None

    weights = {str(row.get("code")): max(0.0, _number(row.get("weight")) or 0.0) for row in positions if row.get("code")}
    current_hhi = _number(portfolio_context.get("hhi"))
    if current_hhi is None:
        current_hhi = sum(weight * weight for weight in weights.values())
    projected_weights = dict(weights)
    if funding_mode == "REPLACEMENT_REVIEW" and probe_source_code:
        projected_weights[probe_source_code] = max(
            0.0,
            projected_weights.get(probe_source_code, 0.0) - max(0.0, probe_weight or 0.0),
        )
    projected_weights[candidate_code] = projected_weights.get(candidate_code, 0.0) + max(0.0, probe_weight or 0.0)
    projected_hhi = sum(weight * weight for weight in projected_weights.values())
    hhi_delta = projected_hhi - current_hhi
    concentration_score = max(0.0, min(100.0, 100.0 - max(0.0, hhi_delta) * 500.0))
    hard_cap_violation = cap is not None and (existing_weight + (probe_weight or 0.0)) > cap + 1e-9
    if hard_cap_violation:
        concentration_score = 0.0

    candidate_return_values = list(candidate_returns.values())
    candidate_volatility = stdev(candidate_return_values) * math.sqrt(242.0) if len(candidate_return_values) >= 2 else None
    current_volatility = _number(portfolio_context.get("portfolio_vol_60") or portfolio_context.get("portfolio_vol_20"))
    projected_volatility = None
    risk_delta = None
    marginal_risk_score = None
    if candidate_volatility is not None:
        correlation_for_risk = weighted_corr if weighted_corr is not None else 0.0
        base = current_volatility or 0.0
        probe = max(0.0, probe_weight or 0.0)
        if funding_mode == "CASH_FUNDED":
            # Cash is not a risk asset.  Existing risk assets remain at their
            # original weights when the probe is funded from cash.
            projected_variance = (
                base**2
                + probe**2 * candidate_volatility**2
                + 2.0 * probe * correlation_for_risk * base * candidate_volatility
            )
        else:
            projected_variance = (
                (1.0 - probe) ** 2 * base**2
                + probe**2 * candidate_volatility**2
                + 2.0 * (1.0 - probe) * probe * correlation_for_risk * base * candidate_volatility
            )
        projected_volatility = math.sqrt(max(0.0, projected_variance))
        risk_delta = projected_volatility - base
        marginal_risk_score = max(0.0, min(100.0, 70.0 - risk_delta * 250.0))
    elif weighted_corr is not None:
        marginal_risk_score = max(0.0, min(100.0, 100.0 * (1.0 - max(0.0, weighted_corr))))

    candidate_industry = candidate.get("industry") or (candidate.get("metadata") or {}).get("industry")
    industry_score = None
    industry_available = False
    if candidate_industry:
        industry_weights: dict[str, float] = {}
        for position in positions:
            industry = position.get("industry")
            weight = max(0.0, _number(position.get("weight")) or 0.0)
            if industry:
                industry_weights[str(industry)] = industry_weights.get(str(industry), 0.0) + weight
        current_industry_weight = industry_weights.get(str(candidate_industry), 0.0)
        industry_score = max(0.0, min(100.0, 100.0 - current_industry_weight * 250.0))
        industry_available = True

    fit_components = {
        "diversification": {"score": diversification_score, "available": diversification_score is not None, "confidence": 100.0 if not positions or correlations else 60.0},
        "marginal_risk": {"score": marginal_risk_score, "available": marginal_risk_score is not None, "confidence": 90.0 if candidate_volatility is not None else 60.0},
        "concentration": {"score": concentration_score, "available": True, "confidence": 100.0},
        "exposure": {"score": industry_score, "available": industry_available, "confidence": 80.0},
    }
    usable = {key: value for key, value in fit_components.items() if value["available"] and value["score"] is not None}
    available_weight = sum(config.portfolio_fit_weights[key] for key in usable)
    fit_score = sum(float(usable[key]["score"]) * config.portfolio_fit_weights[key] for key in usable) / available_weight if available_weight else None
    fit_confidence = sum(float(usable[key]["confidence"]) * config.portfolio_fit_weights[key] for key in usable) / available_weight * available_weight if available_weight else 0.0

    return {
        "score": round(fit_score, 4) if fit_score is not None else None,
        "portfolio_fit_score": round(fit_score, 4) if fit_score is not None else None,
        "available_weight": round(available_weight, 6),
        "coverage": round(available_weight, 6),
        "confidence": round(fit_confidence, 4),
        "components": fit_components,
        "probe_weight": probe_weight,
        "probe_weight_is_simulation": True,
        "simulation_only": True,
        "order_generation_allowed": False,
        "funding_mode": funding_mode,
        "probe_source_code": probe_source_code,
        "replacement_opportunity_score": replacement_opportunity_score,
        "replacement_weight_delta": (
            -probe_weight if funding_mode == "REPLACEMENT_REVIEW" and probe_source_code else 0.0
        ),
        "cash_ratio": cash_ratio,
        "spendable_cash": spendable_cash,
        "candidate_hard_cap": cap,
        "candidate_hard_cap_headroom": headroom,
        "weighted_candidate_correlation": weighted_corr,
        "max_candidate_correlation": max_corr,
        "high_corr_positions": high_corr_positions,
        "correlation_samples": correlations,
        "current_hhi": current_hhi,
        "projected_weights": projected_weights,
        "projected_hhi": projected_hhi,
        "hhi_delta": hhi_delta,
        "current_portfolio_volatility": current_volatility,
        "candidate_volatility": candidate_volatility,
        "correlation_for_risk": weighted_corr if weighted_corr is not None else 0.0,
        "projected_portfolio_volatility": projected_volatility,
        "risk_delta": risk_delta,
        "hard_cap_violation": hard_cap_violation,
        "industry_available": industry_available,
    }


portfolio_fit_score = calculate_portfolio_fit


__all__ = ["calculate_portfolio_fit", "portfolio_fit_score"]
