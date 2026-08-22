"""Provider contracts and the in-memory quote snapshot value object."""
from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import date, datetime
from collections.abc import Iterable, Mapping
from typing import Any

from ..codes import normalize_security_code
from ..models import DataQualityStatus, NormalizedQuote, QuoteSnapshot
from ..quality import validate_quote


# Keep the historical provider import path while using one canonical model.
MarketQuoteSnapshot = QuoteSnapshot


class SecurityProvider(ABC):
    @abstractmethod
    def list_securities(self, *, market: str = "CN") -> list[dict[str, Any]]:
        raise NotImplementedError


class CalendarProvider(ABC):
    @abstractmethod
    def get_calendar(self, start: date, end: date, *, market: str = "CN") -> list[dict[str, Any]]:
        raise NotImplementedError


class KLineProvider(ABC):
    @abstractmethod
    def get_kline(self, code: str, *, limit: int = 30) -> list[dict[str, Any]]:
        raise NotImplementedError


class QuoteProvider(ABC):
    name = "unknown"

    @abstractmethod
    def get_quotes(self, codes: Iterable[str]) -> dict[str, NormalizedQuote]:
        raise NotImplementedError

    def get_quote(self, code: str) -> NormalizedQuote | None:
        return self.get_quotes([code]).get(normalize_security_code(code))

    def get_all_a_share_quotes(self, universe: Iterable[str]) -> dict[str, NormalizedQuote]:
        """Batch contract; providers must not turn this into one HTTP call per code."""
        return self.get_quotes(universe)


def build_quote_snapshot(
    quotes: Iterable[NormalizedQuote | Mapping[str, Any]],
    *,
    expected_count: int,
    provider: str,
    fallback_level: int = 0,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    trade_date: date | None = None,
    errors: list[Any] | None = None,
    snapshot_key: str | None = None,
    requested_codes: Iterable[str] | None = None,
) -> MarketQuoteSnapshot:
    started = started_at or datetime.now().astimezone()
    completed = completed_at or datetime.now(started.tzinfo)
    unique: dict[str, NormalizedQuote] = {}
    local_errors = list(errors or [])
    for raw_quote in quotes:
        if isinstance(raw_quote, NormalizedQuote):
            quote = deepcopy(raw_quote)
        elif isinstance(raw_quote, Mapping):
            quote = NormalizedQuote.from_mapping(dict(raw_quote), provider=provider)
        else:
            local_errors.append({"error_code": "invalid_quote", "message": "unsupported quote value"})
            continue
        code = normalize_security_code(quote.code)
        if not code:
            local_errors.append({"code": quote.code, "error_code": "invalid_code", "message": "quote code is invalid"})
            continue
        if code in unique:
            local_errors.append({"code": code, "error_code": "duplicate_quote", "message": "duplicate quote filtered"})
            continue
        quote.code = code
        validation = validate_quote(quote)
        # Provider-declared CONFLICT/INVALID/STALE must not be silently
        # upgraded; otherwise apply deterministic validation to default VALID
        # fixture values as well.
        if quote.quality_status in {DataQualityStatus.VALID, DataQualityStatus.MISSING}:
            quote.quality_status = validation.status
        elif quote.quality_status == DataQualityStatus.DEGRADED and validation.status in {
            DataQualityStatus.INVALID,
            DataQualityStatus.MISSING,
        }:
            quote.quality_status = validation.status
        if validation.errors:
            quote.errors.extend(error for error in validation.errors if error not in quote.errors)
        if quote.quality_status == DataQualityStatus.INVALID:
            local_errors.append(
                {
                    "code": code,
                    "error_code": "invalid_quote",
                    "message": ",".join(quote.errors) or "quote failed validation",
                }
            )
            continue
        unique[code] = quote
    valid = [
        quote
        for quote in unique.values()
        if quote.quality_status in {DataQualityStatus.VALID, DataQualityStatus.DEGRADED}
    ]
    expected = max(0, expected_count)
    coverage = min(len(valid) / expected, 1.0) if expected else 0.0
    requested = list(dict.fromkeys(normalize_security_code(code) for code in (requested_codes or []) if normalize_security_code(code)))
    valid_codes = {quote.code for quote in valid}
    missing_codes = [code for code in requested if code not in valid_codes]
    missing_codes.extend(
        code for code in unique if code not in valid_codes and code not in missing_codes
    )
    if expected == 0 or not unique or not valid:
        quality = DataQualityStatus.MISSING
    elif any(quote.quality_status == DataQualityStatus.CONFLICT for quote in unique.values()):
        quality = DataQualityStatus.CONFLICT
    elif any(quote.quality_status == DataQualityStatus.STALE for quote in unique.values()) and len(valid) >= expected:
        quality = DataQualityStatus.STALE
    elif len(valid) < expected or local_errors or any(quote.quality_status == DataQualityStatus.DEGRADED for quote in valid):
        quality = DataQualityStatus.DEGRADED
    else:
        quality = DataQualityStatus.VALID
    return QuoteSnapshot(
        snapshot_key=snapshot_key,
        market="CN",
        started_at=started,
        completed_at=completed,
        trade_date=trade_date,
        provider=provider,
        fallback_level=fallback_level,
        expected_count=expected,
        received_count=len(valid),
        coverage_ratio=round(coverage, 6),
        quotes=list(unique.values()),
        errors=local_errors,
        quality_status=quality,
        missing_codes=missing_codes,
    )
