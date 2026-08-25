"""Server-owned Portfolio State and deterministic local-cache risk calculations."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from statistics import mean, stdev
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..market.codes import normalize_security_code
from ..market_engine_models import AllAMedianIndexDaily
from ..market_models import SecurityMaster
from ..services.daily_bar_cache import load_daily_bars
from ..services.market_snapshot_service import collect_snapshot_quotes
from ..v2_models import HoldingItem, PortfolioSnapshot
from .config import KEEP_SCORE_MIN_AVAILABLE_WEIGHT, KEEP_SCORE_WEIGHTS
from .constraints import hard_cap_for_security
from .snapshot_diff import snapshot_cash, snapshot_reserve_assets

QuoteLoader = Callable[[list[str]], Any]


def _utc_naive(value: datetime | None) -> datetime:
    value = value or datetime.now(UTC)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _quote_value(raw: Any, field: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        return raw.get(field, default)
    return getattr(raw, field, default)


def _quote_status(raw: Any) -> str:
    value = _quote_value(raw, "quality_status", None)
    if value is None and isinstance(raw, dict):
        return "STALE" if raw.get("stale") else "VALID" if raw.get("price") is not None else "MISSING"
    return str(getattr(value, "value", value) or "MISSING").upper()


def _default_quote_loader(codes: list[str]) -> Any:
    return collect_snapshot_quotes({"codes": codes, "route": "critical"})


def _quote_map(raw: Any) -> dict[str, Any]:
    values = raw.get("quotes") if isinstance(raw, dict) and "quotes" in raw else raw
    if isinstance(values, dict):
        values = list(values.values())
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        return {}
    result: dict[str, Any] = {}
    for item in values:
        code = normalize_security_code(_quote_value(item, "code", ""))
        if code:
            result[code] = item
    return result


def latest_confirmed_snapshot(
    db: Session,
    *,
    portfolio_id: int,
    as_of: datetime | None = None,
) -> PortfolioSnapshot | None:
    query = db.query(PortfolioSnapshot).filter(
        PortfolioSnapshot.portfolio_id == portfolio_id,
        PortfolioSnapshot.status == "confirmed",
    )
    if as_of is not None:
        query = query.filter(PortfolioSnapshot.snapshot_time <= _utc_naive(as_of))
    return query.order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).first()


def build_portfolio_state(
    db: Session,
    *,
    portfolio_id: int,
    as_of: datetime | None = None,
    snapshot: PortfolioSnapshot | None = None,
    quote_loader: QuoteLoader | None = None,
    quote_rows: Any = None,
    allow_live_quotes: bool = True,
) -> dict[str, Any]:
    """Build a current or replay-bounded state without treating screenshots as live prices."""

    as_of = _utc_naive(as_of)
    snapshot = snapshot or latest_confirmed_snapshot(db, portfolio_id=portfolio_id, as_of=as_of)
    if snapshot is None:
        raise ValueError("confirmed_snapshot_not_found")
    codes = [normalize_security_code(item.code) for item in snapshot.holdings if normalize_security_code(item.code)]
    raw_quotes = quote_rows
    historical_snapshot_valuation = not allow_live_quotes and raw_quotes is None
    if raw_quotes is None and codes and allow_live_quotes:
        raw_quotes = (quote_loader or _default_quote_loader)(codes)
    quote_by_code = _quote_map(raw_quotes or [])
    masters = {
        row.code: row
        for row in db.execute(select(SecurityMaster).where(SecurityMaster.market == "CN", SecurityMaster.code.in_(codes))).scalars()
    } if codes else {}
    positions: list[dict[str, Any]] = []
    risk_flags: list[str] = []
    accepted_quotes = 0
    classification_count = 0
    for item in snapshot.holdings:
        code = normalize_security_code(item.code)
        if not code:
            positions.append({
                "code": None,
                "name": item.name,
                "qty": item.qty,
                "available_qty": item.available_qty,
                "market_value": None,
                "weight": None,
                "quote_quality": "MISSING",
                "flags": ["SECURITY_CODE_MISSING"],
            })
            risk_flags.append("SECURITY_CODE_MISSING")
            continue
        quote = quote_by_code.get(code)
        quote_quality = _quote_status(quote) if quote is not None else "MISSING"
        price = _number(_quote_value(quote, "price")) if quote is not None else None
        quote_usable = quote_quality in {"VALID", "DEGRADED"} and price is not None and price > 0
        if quote_usable:
            accepted_quotes += 1
        master = masters.get(code)
        security_type = master.security_type if master is not None else None
        etf_category = master.etf_category if master is not None else None
        if security_type:
            classification_count += 1
        flags: list[str] = []
        if not quote_usable:
            flags.append(f"QUOTE_{quote_quality}")
            risk_flags.append(f"QUOTE_{quote_quality}:{code}")
        if master is None:
            flags.append("SECURITY_CLASSIFICATION_UNKNOWN")
        hard_cap, cap_flags = hard_cap_for_security(security_type, etf_category)
        flags.extend(cap_flags)
        # Historical replay may only use contemporaneous snapshot valuation or
        # explicitly supplied archived quotes; it must never fetch live data.
        market_value = item.qty * price if quote_usable and item.qty is not None else (
            item.market_value if historical_snapshot_valuation else None
        )
        positions.append({
            "code": code,
            "name": item.name or (master.name if master is not None else None),
            "security_type": security_type,
            "etf_category": etf_category,
            "current_price": price if quote_usable else None,
            "quote_quality": quote_quality,
            "qty": item.qty,
            "available_qty": item.available_qty,
            "screenshot_price": item.screenshot_price,
            "snapshot_market_value": item.market_value,
            "market_value": market_value,
            "cost": item.cost,
            "hard_cap": hard_cap,
            "flags": flags,
        })
    cash = snapshot_cash(snapshot)
    repo_or_standard_bond_value = snapshot.repo_or_standard_bond_value
    reserve_assets = snapshot_reserve_assets(snapshot)
    valued_positions = [float(row["market_value"]) for row in positions if row.get("market_value") is not None]
    complete_valuation = len(valued_positions) == len(positions)
    market_value = sum(valued_positions) if complete_valuation else None
    snapshot_total_assets = snapshot.total_assets
    current_estimated_total_assets = market_value + reserve_assets if market_value is not None and reserve_assets is not None else None
    total_assets = snapshot_total_assets if historical_snapshot_valuation else current_estimated_total_assets
    if total_assets is None and historical_snapshot_valuation:
        total_assets = current_estimated_total_assets
    flags = list(dict.fromkeys(risk_flags))
    if historical_snapshot_valuation:
        flags.append("HISTORICAL_SNAPSHOT_VALUATION")
    if (
        snapshot_total_assets is not None
        and snapshot_total_assets > 0
        and current_estimated_total_assets is not None
        and abs(current_estimated_total_assets - snapshot_total_assets) > snapshot_total_assets * 0.02
    ):
        flags.append("VALUATION_DRIFT")
    if total_assets is None or total_assets <= 0:
        flags.append("TOTAL_ASSETS_UNKNOWN")
    for row in positions:
        row["weight"] = (float(row["market_value"]) / total_assets) if row.get("market_value") is not None and total_assets and total_assets > 0 else None
        if row["hard_cap"] is not None and row["weight"] is not None and row["weight"] > row["hard_cap"] + 1e-9:
            row["flags"].append("HARD_CAP_BREACH")
            flags.append(f"HARD_CAP_BREACH:{row['code']}")
    cash_ratio = reserve_assets / total_assets if reserve_assets is not None and total_assets and total_assets > 0 else None
    cash_only_ratio = cash / total_assets if cash is not None and total_assets and total_assets > 0 else None
    gross_exposure = sum(float(row["weight"] or 0.0) for row in positions if row.get("weight") is not None) if total_assets else None
    missing_quotes = len(codes) - accepted_quotes
    if codes and accepted_quotes == 0 and not historical_snapshot_valuation:
        quality = "BLOCKED"
    elif missing_quotes or not complete_valuation or "VALUATION_DRIFT" in flags or historical_snapshot_valuation:
        quality = "DEGRADED"
    else:
        quality = "VALID"
    return {
        "portfolio_id": portfolio_id,
        "snapshot_id": snapshot.id,
        "snapshot_time": snapshot.snapshot_time.isoformat(),
        "as_of": as_of.isoformat(),
        "total_assets": total_assets,
        "snapshot_total_assets": snapshot_total_assets,
        "current_estimated_total_assets": current_estimated_total_assets,
        "total_market_value": market_value,
        "cash": cash,
        "repo_or_standard_bond_value": repo_or_standard_bond_value,
        "reserve_assets": reserve_assets,
        "cash_ratio": cash_ratio,
        "cash_only_ratio": cash_only_ratio,
        "gross_exposure": gross_exposure,
        "position_count": len(positions),
        "stock_count": sum(1 for row in positions if row.get("security_type") == "STOCK"),
        "etf_count": sum(1 for row in positions if row.get("security_type") == "ETF"),
        "unclassified_count": len(positions) - classification_count,
        "positions": positions,
        "quote_coverage": accepted_quotes / len(codes) if codes else 1.0,
        "classification_coverage": classification_count / len(positions) if positions else 1.0,
        "risk_flags": list(dict.fromkeys(flags)),
        "quality_status": quality,
        "data_quality": {
            "quote_coverage": accepted_quotes / len(codes) if codes else 1.0,
            "missing_quote_count": missing_quotes,
            "classification_coverage": classification_count / len(positions) if positions else 1.0,
        },
    }


def _returns_by_code(db: Session, codes: list[str], as_of: datetime) -> dict[str, dict[Any, float]]:
    rows = load_daily_bars(
        db,
        codes,
        trade_date=as_of.date(),
        available_at=as_of,
        limit=max(settings.PORTFOLIO_CORRELATION_LOOKBACK_DAYS + 5, 70),
    )
    closes: dict[str, list[tuple[Any, float]]] = defaultdict(list)
    for row in rows:
        close = _number(row.get("close"))
        if close is not None and close > 0:
            closes[row["code"]].append((row["trade_date"], close))
    output: dict[str, dict[Any, float]] = {}
    for code, series in closes.items():
        values: dict[Any, float] = {}
        for (previous_date, previous), (current_date, current) in zip(series, series[1:]):
            if previous > 0:
                values[current_date] = current / previous - 1.0
        output[code] = values
    return output


def _pair_correlation(first: dict[Any, float], second: dict[Any, float]) -> tuple[float | None, int]:
    dates = sorted(set(first) & set(second))
    if len(dates) < settings.PORTFOLIO_CORRELATION_MIN_SAMPLES:
        return None, len(dates)
    left = [first[day] for day in dates]
    right = [second[day] for day in dates]
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    if left_var <= 0 or right_var <= 0:
        return None, len(dates)
    return numerator / math.sqrt(left_var * right_var), len(dates)


def _volatility(values: dict[Any, float], window: int) -> float | None:
    series = [values[day] for day in sorted(values)[-window:]]
    if len(series) < min(window, 2):
        return None
    return stdev(series) * math.sqrt(settings.PORTFOLIO_TRADING_DAYS_PER_YEAR)


def _ma(values: list[float], length: int) -> float | None:
    return mean(values[-length:]) if len(values) >= length else None


def _keep_score(
    position: dict[str, Any],
    closes: list[float],
    max_corr: float | None,
    benchmark_return_20: float | None,
) -> tuple[float | None, dict[str, float | None], float, float]:
    price = position.get("current_price")
    ma20, ma60 = _ma(closes, 20), _ma(closes, 60)
    trend = None
    if price is not None and ma20 is not None and ma60 is not None:
        parts = [float(price) > ma20, float(price) > ma60]
        if len(closes) >= 61:
            parts.extend([ma20 > mean(closes[-21:-1]), ma60 > mean(closes[-61:-1])])
        trend = 100.0 * sum(parts) / len(parts)
    relative = None
    if len(closes) >= 21 and benchmark_return_20 is not None:
        relative_return = closes[-1] / closes[-21] - 1.0 - benchmark_return_20
        relative = max(0.0, min(100.0, 50.0 + relative_return * 500))
    vol = position.get("volatility_60") or position.get("volatility_20")
    risk_quality = max(0.0, min(100.0, 100.0 - float(vol) * 100.0)) if vol is not None else None
    diversification = max(0.0, min(100.0, 100.0 * (1.0 - max_corr))) if max_corr is not None else None
    qty, available = position.get("qty"), position.get("available_qty")
    execution_availability = (100.0 * max(0.0, min(1.0, float(available) / float(qty)))) if qty and available is not None else None
    parts = {
        "trend_health": trend,
        "relative_strength": relative,
        "risk_quality": risk_quality,
        "diversification_contribution": diversification,
        "execution_availability": execution_availability,
    }
    usable = {key: value for key, value in parts.items() if key in KEEP_SCORE_WEIGHTS and value is not None}
    if not usable:
        return None, parts, 0.0, 0.0
    total_weight = sum(KEEP_SCORE_WEIGHTS[key] for key in usable)
    available_weight = total_weight
    confidence = 100.0 * available_weight / sum(KEEP_SCORE_WEIGHTS.values())
    if available_weight < KEEP_SCORE_MIN_AVAILABLE_WEIGHT:
        return None, parts, available_weight, confidence
    score = sum(float(value) * KEEP_SCORE_WEIGHTS[key] / total_weight for key, value in usable.items())
    return score, parts, available_weight, confidence


def _clusters(nodes: list[str], pairs: list[dict[str, Any]]) -> list[list[str]]:
    graph: dict[str, set[str]] = {node: set() for node in nodes}
    for pair in pairs:
        graph[pair["code_a"]].add(pair["code_b"])
        graph[pair["code_b"]].add(pair["code_a"])
    clusters: list[list[str]] = []
    visited: set[str] = set()
    for node in nodes:
        if node in visited or not graph[node]:
            continue
        stack, group = [node], []
        visited.add(node)
        while stack:
            current = stack.pop()
            group.append(current)
            for neighbor in graph[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        if len(group) >= 2:
            clusters.append(sorted(group))
    return clusters


def calculate_risk_metrics(db: Session, *, state: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    """Compute local-cache concentration, volatility, correlation, and diagnostic Keep Scores."""

    positions = [dict(row) for row in state.get("positions") or [] if row.get("code")]
    codes = [row["code"] for row in positions]
    returns = _returns_by_code(db, codes, as_of)
    bars = load_daily_bars(db, codes, trade_date=as_of.date(), available_at=as_of, limit=70) if codes else []
    closes_by_code: dict[str, list[float]] = defaultdict(list)
    for bar in bars:
        close = _number(bar.get("close"))
        if close is not None:
            closes_by_code[bar["code"]].append(close)
    benchmark_rows = db.execute(select(AllAMedianIndexDaily).where(
        AllAMedianIndexDaily.market == "CN",
        AllAMedianIndexDaily.trade_date <= as_of.date(),
        (AllAMedianIndexDaily.available_at.is_(None)) | (AllAMedianIndexDaily.available_at <= as_of),
        AllAMedianIndexDaily.quality_status.in_(("VALID", "DEGRADED")),
    ).order_by(AllAMedianIndexDaily.trade_date.asc())).scalars().all()
    benchmark_return_20 = None
    if len(benchmark_rows) >= 21 and benchmark_rows[-21].index_value > 0:
        benchmark_return_20 = benchmark_rows[-1].index_value / benchmark_rows[-21].index_value - 1.0
    correlation_pairs: list[dict[str, Any]] = []
    for index, first in enumerate(positions):
        for second in positions[index + 1:]:
            corr, samples = _pair_correlation(returns.get(first["code"], {}), returns.get(second["code"], {}))
            if corr is not None:
                correlation_pairs.append({"code_a": first["code"], "code_b": second["code"], "correlation": corr, "samples": samples})
    high_pairs = [pair for pair in correlation_pairs if pair["correlation"] >= settings.PORTFOLIO_HIGH_CORRELATION_THRESHOLD]
    max_by_code: dict[str, float | None] = {code: None for code in codes}
    for pair in correlation_pairs:
        for code in (pair["code_a"], pair["code_b"]):
            current = max_by_code[code]
            max_by_code[code] = pair["correlation"] if current is None else max(current, pair["correlation"])
    weights = {row["code"]: _number(row.get("weight")) for row in positions}
    weights = {code: weight for code, weight in weights.items() if weight is not None and weight > 0}
    weighted_sum = weighted_denominator = 0.0
    for pair in correlation_pairs:
        factor = weights.get(pair["code_a"], 0.0) * weights.get(pair["code_b"], 0.0)
        weighted_sum += pair["correlation"] * factor
        weighted_denominator += factor
    weighted_average = weighted_sum / weighted_denominator if weighted_denominator > 0 else None
    average = mean([pair["correlation"] for pair in correlation_pairs]) if correlation_pairs else None
    max_pairwise = max((pair["correlation"] for pair in correlation_pairs), default=None)
    common_dates = set.intersection(*(set(returns[code]) for code in weights)) if weights and all(returns.get(code) for code in weights) else set()
    portfolio_returns: list[float] = []
    if len(common_dates) >= settings.PORTFOLIO_CORRELATION_MIN_SAMPLES:
        total_weight = sum(weights.values())
        for day in sorted(common_dates):
            portfolio_returns.append(sum(weights[code] * returns[code][day] for code in weights) / total_weight)
    portfolio_vol_20 = stdev(portfolio_returns[-20:]) * math.sqrt(settings.PORTFOLIO_TRADING_DAYS_PER_YEAR) if len(portfolio_returns) >= 20 else None
    portfolio_vol_60 = stdev(portfolio_returns[-60:]) * math.sqrt(settings.PORTFOLIO_TRADING_DAYS_PER_YEAR) if len(portfolio_returns) >= 60 else None
    risk_contrib: dict[str, float | None] = {code: None for code in codes}
    if len(common_dates) >= settings.PORTFOLIO_CORRELATION_MIN_SAMPLES and len(weights) >= 1 and len(portfolio_returns) >= 2:
        ordered = list(weights)
        total_weight = sum(weights.values())
        vector_weights = [weights[code] / total_weight for code in ordered]
        series = [[returns[code][day] for day in sorted(common_dates)] for code in ordered]
        covariance = [[_covariance(left, right) for right in series] for left in series]
        cov_w = [sum(covariance[i][j] * vector_weights[j] for j in range(len(ordered))) for i in range(len(ordered))]
        contributions = [vector_weights[i] * cov_w[i] for i in range(len(ordered))]
        variance = sum(contributions)
        if variance > 0:
            for code, contribution in zip(ordered, contributions):
                risk_contrib[code] = contribution / variance
    for position in positions:
        code = position["code"]
        position["volatility_20"] = _volatility(returns.get(code, {}), 20)
        position["volatility_60"] = _volatility(returns.get(code, {}), 60)
        position["history_coverage"] = len(returns.get(code, {}))
        position["max_correlation_with_other"] = max_by_code.get(code)
        position["risk_contribution_ratio"] = risk_contrib.get(code)
        score, breakdown, available_weight, keep_confidence = _keep_score(
            position,
            closes_by_code.get(code, []),
            max_by_code.get(code),
            benchmark_return_20,
        )
        position["keep_score"] = score
        position["keep_score_breakdown"] = breakdown
        position["keep_score_available_weight"] = available_weight
        position["keep_score_confidence"] = keep_confidence
        position["execution_availability"] = breakdown.get("execution_availability")
    concentration_weights = sorted((float(row["weight"]) for row in positions if row.get("weight") is not None), reverse=True)
    history_coverage = sum(1 for row in positions if len(returns.get(row["code"], {})) >= settings.PORTFOLIO_CORRELATION_MIN_SAMPLES) / len(positions) if positions else 1.0
    confidence = 100.0 * (
        0.40 * float(state.get("quote_coverage") or 0.0)
        + 0.30 * history_coverage
        + 0.15 * float(state.get("classification_coverage") or 0.0)
        + 0.15
    )
    quality = str(state.get("quality_status") or "DEGRADED")
    if history_coverage < 1.0 and quality == "VALID":
        quality = "DEGRADED"
    return {
        "top1_weight": sum(concentration_weights[:1]) if concentration_weights else 0.0,
        "top3_weight": sum(concentration_weights[:3]) if concentration_weights else 0.0,
        "top5_weight": sum(concentration_weights[:5]) if concentration_weights else 0.0,
        "hhi": sum(weight * weight for weight in concentration_weights),
        "portfolio_vol_20": portfolio_vol_20,
        "portfolio_vol_60": portfolio_vol_60,
        "weighted_average_correlation": weighted_average,
        "average_pairwise_correlation": average,
        "max_pairwise_correlation": max_pairwise,
        "high_correlation_pairs": high_pairs,
        "correlation_pairs": correlation_pairs,
        "correlation_clusters": _clusters(codes, high_pairs),
        "etf_lookthrough_available": False,
        "positions": positions,
        "history_coverage": history_coverage,
        "confidence": round(confidence, 2),
        "quality_status": quality,
        "risk_contribution_available": any(value is not None for value in risk_contrib.values()),
        "industry_exposure": None,
        "industry_exposure_available": False,
        "benchmark": {
            "name": "all_a_median_index",
            "return_20": benchmark_return_20,
            "available": benchmark_return_20 is not None,
        },
    }


def _covariance(first: list[float], second: list[float]) -> float:
    if len(first) < 2 or len(first) != len(second):
        return 0.0
    left_mean, right_mean = mean(first), mean(second)
    return sum((a - left_mean) * (b - right_mean) for a, b in zip(first, second)) / (len(first) - 1)


__all__ = ["build_portfolio_state", "calculate_risk_metrics", "latest_confirmed_snapshot"]
