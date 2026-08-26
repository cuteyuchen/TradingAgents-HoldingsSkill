"""Candidate universe construction from server-owned local facts."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
from typing import Any

from ..market.codes import normalize_security_code
from .config import CandidateConfig, DEFAULT_CONFIG


@dataclass(frozen=True)
class UniverseExclusion:
    code: str
    reason_codes: tuple[str, ...]
    name: str | None = None


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).replace("/", "-")[:10])
    except ValueError:
        return None


def _code(value: Any) -> str:
    return normalize_security_code(value)


def _quote_map(quotes: Any) -> dict[str, dict[str, Any]]:
    if quotes is None:
        return {}
    if isinstance(quotes, Mapping) and "quotes" in quotes:
        quotes = quotes.get("quotes") or []
    if isinstance(quotes, Mapping):
        output: dict[str, dict[str, Any]] = {}
        for key, value in quotes.items():
            if isinstance(value, Mapping):
                item = dict(value)
                item.setdefault("code", key)
                output[_code(item.get("code"))] = item
        return {key: value for key, value in output.items() if key}
    output = {}
    for value in quotes if isinstance(quotes, Iterable) and not isinstance(quotes, (str, bytes)) else []:
        if isinstance(value, Mapping):
            item = dict(value)
            item_code = _code(item.get("code") or item.get("symbol"))
            if item_code:
                item["code"] = item_code
                output[item_code] = item
    return output


def _bars_by_code(bars: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(bars, Mapping):
        output = {}
        for key, values in bars.items():
            code = _code(key)
            if not code:
                continue
            output[code] = [dict(row) for row in values or [] if isinstance(row, Mapping)]
        return output
    output: dict[str, list[dict[str, Any]]] = {}
    for row in bars if isinstance(bars, Iterable) and not isinstance(bars, (str, bytes)) else []:
        if not isinstance(row, Mapping):
            continue
        code = _code(row.get("code") or row.get("symbol"))
        if code:
            output.setdefault(code, []).append(dict(row))
    return output


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _median_amount(rows: list[dict[str, Any]]) -> float | None:
    values = [_number(row.get("amount")) for row in rows[-20:]]
    values = [value for value in values if value is not None and value > 0]
    return median(values) if values else None


def _percentile(value: float | None, values: list[float]) -> float | None:
    if value is None or not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return 100.0
    below = sum(item < value for item in ordered)
    equal = sum(item == value for item in ordered)
    return 100.0 * (below + max(1, equal) / 2) / len(ordered)


def build_candidate_universe(
    securities: Iterable[Any],
    *,
    quotes: Any = None,
    bars: Any = None,
    held_codes: Iterable[Any] = (),
    as_of: date | datetime | str | None = None,
    trading_days_by_code: Mapping[str, int] | None = None,
    config: CandidateConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Return eligible stock/ETF rows and deterministic exclusion diagnostics.

    The function is intentionally pure.  The service supplies batch-loaded
    SecurityMaster, quote, daily-bar, and TradingCalendar facts.
    """

    cutoff = _as_date(as_of) if as_of is not None else None
    quote_map = _quote_map(quotes)
    bar_map = _bars_by_code(bars)
    held = {_code(value) for value in held_codes if _code(value)}
    trading_days = {_code(key): int(value) for key, value in (trading_days_by_code or {}).items() if _code(key)}
    securities_list = list(securities)
    exclusions: list[UniverseExclusion] = []
    basic: list[dict[str, Any]] = []

    def exclude(security: Any, *reasons: str) -> None:
        code = _code(_get(security, "code") or _get(security, "symbol"))
        exclusions.append(UniverseExclusion(code, tuple(dict.fromkeys(reasons)), _get(security, "name")))

    for security in securities_list:
        code = _code(_get(security, "code") or _get(security, "symbol"))
        name = _get(security, "name")
        security_type = str(_get(security, "security_type") or "").upper()
        exchange = str(_get(security, "exchange") or "").upper()
        status = str(_get(security, "status") or "ACTIVE").upper()
        reasons: list[str] = []
        if not code:
            continue
        if security_type not in {"STOCK", "ETF"}:
            reasons.append("UNIVERSE_SECURITY_TYPE")
        if security_type == "STOCK" and exchange not in {"SSE", "SZSE"}:
            reasons.append("UNIVERSE_BSE")
        if security_type == "ETF" and exchange not in {"SSE", "SZSE", "BSE"}:
            reasons.append("UNIVERSE_EXCHANGE")
        if status not in {"ACTIVE", "LISTED"}:
            reasons.append("UNIVERSE_INACTIVE")
        if bool(_get(security, "is_suspended")):
            reasons.append("UNIVERSE_SUSPENDED")
        security_name = str(name or "").strip().upper()
        if bool(_get(security, "is_st")) or security_name.startswith(("ST", "*ST")):
            reasons.append("UNIVERSE_ST")
        delisting_date = _as_date(_get(security, "delisting_date"))
        if cutoff is not None and delisting_date is not None and delisting_date <= cutoff:
            reasons.append("UNIVERSE_DELISTED")
        if code in held:
            reasons.append("UNIVERSE_HELD")

        rows = sorted(bar_map.get(code, []), key=lambda row: str(row.get("trade_date") or row.get("date") or ""))
        quote = quote_map.get(code)
        if quote is None and rows:
            latest = rows[-1]
            quote = {
                "code": code,
                "price": latest.get("close"),
                "prev_close": latest.get("prev_close"),
                "amount": latest.get("amount"),
                "volume": latest.get("volume"),
                "turnover_rate": latest.get("turnover_rate"),
                "quality_status": "DEGRADED",
                "provider": latest.get("provider") or "daily_bar_cache",
                "available_at": latest.get("available_at"),
                "source": "daily_bar_cache_close_proxy",
            }
        if quote is None:
            reasons.append("QUOTE_MISSING")
        else:
            quote = dict(quote)
            quality = str(quote.get("quality_status") or quote.get("quality") or "VALID").upper()
            if quality not in {"VALID", "DEGRADED"}:
                reasons.append(f"QUOTE_{quality}")
            price = _number(quote.get("price") or quote.get("close"))
            prev_close = _number(quote.get("prev_close") or quote.get("previous_close"))
            if price is None or price <= 0:
                reasons.append("PRICE_ANOMALY")
            if prev_close is not None and prev_close <= 0:
                reasons.append("PRICE_ANOMALY")
            if bool(quote.get("is_suspended")):
                reasons.append("UNIVERSE_SUSPENDED")
            if str(quote.get("trade_status") or "").upper() in {"SUSPENDED", "停牌"}:
                reasons.append("UNIVERSE_SUSPENDED")
            quote["quality_status"] = quality
            quote["price"] = price
            quote["prev_close"] = prev_close

        if len(rows) < config.min_history_bars:
            reasons.append("HISTORY_INSUFFICIENT")
        listing_date = _as_date(_get(security, "listing_date"))
        if listing_date is not None and code not in trading_days:
            reasons.append("TRADING_CALENDAR_MISSING")
        history_days = trading_days.get(code, len(rows))
        if history_days < config.min_listing_trading_days:
            reasons.append("UNIVERSE_NEW_LISTING")
        if reasons:
            exclude(security, *reasons)
            continue
        metadata = dict(_get(security, "raw_metadata_json") or _get(security, "metadata") or {})
        quote_metadata = dict((quote or {}).get("metadata") or {}) if quote else {}
        metadata = {**metadata, **quote_metadata}
        basic.append(
            {
                "code": code,
                "name": name,
                "security": security,
                "security_type": security_type,
                "etf_category": _get(security, "etf_category"),
                "exchange": exchange,
                "quote": quote,
                "bars": rows,
                "history_days": history_days,
                "median_amount": _median_amount(rows),
                "metadata": metadata,
                "limit_up": bool(quote and (quote.get("is_limit_up") or metadata.get("is_limit_up"))),
                "limit_down": bool(quote and (quote.get("is_limit_down") or metadata.get("is_limit_down"))),
            }
        )

    # Apply percentile liquidity filtering only when the cross-section is large
    # enough to make the percentile meaningful.  Small deterministic fixtures
    # remain testable without inventing a 99th percentile from five rows.
    amount_values = [row["median_amount"] for row in basic if row["median_amount"] is not None]
    eligible: list[dict[str, Any]] = []
    for row in basic:
        amount_percentile = _percentile(row["median_amount"], amount_values)
        row["liquidity_percentile"] = amount_percentile
        liquidity_min = (
            config.stock_liquidity_percentile_min
            if row["security_type"] == "STOCK"
            else config.etf_liquidity_percentile_min
        )
        reasons: list[str] = []
        if row["security_type"] == "ETF" and row["median_amount"] is not None and row["median_amount"] < config.etf_min_median_amount:
            reasons.append("UNIVERSE_LIQUIDITY_LOW")
        if len(amount_values) >= config.percentile_min_samples and amount_percentile is not None and amount_percentile < liquidity_min:
            reasons.append("UNIVERSE_LIQUIDITY_LOW")
        if row.get("limit_down"):
            reasons.append("PRICE_LIMIT_DOWN")
        if reasons:
            exclude(row["security"], *reasons)
        else:
            eligible.append(row)

    counts = Counter(reason for item in exclusions for reason in item.reason_codes)
    return {
        "universe": eligible,
        "eligible": eligible,
        "exclusions": [
            {"code": item.code, "name": item.name, "reason_codes": list(item.reason_codes)}
            for item in exclusions
        ],
        "exclusion_counts": dict(sorted(counts.items())),
        "universe_count": len(securities_list),
        "eligible_count": len(eligible),
        "quote_coverage": sum(1 for row in eligible if row.get("quote")) / len(eligible) if eligible else 0.0,
        "bar_coverage": sum(1 for row in eligible if len(row.get("bars") or []) >= config.min_history_bars) / len(eligible) if eligible else 0.0,
    }


__all__ = ["UniverseExclusion", "build_candidate_universe"]
