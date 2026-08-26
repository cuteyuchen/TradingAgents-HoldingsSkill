"""Deterministic ETF Opportunity Score with explicit proxy/coverage semantics."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from .config import CandidateConfig, DEFAULT_CONFIG
from .factors import combine_components, component, feature_snapshot, metadata_section, metric_value, percentile_rank, section_available_at


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _rank_or_scale(code: str, value: float | None, values: Mapping[str, float | None] | None, *, direction: str = "higher", low: float | None = None, high: float | None = None, config: CandidateConfig) -> float | None:
    if value is None:
        return None
    if values:
        return percentile_rank(value, values.values(), direction=direction, winsorize=(config.percentile_winsorize_low, config.percentile_winsorize_high), min_samples=config.percentile_min_samples)
    if low is None or high is None or high <= low:
        return None
    normalized = 100.0 * (value - low) / (high - low)
    return max(0.0, min(100.0, normalized if direction == "higher" else 100.0 - normalized))


def _values(context: Mapping[str, Any], key: str) -> Mapping[str, float | None] | None:
    value = context.get(key)
    return value if isinstance(value, Mapping) else None


def score_etf_candidate(
    code: str,
    bars: Iterable[Any],
    *,
    price: float | None = None,
    quote: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    cross_sectional: Mapping[str, Mapping[str, float | None]] | None = None,
    benchmark: Mapping[str, Any] | None = None,
    underlying_bars: Iterable[Any] | None = None,
    as_of: date | datetime | str | None = None,
    live: bool | None = None,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    quote = dict(quote or {})
    metadata = dict(metadata or {})
    cross_sectional = cross_sectional or {}
    rows = list(underlying_bars or bars)
    features = feature_snapshot(rows, price=price or _number(quote.get("price")))
    underlying_proxy = underlying_bars is None
    current, ma20, ma60, ma120 = features.get("price"), features.get("ma20"), features.get("ma60"), features.get("ma120")
    trend_checks = []
    if current is not None and ma20 is not None:
        trend_checks.append(current > ma20)
    if ma20 is not None and ma60 is not None:
        trend_checks.append(ma20 > ma60)
    if ma60 is not None and ma120 is not None:
        trend_checks.append(ma60 > ma120)
    if features.get("ma20_slope") is not None:
        trend_checks.append(float(features["ma20_slope"]) > 0)
    trend = 100.0 * sum(trend_checks) / len(trend_checks) if trend_checks else None
    benchmark_return20 = _number((benchmark or {}).get("return20"))
    benchmark_return60 = _number((benchmark or {}).get("return60"))
    relative20 = features.get("return20") - benchmark_return20 if features.get("return20") is not None and benchmark_return20 is not None else features.get("return20")
    relative60 = features.get("return60") - benchmark_return60 if features.get("return60") is not None and benchmark_return60 is not None else features.get("return60")
    relative_parts = [
        _rank_or_scale(code, relative20, _values(cross_sectional, "relative20"), low=-0.50, high=0.80, config=config),
        _rank_or_scale(code, relative60, _values(cross_sectional, "relative60"), low=-0.80, high=1.50, config=config),
    ]
    relative_usable = [item for item in relative_parts if item is not None]
    relative_strength = sum(relative_usable) / len(relative_usable) if relative_usable else None

    amount = features.get("median_amount20")
    turnover = features.get("latest_bar", {}).get("turnover_rate") if features.get("latest_bar") else None
    liquidity_parts = [
        (_rank_or_scale(code, amount, _values(cross_sectional, "amount20"), low=0.0, high=1e9, config=config), 0.6),
        (_rank_or_scale(code, turnover, _values(cross_sectional, "turnover"), low=0.0, high=20.0, config=config), 0.4),
    ]
    liquidity_usable = [(value, weight) for value, weight in liquidity_parts if value is not None]
    liquidity = sum(value * weight for value, weight in liquidity_usable) / sum(weight for _, weight in liquidity_usable) if liquidity_usable else None

    factor_live = as_of is None if live is None else live
    valuation = metadata_section(metadata, "valuation")
    valuation = valuation if section_available_at(valuation, as_of, live=factor_live) else {}
    pe = metric_value(valuation, "pe_ttm", "pe")
    pb = metric_value(valuation, "pb")
    dividend = metric_value(valuation, "dividend_yield", "dividend_yield_ratio")
    valuation_parts = [
        (_rank_or_scale(code, pe if pe is not None and pe > 0 else None, _values(cross_sectional, "pe"), direction="lower", low=0.0, high=80.0, config=config), 0.45),
        (_rank_or_scale(code, pb if pb is not None and pb > 0 else None, _values(cross_sectional, "pb"), direction="lower", low=0.0, high=12.0, config=config), 0.35),
        (_rank_or_scale(code, dividend, _values(cross_sectional, "dividend"), low=0.0, high=0.15, config=config), 0.20),
    ]
    valuation_usable = [(value, weight) for value, weight in valuation_parts if value is not None]
    valuation_score = sum(value * weight for value, weight in valuation_usable) / sum(weight for _, weight in valuation_usable) if valuation_usable else None

    breadth = metadata_section(metadata, "constituent_breadth") or metadata_section(metadata, "breadth")
    breadth = breadth if section_available_at(breadth, as_of, live=factor_live) else {}
    breadth_value = metric_value(breadth, "score", "breadth", "advance_ratio", "positive_ratio")
    if breadth_value is not None and breadth_value <= 1:
        breadth_value *= 100.0
    breadth_score = _rank_or_scale(code, breadth_value, _values(cross_sectional, "constituent_breadth"), low=0.0, high=100.0, config=config)

    volatility = features.get("volatility60") or features.get("volatility20")
    drawdown = features.get("max_drawdown60")
    risk_parts = [
        (100.0 - min(100.0, max(0.0, float(volatility or 0.0) * 1000.0)), 0.55) if volatility is not None else (None, 0.55),
        (100.0 - min(100.0, max(0.0, float(drawdown or 0.0) * 300.0)), 0.45) if drawdown is not None else (None, 0.45),
    ]
    risk_usable = [(value, weight) for value, weight in risk_parts if value is not None]
    risk = sum(value * weight for value, weight in risk_usable) / sum(weight for _, weight in risk_usable) if risk_usable else None

    components = {
        "underlying_trend": component(trend, raw={"checks": trend_checks, "proxy": underlying_proxy}, confidence=65.0 if underlying_proxy else 100.0, source="etf_price_proxy" if underlying_proxy else "underlying_index_cache"),
        "relative_strength": component(relative_strength, raw={"relative20": relative20, "relative60": relative60, "benchmark": benchmark}, source="all_a_median_index" if benchmark else "daily_bar_cache"),
        "liquidity": component(liquidity, raw={"median_amount20": amount, "turnover": turnover}, source="daily_bar_cache"),
        "valuation": component(valuation_score, raw={"pe_ttm": pe, "pb": pb, "dividend_yield": dividend}, source="factor_metadata", reason="negative PE is unavailable"),
        "constituent_breadth": component(breadth_score, raw=breadth, source="etf_constituent_cache", reason="missing constituent breadth is unavailable, not zero"),
        "risk": component(risk, raw={"volatility60": volatility, "max_drawdown60": drawdown}, source="daily_bar_cache"),
    }
    combined = combine_components(components, config.etf_factor_weights)
    return {
        **combined,
        "components": components,
        "features": features,
        "underlying_proxy": underlying_proxy,
        "quality_status": "FULL" if combined["coverage"] >= 0.80 else "DEGRADED" if combined["coverage"] >= 0.65 else "INSUFFICIENT",
        "security_type": "ETF",
    }


etf_opportunity_score = score_etf_candidate


__all__ = ["score_etf_candidate", "etf_opportunity_score"]
