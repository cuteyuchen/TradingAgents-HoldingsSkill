"""Validation, freshness, and cross-provider quote comparison."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import DataQualityStatus, NormalizedQuote, QuoteComparison, QuoteValidation


DEFAULT_QUOTE_FRESHNESS_SECONDS = 90.0
DEFAULT_CONFLICT_THRESHOLD_PCT = 0.5


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def freshness_seconds(quote: NormalizedQuote, now: datetime | None = None) -> float | None:
    """Return age in seconds using source timestamp, then fetch timestamp."""

    reference = quote.source_timestamp or quote.fetched_at
    current = _as_utc(now) or datetime.now(UTC)
    reference = _as_utc(reference)
    if reference is None:
        return None
    return max(0.0, (current - reference).total_seconds())


def is_final_close_timestamp(
    source_timestamp: datetime | None,
    *,
    session_trade_date: date | None,
    now: datetime | None = None,
    close_time: time = time(15, 0),
) -> bool:
    """Return whether a quote is the same-day official close snapshot.

    The exception is deliberately bounded to the same Shanghai calendar date.
    A Friday close therefore cannot remain fresh throughout the weekend or the
    next trading session.
    """

    if source_timestamp is None or session_trade_date is None:
        return False
    source_local = _as_utc(source_timestamp).astimezone(ZoneInfo("Asia/Shanghai"))
    current_local = (_as_utc(now) or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Shanghai"))
    return (
        source_local.date() == session_trade_date
        and current_local.date() == session_trade_date
        and source_local.time() >= close_time
    )


def is_stale(
    quote: NormalizedQuote,
    max_age_seconds: float = DEFAULT_QUOTE_FRESHNESS_SECONDS,
    now: datetime | None = None,
    session_trade_date: date | None = None,
) -> bool:
    age = freshness_seconds(quote, now)
    if is_final_close_timestamp(
        quote.source_timestamp,
        session_trade_date=session_trade_date,
        now=now,
    ):
        return False
    if quote.quality_status == DataQualityStatus.STALE:
        return True
    return age is not None and age > max_age_seconds


def validate_normalized_quote(
    quote: NormalizedQuote,
    *,
    now: datetime | None = None,
    max_age_seconds: float | None = DEFAULT_QUOTE_FRESHNESS_SECONDS,
    session_trade_date: date | None = None,
) -> QuoteValidation:
    """Apply provider-independent sanity checks without inventing missing data."""

    errors: list[str] = []
    if not quote.code or len(quote.code) != 6 or not quote.code.isdigit():
        errors.append("invalid_code")
    numeric_nonnegative = {
        "price": quote.price,
        "prev_close": quote.prev_close,
        "open": quote.open,
        "high": quote.high,
        "low": quote.low,
        "volume": quote.volume,
        "amount": quote.amount,
        "turnover_rate": quote.turnover_rate,
        "bid": quote.bid,
        "ask": quote.ask,
    }
    for name, value in numeric_nonnegative.items():
        if value is not None and value < 0:
            errors.append(f"negative_{name}")
    if quote.high is not None and quote.low is not None and quote.high < quote.low:
        errors.append("high_below_low")
    if quote.trade_date is not None:
        current = _as_utc(now) or datetime.now(UTC)
        today = current.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if quote.trade_date > today + timedelta(days=1):
            errors.append("future_trade_date")
    if quote.source_timestamp is not None:
        current = _as_utc(now) or datetime.now(UTC)
        if _as_utc(quote.source_timestamp) and _as_utc(quote.source_timestamp) > current + timedelta(minutes=5):
            errors.append("future_source_timestamp")

    # A zero price is a missing quote for a known suspended instrument; without
    # suspension context it is invalid rather than a fabricated market move.
    if quote.price is None:
        errors.append("missing_price")
    elif quote.price == 0:
        errors.append("zero_price_suspended" if quote.is_suspended else "zero_price")

    if errors:
        hard_errors = [error for error in errors if error not in {"missing_price", "zero_price_suspended"}]
        if not hard_errors and errors in (["zero_price_suspended"], ["missing_price"]):
            return QuoteValidation(DataQualityStatus.MISSING, tuple(errors))
        return QuoteValidation(DataQualityStatus.INVALID, tuple(errors))

    status = quote.quality_status
    if status in {DataQualityStatus.INVALID, DataQualityStatus.MISSING, DataQualityStatus.CONFLICT}:
        return QuoteValidation(status, tuple(errors))
    if max_age_seconds is not None and is_stale(
        quote,
        max_age_seconds=max_age_seconds,
        now=now,
        session_trade_date=session_trade_date,
    ):
        return QuoteValidation(DataQualityStatus.STALE, ("quote_stale",))

    optional_missing = [
        name
        for name, value in (("prev_close", quote.prev_close), ("open", quote.open), ("high", quote.high), ("low", quote.low), ("volume", quote.volume), ("amount", quote.amount))
        if value is None
    ]
    if optional_missing:
        return QuoteValidation(DataQualityStatus.DEGRADED, tuple(f"missing_{name}" for name in optional_missing))
    return QuoteValidation(DataQualityStatus.VALID, ())


def validate_quote(*args: Any, **kwargs: Any) -> QuoteValidation:
    """Compatibility alias for :func:`validate_normalized_quote`."""

    return validate_normalized_quote(*args, **kwargs)


def _difference_pct(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    reference = (abs(left) + abs(right)) / 2
    if reference == 0:
        return 0.0 if left == right else 100.0
    return abs(left - right) / reference * 100


def compare_quotes(
    quote_a: NormalizedQuote,
    quote_b: NormalizedQuote,
    *,
    price_conflict_threshold_pct: float = DEFAULT_CONFLICT_THRESHOLD_PCT,
    prev_close_conflict_threshold_pct: float | None = None,
) -> QuoteComparison:
    """Compare semantically compatible fields from two providers."""

    if prev_close_conflict_threshold_pct is None:
        prev_close_conflict_threshold_pct = price_conflict_threshold_pct
    errors: list[str] = []
    if quote_a.code != quote_b.code:
        errors.append("code_mismatch")
    price_diff = _difference_pct(quote_a.price, quote_b.price)
    prev_diff = _difference_pct(quote_a.prev_close, quote_b.prev_close)
    prev_conflict = prev_diff is not None and prev_diff > prev_close_conflict_threshold_pct
    status_a = str(quote_a.metadata.get("trade_status") or quote_a.metadata.get("status") or "").upper()
    status_b = str(quote_b.metadata.get("trade_status") or quote_b.metadata.get("status") or "").upper()
    trade_conflict = quote_a.is_suspended != quote_b.is_suspended or (
        status_a and status_b and status_a != status_b
    ) or (
        quote_a.trade_date is not None and quote_b.trade_date is not None and quote_a.trade_date != quote_b.trade_date
    )
    if price_diff is None:
        errors.append("price_missing")
    if prev_conflict:
        errors.append("prev_close_conflict")
    if trade_conflict:
        errors.append("trade_status_conflict")
    if price_diff is not None and price_diff > price_conflict_threshold_pct:
        errors.append("price_conflict")

    statuses = {quote_a.quality_status, quote_b.quality_status}
    if errors and any(item in errors for item in ("price_conflict", "prev_close_conflict", "trade_status_conflict", "code_mismatch")):
        status = DataQualityStatus.CONFLICT
    elif DataQualityStatus.INVALID in statuses:
        status = DataQualityStatus.INVALID
    elif DataQualityStatus.MISSING in statuses or price_diff is None:
        status = DataQualityStatus.MISSING
    elif DataQualityStatus.STALE in statuses:
        status = DataQualityStatus.STALE
    elif DataQualityStatus.DEGRADED in statuses:
        status = DataQualityStatus.DEGRADED
    else:
        status = DataQualityStatus.VALID
    return QuoteComparison(price_diff, prev_diff, prev_conflict, trade_conflict, status, tuple(errors))


compare_quote_sources = compare_quotes
