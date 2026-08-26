"""Deterministic stock Opportunity Score."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from .config import CandidateConfig, DEFAULT_CONFIG
from .factors import (
    combine_components,
    component,
    feature_snapshot,
    metadata_section,
    metric_value,
    percentile_rank,
    section_available_at,
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _rank_or_scale(
    code: str,
    value: float | None,
    values: Mapping[str, float | None] | None,
    *,
    direction: str = "higher",
    low: float | None = None,
    high: float | None = None,
    config: CandidateConfig,
) -> float | None:
    if value is None:
        return None
    if values:
        # A supplied cross-section is authoritative.  Small samples are
        # intentionally unavailable rather than being presented as precise.
        return percentile_rank(
            value,
            values.values(),
            direction=direction,
            winsorize=(config.percentile_winsorize_low, config.percentile_winsorize_high),
            min_samples=config.percentile_min_samples,
        )
    if low is None or high is None or high <= low:
        return None
    normalized = 100.0 * (value - low) / (high - low)
    return max(0.0, min(100.0, normalized if direction == "higher" else 100.0 - normalized))


def _context_values(context: Mapping[str, Any], key: str) -> Mapping[str, float | None] | None:
    value = context.get(key)
    return value if isinstance(value, Mapping) else None


def _available_section(metadata: Mapping[str, Any], name: str, as_of: date | datetime | str | None) -> dict[str, Any]:
    section = metadata_section(metadata, name)
    return section if section and section_available_at(section, as_of) else {}


def _trend_score(features: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    price, ma20, ma60, ma120 = features.get("price"), features.get("ma20"), features.get("ma60"), features.get("ma120")
    checks: list[bool] = []
    if price is not None and ma20 is not None:
        checks.append(price > ma20)
    if ma20 is not None and ma60 is not None:
        checks.append(ma20 > ma60)
    if ma60 is not None and ma120 is not None:
        checks.append(ma60 > ma120)
    if features.get("ma20_slope") is not None:
        checks.append(float(features["ma20_slope"]) > 0)
    if features.get("ma60_slope") is not None:
        checks.append(float(features["ma60_slope"]) > 0)
    return (100.0 * sum(checks) / len(checks), {"checks": checks, "price": price, "ma20": ma20, "ma60": ma60, "ma120": ma120}) if checks else (None, {})


def score_stock_candidate(
    code: str,
    bars: Iterable[Any],
    *,
    price: float | None = None,
    quote: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    cross_sectional: Mapping[str, Mapping[str, float | None]] | None = None,
    as_of: date | datetime | str | None = None,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    quote = dict(quote or {})
    metadata = dict(metadata or {})
    features = feature_snapshot(bars, price=price or _number(quote.get("price")))
    cross_sectional = cross_sectional or {}
    trend, trend_raw = _trend_score(features)

    return20 = features.get("return20")
    return60 = features.get("return60")
    momentum20 = _rank_or_scale(code, return20, _context_values(cross_sectional, "return20"), low=-0.30, high=0.50, config=config)
    momentum60 = _rank_or_scale(code, return60, _context_values(cross_sectional, "return60"), low=-0.50, high=1.00, config=config)
    acceleration = None
    if return20 is not None and return60 is not None:
        acceleration = _rank_or_scale(code, return20 - return60 / 3.0, _context_values(cross_sectional, "acceleration"), low=-0.30, high=0.30, config=config)
    momentum_parts = [
        (value, weight)
        for value, weight in zip((momentum20, momentum60, acceleration), (0.4, 0.4, 0.2))
        if value is not None
    ]
    momentum = (
        sum(value * weight for value, weight in momentum_parts) / sum(weight for _, weight in momentum_parts)
        if momentum_parts
        else None
    )

    fundamental = _available_section(metadata, "fundamental", as_of)
    roe = metric_value(fundamental, "roe", "roe_ttm")
    revenue_growth = metric_value(fundamental, "revenue_yoy", "revenue_growth")
    profit_growth = metric_value(fundamental, "profit_yoy", "profit_growth")
    cash_quality = metric_value(fundamental, "operating_cash_quality", "ocf_quality", "operating_cash_flow")
    margin_quality = metric_value(fundamental, "margin_quality", "gross_margin", "net_margin")
    fundamental_parts = [
        _rank_or_scale(code, roe, _context_values(cross_sectional, "roe"), low=-0.20, high=0.40, config=config),
        _rank_or_scale(code, revenue_growth, _context_values(cross_sectional, "revenue_growth"), low=-0.50, high=1.00, config=config),
        _rank_or_scale(code, profit_growth, _context_values(cross_sectional, "profit_growth"), low=-1.00, high=2.00, config=config),
        _rank_or_scale(code, cash_quality, _context_values(cross_sectional, "cash_quality"), low=-1.00, high=2.00, config=config),
        _rank_or_scale(code, margin_quality, _context_values(cross_sectional, "margin_quality"), low=0.0, high=1.0, config=config),
    ]
    fundamental_weights = (0.30, 0.20, 0.25, 0.15, 0.10)
    usable_fundamental = [(value, weight) for value, weight in zip(fundamental_parts, fundamental_weights) if value is not None]
    fundamental_score = sum(value * weight for value, weight in usable_fundamental) / sum(weight for _, weight in usable_fundamental) if usable_fundamental else None

    valuation = _available_section(metadata, "valuation", as_of)
    pe = metric_value(valuation, "pe_ttm", "pe")
    pb = metric_value(valuation, "pb")
    dividend = metric_value(valuation, "dividend_yield", "dividend_yield_ratio")
    pe_score = _rank_or_scale(code, pe if pe is not None and pe > 0 else None, _context_values(cross_sectional, "pe"), direction="lower", low=0.0, high=80.0, config=config)
    pb_score = _rank_or_scale(code, pb if pb is not None and pb > 0 else None, _context_values(cross_sectional, "pb"), direction="lower", low=0.0, high=12.0, config=config)
    dividend_score = _rank_or_scale(code, dividend, _context_values(cross_sectional, "dividend"), low=0.0, high=0.15, config=config)
    valuation_parts = [(value, weight) for value, weight in ((pe_score, 0.45), (pb_score, 0.35), (dividend_score, 0.20)) if value is not None]
    valuation_score = sum(value * weight for value, weight in valuation_parts) / sum(weight for _, weight in valuation_parts) if valuation_parts else None

    amount = features.get("median_amount20")
    turnover = features.get("latest_bar", {}).get("turnover_rate") if features.get("latest_bar") else None
    rel_volume = features.get("relative_volume20")
    price_volume = features.get("price_volume_confirmation")
    money_flow = metric_value(_available_section(metadata, "flow", as_of), "main_net", "net_money_flow")
    flow_parts = [
        (_rank_or_scale(code, amount, _context_values(cross_sectional, "amount20"), low=0.0, high=1e9, config=config), 0.25),
        (_rank_or_scale(code, turnover, _context_values(cross_sectional, "turnover"), low=0.0, high=20.0, config=config), 0.20),
        (_rank_or_scale(code, rel_volume, _context_values(cross_sectional, "relative_volume20"), low=0.0, high=5.0, config=config), 0.25),
        (price_volume, 0.20),
        (_rank_or_scale(code, money_flow, _context_values(cross_sectional, "money_flow"), low=-1e8, high=1e8, config=config), 0.10),
    ]
    usable_flow = [(value, weight) for value, weight in flow_parts if value is not None]
    flow_score = sum(value * weight for value, weight in usable_flow) / sum(weight for _, weight in usable_flow) if usable_flow else None

    industry = _available_section(metadata, "industry", as_of)
    industry_rs = metric_value(industry, "relative_strength", "rs20", "industry_rs")
    industry_breadth = metric_value(industry, "breadth", "industry_breadth")
    industry_trend = metric_value(industry, "trend", "industry_trend")
    industry_parts = [
        (_rank_or_scale(code, industry_rs, _context_values(cross_sectional, "industry_rs"), low=-0.30, high=0.50, config=config), 0.40),
        (_rank_or_scale(code, industry_breadth, _context_values(cross_sectional, "industry_breadth"), low=0.0, high=1.0, config=config), 0.30),
        (_rank_or_scale(code, industry_trend, _context_values(cross_sectional, "industry_trend"), low=-1.0, high=1.0, config=config), 0.30),
    ]
    usable_industry = [(value, weight) for value, weight in industry_parts if value is not None]
    industry_score = sum(value * weight for value, weight in usable_industry) / sum(weight for _, weight in usable_industry) if usable_industry else None

    volatility = features.get("volatility60") or features.get("volatility20")
    drawdown = features.get("max_drawdown60")
    tail = features.get("downside_tail_frequency")
    risk_parts = [
        (100.0 - min(100.0, max(0.0, float(volatility or 0.0) * 1000.0)), 0.35) if volatility is not None else (None, 0.35),
        (100.0 - min(100.0, max(0.0, float(drawdown or 0.0) * 300.0)), 0.35) if drawdown is not None else (None, 0.35),
        (100.0 - min(100.0, max(0.0, float(tail or 0.0) * 500.0)), 0.30) if tail is not None else (None, 0.30),
    ]
    usable_risk = [(value, weight) for value, weight in risk_parts if value is not None]
    risk_score = sum(value * weight for value, weight in usable_risk) / sum(weight for _, weight in usable_risk) if usable_risk else None

    components = {
        "trend": component(trend, raw=trend_raw, source="daily_bar_cache"),
        "momentum": component(momentum, raw={"return20": return20, "return60": return60, "acceleration": return20 - return60 / 3.0 if return20 is not None and return60 is not None else None}, source="daily_bar_cache"),
        "fundamental": component(fundamental_score, raw=fundamental, source="factor_metadata", reason="point-in-time fundamental data only"),
        "valuation": component(valuation_score, raw={"pe_ttm": pe, "pb": pb, "dividend_yield": dividend}, source="factor_metadata", reason="negative PE is unavailable"),
        "flow": component(flow_score, raw={"median_amount20": amount, "turnover": turnover, "relative_volume20": rel_volume, "money_flow": money_flow}, source="daily_bar_cache/factor_metadata"),
        "industry": component(industry_score, raw=industry, source="factor_metadata", reason="industry mapping is never inferred from security name"),
        "risk": component(risk_score, raw={"volatility60": volatility, "max_drawdown60": drawdown, "downside_tail_frequency": tail}, source="daily_bar_cache"),
    }
    combined = combine_components(components, config.stock_factor_weights)
    return {
        **combined,
        "components": components,
        "features": features,
        "quality_status": "FULL" if combined["coverage"] >= 0.80 else "DEGRADED" if combined["coverage"] >= 0.65 else "INSUFFICIENT",
        "security_type": "STOCK",
    }


stock_opportunity_score = score_stock_candidate


__all__ = ["score_stock_candidate", "stock_opportunity_score"]
