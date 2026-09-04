"""Provider contracts and the in-memory quote snapshot value object."""
from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import date, datetime
from collections.abc import Iterable, Mapping
from typing import Any

from ..codes import normalize_security_code
from ..models import DataQualityStatus, NormalizedQuote, QuoteSnapshot
from ..quality import DEFAULT_QUOTE_FRESHNESS_SECONDS, is_final_close_timestamp, validate_quote


# Keep the historical provider import path while using one canonical model.
MarketQuoteSnapshot = QuoteSnapshot


def apply_quote_validation(
    quote: NormalizedQuote,
    *,
    now: datetime | None = None,
    max_age_seconds: float | None = DEFAULT_QUOTE_FRESHNESS_SECONDS,
    session_trade_date: date | None = None,
) -> NormalizedQuote:
    """Apply the canonical deterministic quality rules to one quote in place."""

    validation = validate_quote(
        quote,
        now=now,
        max_age_seconds=max_age_seconds,
        session_trade_date=session_trade_date,
    )
    if quote.quality_status in {DataQualityStatus.VALID, DataQualityStatus.DEGRADED} and validation.status in {
        DataQualityStatus.STALE,
        DataQualityStatus.INVALID,
        DataQualityStatus.MISSING,
        DataQualityStatus.CONFLICT,
    }:
        quote.quality_status = validation.status
    elif quote.quality_status == DataQualityStatus.STALE and (
        validation.status in {
            DataQualityStatus.INVALID,
            DataQualityStatus.MISSING,
            DataQualityStatus.CONFLICT,
        }
        or (
            validation.status in {DataQualityStatus.VALID, DataQualityStatus.DEGRADED}
            and is_final_close_timestamp(
                quote.source_timestamp,
                session_trade_date=session_trade_date,
                now=now,
            )
        )
    ):
        quote.quality_status = validation.status
    if validation.errors:
        quote.errors.extend(error for error in validation.errors if error not in quote.errors)
    return quote


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
    provider: str | None = None,
    fallback_level: int = 0,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    trade_date: date | None = None,
    errors: list[Any] | None = None,
    snapshot_key: str | None = None,
    requested_codes: Iterable[str] | None = None,
    requested_route: str | None = None,
    max_age_seconds: float | None = DEFAULT_QUOTE_FRESHNESS_SECONDS,
    metadata: Mapping[str, Any] | None = None,
) -> MarketQuoteSnapshot:
    started = started_at or datetime.now().astimezone()
    completed = completed_at or datetime.now(started.tzinfo)
    requested = list(
        dict.fromkeys(
            code
            for value in (requested_codes or [])
            if (code := normalize_security_code(value))
        )
    )
    requested_set = set(requested)
    unique: dict[str, NormalizedQuote] = {}
    local_errors = list(errors or [])
    for raw_quote in quotes:
        if isinstance(raw_quote, NormalizedQuote):
            quote = deepcopy(raw_quote)
        elif isinstance(raw_quote, Mapping):
            values = dict(raw_quote)
            if provider and not values.get("provider"):
                values["provider"] = provider
            quote = NormalizedQuote.from_mapping(values)
        else:
            local_errors.append({"error_code": "invalid_quote", "message": "unsupported quote value"})
            continue
        code = normalize_security_code(quote.code)
        if not code:
            local_errors.append({"code": quote.code, "error_code": "invalid_code", "message": "quote code is invalid"})
            invalid_key = f"__invalid_{len(unique)}"
            unique[invalid_key] = NormalizedQuote(
                code="",
                provider=provider or "unknown",
                quality_status=DataQualityStatus.INVALID,
                errors=["invalid_code"],
            )
            continue
        if requested_set and code not in requested_set:
            local_errors.append(
                {
                    "code": code,
                    "error_code": "unexpected_quote",
                    "message": "provider returned a quote outside the requested universe",
                }
            )
            continue
        if code in unique:
            local_errors.append({"code": code, "error_code": "duplicate_quote", "message": "duplicate quote filtered"})
            continue
        quote.code = code
        apply_quote_validation(
            quote,
            now=completed,
            max_age_seconds=max_age_seconds,
            session_trade_date=trade_date,
        )
        if quote.quality_status == DataQualityStatus.INVALID:
            local_errors.append(
                {
                    "code": code,
                    "error_code": "invalid_quote",
                    "message": ",".join(quote.errors) or "quote failed validation",
                }
            )
        unique[code] = quote
    received = [
        quote
        for quote in unique.values()
        if quote.quality_status
        in {
            DataQualityStatus.VALID,
            DataQualityStatus.DEGRADED,
            DataQualityStatus.STALE,
            DataQualityStatus.CONFLICT,
        }
    ]
    expected = len(requested) if requested else max(0, expected_count)
    coverage = min(len(received) / expected, 1.0) if expected else 0.0
    received_codes = {quote.code for quote in received}
    missing_codes = [code for code in requested if code not in received_codes]
    missing_codes.extend(
        code for code in unique if code not in received_codes and code not in missing_codes
    )
    statuses = {quote.quality_status for quote in unique.values()}
    if expected == 0 or not unique:
        quality = DataQualityStatus.MISSING
    elif DataQualityStatus.CONFLICT in statuses:
        quality = DataQualityStatus.CONFLICT
    elif not received:
        quality = DataQualityStatus.INVALID if DataQualityStatus.INVALID in statuses else DataQualityStatus.MISSING
    elif len(received) < expected or local_errors or statuses & {
        DataQualityStatus.INVALID,
        DataQualityStatus.MISSING,
        DataQualityStatus.DEGRADED,
    }:
        quality = DataQualityStatus.DEGRADED
    elif DataQualityStatus.STALE in statuses:
        quality = DataQualityStatus.STALE
    else:
        quality = DataQualityStatus.VALID

    provider_counts: dict[str, int] = {}
    provider_endpoints: dict[str, list[str]] = {}
    provider_request_ids: dict[str, list[str]] = {}
    provider_fallback_levels: dict[str, int] = {}
    provider_source_timestamps: dict[str, datetime] = {}
    provider_quality_statuses: dict[str, str] = {}
    quality_rank = {
        DataQualityStatus.VALID: 0,
        DataQualityStatus.DEGRADED: 1,
        DataQualityStatus.STALE: 2,
        DataQualityStatus.MISSING: 3,
        DataQualityStatus.INVALID: 4,
        DataQualityStatus.CONFLICT: 5,
    }
    for quote in received:
        provider_name = str(quote.provider or provider or "unknown").strip().lower() or "unknown"
        provider_counts[provider_name] = provider_counts.get(provider_name, 0) + 1
        provider_fallback_levels[provider_name] = max(
            provider_fallback_levels.get(provider_name, 0),
            int(quote.fallback_level or 0),
        )
        if quote.source_timestamp is not None:
            current_source = provider_source_timestamps.get(provider_name)
            if current_source is None or quote.source_timestamp < current_source:
                provider_source_timestamps[provider_name] = quote.source_timestamp
        current_quality = provider_quality_statuses.get(provider_name)
        if current_quality is None or quality_rank[quote.quality_status] > quality_rank[DataQualityStatus(current_quality)]:
            provider_quality_statuses[provider_name] = quote.quality_status.value
        if quote.raw_reference:
            endpoints = provider_endpoints.setdefault(provider_name, [])
            if quote.raw_reference not in endpoints:
                endpoints.append(quote.raw_reference)
        request_id = quote.metadata.get("request_id") if isinstance(quote.metadata, Mapping) else None
        if request_id:
            request_ids = provider_request_ids.setdefault(provider_name, [])
            if str(request_id) not in request_ids:
                request_ids.append(str(request_id))
    actual_provider = (
        next(iter(provider_counts))
        if len(provider_counts) == 1
        else "mixed"
        if provider_counts
        else str(provider or "unknown").strip().lower() or "unknown"
    )
    actual_fallback_level = max(
        [max(0, int(fallback_level or 0)), *[quote.fallback_level for quote in unique.values()]],
        default=0,
    )
    snapshot_metadata = dict(metadata or {})
    for name, raw_ids in (snapshot_metadata.get("provider_request_ids") or {}).items():
        values = [raw_ids] if isinstance(raw_ids, str) else list(raw_ids or [])
        request_ids = provider_request_ids.setdefault(str(name).strip().lower(), [])
        for value in values:
            if str(value).strip() and str(value) not in request_ids:
                request_ids.append(str(value))
    attempted_endpoints: dict[str, list[str]] = {}
    for name, raw_endpoints in (snapshot_metadata.get("provider_endpoints") or {}).items():
        values = [raw_endpoints] if isinstance(raw_endpoints, str) else list(raw_endpoints or [])
        cleaned = [str(value) for value in values if str(value).strip()]
        if cleaned:
            attempted_endpoints[str(name).strip().lower()] = list(dict.fromkeys(cleaned))
    for name, endpoints in provider_endpoints.items():
        attempted_endpoints[name] = list(
            dict.fromkeys([*attempted_endpoints.get(name, []), *endpoints])
        )
    snapshot_metadata.update(
        {
            "duplicate_count": sum(
                1 for error in local_errors if isinstance(error, Mapping) and error.get("error_code") == "duplicate_quote"
            ),
            "invalid_count": sum(quote.quality_status == DataQualityStatus.INVALID for quote in unique.values()),
            "unexpected_count": sum(
                1 for error in local_errors if isinstance(error, Mapping) and error.get("error_code") == "unexpected_quote"
            ),
            "missing_count": max(expected - len(received), 0),
            "requested_count": len(requested),
            "requested_route": requested_route,
            "provider_counts": provider_counts,
            "provider_endpoints": attempted_endpoints,
            "provider_request_ids": provider_request_ids,
            "provider_fallback_levels": provider_fallback_levels,
            "provider_source_timestamps": {
                name: timestamp.isoformat()
                for name, timestamp in provider_source_timestamps.items()
            },
            "provider_quality_statuses": provider_quality_statuses,
        }
    )
    actual_trade_date = trade_date or next((quote.trade_date for quote in received if quote.trade_date), None)
    return QuoteSnapshot(
        snapshot_key=snapshot_key,
        market="CN",
        started_at=started,
        completed_at=completed,
        trade_date=actual_trade_date,
        provider=actual_provider,
        fallback_level=actual_fallback_level,
        expected_count=expected,
        received_count=len(received),
        coverage_ratio=round(coverage, 6),
        quotes=list(unique.values()),
        errors=local_errors,
        quality_status=quality,
        missing_codes=missing_codes,
        metadata=snapshot_metadata,
    )
