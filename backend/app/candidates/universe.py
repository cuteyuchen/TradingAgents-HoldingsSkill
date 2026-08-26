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


def _is_quote_proxy(quote: Mapping[str, Any] | None) -> bool:
    if not quote:
        return False
    return bool(
        quote.get("quote_is_proxy")
        or str(quote.get("source") or "").strip().lower() == "daily_bar_cache_close_proxy"
    )


def _limit_up(
    quote: Mapping[str, Any] | None,
    *,
    code: str,
    name: Any,
    security_type: str,
    metadata: Mapping[str, Any],
) -> bool:
    """Infer a current limit-up state when the provider omits a flag.

    A daily-bar close proxy is deliberately excluded: it cannot establish the
    current session's limit state.
    """

    if not quote or _is_quote_proxy(quote):
        return False
    if bool(quote.get("is_limit_up") or metadata.get("is_limit_up")):
        return True
    previous = _number(quote.get("prev_close") or quote.get("previous_close"))
    price = _number(quote.get("price") or quote.get("close"))
    if previous is None or previous <= 0 or price is None or price <= 0:
        return False
    explicit_rate = _number(
        quote.get("limit_up_pct")
        or quote.get("price_limit_pct")
        or metadata.get("limit_up_pct")
        or metadata.get("price_limit_pct")
    )
    if explicit_rate is not None and explicit_rate <= 1:
        explicit_rate *= 100.0
    if explicit_rate is None:
        upper_name = str(name or "").strip().upper()
        if security_type == "STOCK" and (upper_name.startswith("ST") or upper_name.startswith("*ST")):
            explicit_rate = 5.0
        elif security_type == "STOCK" and code.startswith(("300", "301", "688", "689")):
            explicit_rate = 20.0
        else:
            explicit_rate = 10.0
    pct_change = _number(quote.get("pct_change") or quote.get("change_percent"))
    if pct_change is not None and abs(pct_change) < 1.0:
        pct_change *= 100.0
    price_change_pct = (price / previous - 1.0) * 100.0
    return max(value for value in (pct_change, price_change_pct) if value is not None) >= explicit_rate - 0.25


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
    structural_rows: list[Any] = []
    basic: list[dict[str, Any]] = []

    structural_reason_codes = {
        "UNIVERSE_SECURITY_TYPE",
        "UNIVERSE_BSE",
        "UNIVERSE_EXCHANGE",
        "UNIVERSE_INACTIVE",
        "UNIVERSE_SUSPENDED",
        "UNIVERSE_ST",
        "UNIVERSE_DELISTED",
        "UNIVERSE_HELD",
        "UNIVERSE_NEW_LISTING",
    }

    def exclude(security: Any, *reasons: str) -> None:
        code = _code(_get(security, "code") or _get(security, "symbol"))
        exclusions.append(UniverseExclusion(code, tuple(dict.fromkeys(reasons)), _get(security, "name")))

    for security in securities_list:
        code = _code(_get(security, "code") or _get(security, "symbol"))
        name = _get(security, "name")
        security_type = str(_get(security, "security_type") or "").upper()
        exchange = str(_get(security, "exchange") or "").upper()
        status = str(_get(security, "status") or "ACTIVE").upper()
        structural_reasons: list[str] = []
        data_reasons: list[str] = []
        if not code:
            continue
        if security_type not in {"STOCK", "ETF"}:
            structural_reasons.append("UNIVERSE_SECURITY_TYPE")
        if security_type == "STOCK" and exchange not in {"SSE", "SZSE"}:
            structural_reasons.append("UNIVERSE_BSE")
        if security_type == "ETF" and exchange not in {"SSE", "SZSE", "BSE"}:
            structural_reasons.append("UNIVERSE_EXCHANGE")
        if status not in {"ACTIVE", "LISTED"}:
            structural_reasons.append("UNIVERSE_INACTIVE")
        if bool(_get(security, "is_suspended")):
            structural_reasons.append("UNIVERSE_SUSPENDED")
        security_name = str(name or "").strip().upper()
        if bool(_get(security, "is_st")) or security_name.startswith(("ST", "*ST")):
            structural_reasons.append("UNIVERSE_ST")
        delisting_date = _as_date(_get(security, "delisting_date"))
        if cutoff is not None and delisting_date is not None and delisting_date <= cutoff:
            structural_reasons.append("UNIVERSE_DELISTED")
        if code in held:
            structural_reasons.append("UNIVERSE_HELD")

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
            data_reasons.append("QUOTE_MISSING")
        else:
            quote = dict(quote)
            quality = str(quote.get("quality_status") or quote.get("quality") or "VALID").upper()
            if quality not in {"VALID", "DEGRADED"}:
                data_reasons.append(f"QUOTE_{quality}")
            price = _number(quote.get("price") or quote.get("close"))
            prev_close = _number(quote.get("prev_close") or quote.get("previous_close"))
            if price is None or price <= 0:
                data_reasons.append("PRICE_ANOMALY")
            if prev_close is not None and prev_close <= 0:
                data_reasons.append("PRICE_ANOMALY")
            if bool(quote.get("is_suspended")):
                structural_reasons.append("UNIVERSE_SUSPENDED")
            if str(quote.get("trade_status") or "").upper() in {"SUSPENDED", "停牌"}:
                structural_reasons.append("UNIVERSE_SUSPENDED")
            quote["quality_status"] = quality
            quote["price"] = price
            quote["prev_close"] = prev_close

        if len(rows) < config.min_history_bars:
            data_reasons.append("HISTORY_INSUFFICIENT")
        listing_date = _as_date(_get(security, "listing_date"))
        if listing_date is not None and code not in trading_days:
            data_reasons.append("TRADING_CALENDAR_MISSING")
        history_days = trading_days.get(code, len(rows))
        if history_days < config.min_listing_trading_days:
            structural_reasons.append("UNIVERSE_NEW_LISTING")

        if structural_reasons:
            exclude(security, *structural_reasons, *data_reasons)
            continue
        structural_rows.append(security)
        if data_reasons:
            # Data-incomplete rows stay in the structural denominator for
            # coverage, but cannot enter the scoreable candidate set.
            exclude(security, *data_reasons)
            continue
        raw_metadata = _get(security, "raw_metadata_json")
        if not isinstance(raw_metadata, Mapping):
            raw_metadata = _get(security, "metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        raw_quote_metadata = (quote or {}).get("metadata") if quote else None
        quote_metadata = dict(raw_quote_metadata) if isinstance(raw_quote_metadata, Mapping) else {}
        metadata = {**metadata, **quote_metadata}
        quote_is_proxy = _is_quote_proxy(quote)
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
                "quote_is_proxy": quote_is_proxy,
                "quote_provenance": (
                    "daily_bar_cache_close_proxy"
                    if quote_is_proxy
                    else quote.get("provider") if quote else None
                ),
                "limit_up": _limit_up(
                    quote,
                    code=code,
                    name=name,
                    security_type=security_type,
                    metadata=metadata,
                ),
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
    structural_count = len(structural_rows)
    quote_ready_count = sum(
        1
        for security in structural_rows
        if (
            (quote := quote_map.get(_code(_get(security, "code") or _get(security, "symbol"))))
            and str(quote.get("quality_status") or quote.get("quality") or "VALID").upper() in {"VALID", "DEGRADED"}
            and _number(quote.get("price") or quote.get("close")) is not None
            and _number(quote.get("price") or quote.get("close")) > 0
            and not bool(quote.get("quote_is_proxy") or quote.get("source") == "daily_bar_cache_close_proxy")
        )
    )
    bar_ready_count = sum(
        1
        for security in structural_rows
        if len(bar_map.get(_code(_get(security, "code") or _get(security, "symbol")), [])) >= config.min_history_bars
    )
    data_exclusion_counts = Counter(
        reason
        for item in exclusions
        for reason in item.reason_codes
        if reason not in structural_reason_codes
    )
    return {
        "universe": eligible,
        "eligible": eligible,
        "exclusions": [
            {"code": item.code, "name": item.name, "reason_codes": list(item.reason_codes)}
            for item in exclusions
        ],
        "exclusion_counts": dict(sorted(counts.items())),
        "universe_count": len(securities_list),
        "structural_candidate_count": structural_count,
        "quote_ready_count": quote_ready_count,
        "bar_ready_count": bar_ready_count,
        "eligible_count": len(eligible),
        "quote_coverage": quote_ready_count / structural_count if structural_count else 0.0,
        "bar_coverage": bar_ready_count / structural_count if structural_count else 0.0,
        "data_exclusion_counts": dict(sorted(data_exclusion_counts.items())),
    }


__all__ = ["UniverseExclusion", "build_candidate_universe"]
