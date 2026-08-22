"""Normalized quote snapshot assembly and runtime metadata persistence.

This module is intentionally provider-agnostic.  Providers may return mappings,
dataclasses, or small model objects; the adapter boundary is normalized here and
only snapshot metadata is persisted.
"""
from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..config import settings
from ..market.codes import normalize_security_code
from ..market.providers.factory import (
    build_all_a_quote_provider,
    build_critical_quote_provider,
    create_quote_provider,
)
from ..market_runtime_models import MarketSnapshot, ProviderHealth, SourceLineage
from .security_master import get_market_universe

QUALITY_STATUSES = frozenset({"VALID", "DEGRADED", "STALE", "CONFLICT", "MISSING", "INVALID"})
HEALTH_STATUSES = frozenset({"HEALTHY", "DEGRADED", "CIRCUIT_OPEN", "RECOVERING"})
DEFAULT_FAILURE_THRESHOLD = 3
SnapshotProvider = Callable[[dict[str, Any]], Any]
_snapshot_provider: SnapshotProvider | None = None


def set_snapshot_provider(provider: SnapshotProvider | None) -> None:
    """Configure the provider-layer callback without coupling this module to it."""
    global _snapshot_provider
    _snapshot_provider = provider


def collect_snapshot_quotes(request: Mapping[str, Any]) -> Any:
    """Invoke an injected provider or an explicitly named configured route.

    Unknown provider names remain fail-closed and return a structured missing
    result.  This keeps API tests and deployments without public-data access
    deterministic while still making ``tencent``/``eastmoney_batch`` usable
    when an operator explicitly selects them.
    """
    if _snapshot_provider is not None:
        return _snapshot_provider(dict(request))
    requested_name = str(request.get("provider") or "").strip().lower()
    known = {"tencent", "eastmoney", "eastmoney_batch", "critical", "holding", "all_a", "auto", "fallback"}
    if not requested_name:
        return {
            "quotes": [],
            "provider": "unconfigured",
            "expected_count": request.get("expected_count") or len(request.get("codes") or []),
            "errors": [{"code": "provider_not_configured", "message": "No quote provider is configured."}],
        }
    if requested_name not in known:
        return {
            "quotes": [],
            "provider": requested_name,
            "expected_count": request.get("expected_count") or len(request.get("codes") or []),
            "errors": [{"code": "provider_not_configured", "message": "No quote provider is configured."}],
        }
    try:
        if requested_name in {"tencent", "eastmoney", "eastmoney_batch"}:
            provider = create_quote_provider(requested_name)
        elif requested_name in {"critical", "holding"}:
            provider = build_critical_quote_provider(
                primary=settings.MARKET_QUOTE_CRITICAL_PRIMARY_PROVIDER,
                fallbacks=settings.MARKET_QUOTE_CRITICAL_FALLBACK_PROVIDERS,
            )
        else:
            provider = build_all_a_quote_provider(
                primary=settings.MARKET_QUOTE_ALL_A_PRIMARY_PROVIDER,
                fallbacks=settings.MARKET_QUOTE_ALL_A_FALLBACK_PROVIDERS,
            )
        codes = list(request.get("codes") or [])
        if not codes:
            return {
                "quotes": [],
                "provider": getattr(provider, "name", requested_name or "all_a"),
                "expected_count": request.get("expected_count") or 0,
                "errors": [{"code": "universe_not_supplied", "message": "Security universe is required for a quote snapshot."}],
            }
        quotes = provider.get_all_a_share_quotes(codes)
        return {
            "quotes": quotes,
            "provider": getattr(provider, "name", requested_name or "all_a"),
            "expected_count": request.get("expected_count") or len(codes),
            "errors": list(getattr(provider, "last_errors", []) or []),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "quotes": [],
            "provider": requested_name or "unconfigured",
            "expected_count": request.get("expected_count") or len(request.get("codes") or []),
            "errors": [{"code": "provider_failure", "message": str(exc)}],
        }


def _value(source: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        for key in keys:
            if key in source:
                return source[key]
        return default
    for key in keys:
        if hasattr(source, key):
            return getattr(source, key)
    return default


def _float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed


def _date(value: Any) -> date | None:
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed_dt = _datetime(value)
    if parsed_dt is not None:
        return parsed_dt.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _canonical_code(value: Any) -> str:
    """Use the new security-master resolver when available, then legacy facade."""
    return normalize_security_code(value)


def _serializable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serializable(item) for item in value]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


def _normalise_quality(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    status = str(value).strip().upper()
    aliases = {"OK": "VALID", "GOOD": "VALID", "PARTIAL": "DEGRADED", "BAD": "INVALID"}
    status = aliases.get(status, status)
    return status if status in QUALITY_STATUSES else None


def normalize_quote(raw: Any, *, provider: str | None = None, fallback_level: int = 0) -> dict[str, Any]:
    """Convert one provider quote to the Phase B normalized field contract."""
    code = _canonical_code(_value(raw, "code", "symbol", "ticker", default=""))
    price = _float(_value(raw, "price", "current_price", "last"))
    prev_close = _float(_value(raw, "prev_close", "prevClose", "pre_close"))
    open_price = _float(_value(raw, "open", "open_price"))
    high = _float(_value(raw, "high", "high_price"))
    low = _float(_value(raw, "low", "low_price"))
    volume = _float(_value(raw, "volume", "vol"))
    amount = _float(_value(raw, "amount", "turnover_amount", "成交额"))
    pct_change = _float(_value(raw, "pct_change", "change_percent", "percent"))
    turnover_rate = _float(_value(raw, "turnover_rate", "turnoverRatio"))
    source_timestamp = _datetime(_value(raw, "source_timestamp", "quote_time", "timestamp", "trade_time"))
    fetched_at = _datetime(_value(raw, "fetched_at", "retrieved_at")) or datetime.now(UTC)
    trade_date = _date(_value(raw, "trade_date", "trading_date")) or (source_timestamp.date() if source_timestamp else None)
    actual_provider = str(_value(raw, "provider", "source", default=provider or "unknown") or provider or "unknown")
    raw_quality = _normalise_quality(_value(raw, "quality_status", "data_quality", "status"))

    validation_error: str | None = None
    if len(code) != 6 or not code.isdigit():
        validation_error = "invalid_code"
    elif price is None:
        # A suspended instrument may legitimately have no current price; it is
        # missing/stale data, not a zero-price crash.
        validation_error = "missing_price"
    elif price < 0 or any(value is not None and value < 0 for value in (prev_close, open_price, high, low, volume, amount)):
        validation_error = "negative_quote_field"
    elif high is not None and low is not None and high < low:
        validation_error = "high_below_low"

    if validation_error == "invalid_code" or validation_error in {"negative_quote_field", "high_below_low"}:
        quality_status = "INVALID"
    elif validation_error == "missing_price":
        quality_status = "MISSING"
    else:
        quality_status = raw_quality or "VALID"

    return {
        "market": str(_value(raw, "market", default="CN") or "CN"),
        "exchange": _value(raw, "exchange", default=None),
        "code": code,
        "name": _value(raw, "name", "security_name", default=None),
        "security_type": _value(raw, "security_type", "instrument_type", default=None),
        "price": price,
        "prev_close": prev_close,
        "open": open_price,
        "high": high,
        "low": low,
        "pct_change": pct_change,
        "volume": volume,
        "amount": amount,
        "turnover_rate": turnover_rate,
        "trade_date": trade_date,
        "source_timestamp": source_timestamp,
        "provider": actual_provider,
        "fetched_at": fetched_at,
        "quality_status": quality_status,
        "fallback_level": int(_value(raw, "fallback_level", default=fallback_level) or 0),
        "raw_reference": _value(raw, "raw_reference", "raw_id", "source_id", default=None),
        "validation_error": validation_error,
    }


def _coerce_raw_quotes(raw_quotes: Any) -> tuple[list[Any], dict[str, Any]]:
    metadata: dict[str, Any] = {}
    if isinstance(raw_quotes, Mapping) and "quotes" in raw_quotes:
        metadata = dict(raw_quotes)
        values = raw_quotes.get("quotes") or []
        if isinstance(values, Mapping):
            return list(values.values()), metadata
        return list(values) if isinstance(values, Iterable) and not isinstance(values, (str, bytes)) else [], metadata
    if isinstance(raw_quotes, Mapping):
        return list(raw_quotes.values()), metadata
    object_quotes = _value(raw_quotes, "quotes", default=None)
    if object_quotes is not None:
        for key in ("provider", "expected_count", "market", "trade_date", "errors", "fallback_level"):
            value = _value(raw_quotes, key, default=None)
            if value is not None:
                metadata[key] = value
        if isinstance(object_quotes, Mapping):
            return list(object_quotes.values()), metadata
        if isinstance(object_quotes, Iterable) and not isinstance(object_quotes, (str, bytes)):
            return list(object_quotes), metadata
    if isinstance(raw_quotes, Iterable) and not isinstance(raw_quotes, (str, bytes)):
        return list(raw_quotes), metadata
    return [], metadata


def _snapshot_quality(quotes: list[dict[str, Any]], expected_count: int, received_count: int, errors: list[Any]) -> str:
    if any(item["quality_status"] == "CONFLICT" for item in quotes):
        return "CONFLICT"
    if expected_count <= 0 or received_count == 0:
        return "MISSING"
    if any(item["quality_status"] == "INVALID" for item in quotes):
        return "INVALID" if received_count == 0 else "DEGRADED"
    if any(item["quality_status"] == "STALE" for item in quotes):
        return "STALE"
    if errors or received_count < expected_count or any(item["quality_status"] == "DEGRADED" for item in quotes):
        return "DEGRADED"
    return "VALID"


def build_quote_snapshot(
    raw_quotes: Any,
    *,
    expected_count: int | None = None,
    requested_codes: Iterable[str] | None = None,
    provider: str | None = None,
    fallback_level: int = 0,
    trade_date: date | str | None = None,
    snapshot_key: str | None = None,
    errors: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Build a serializable snapshot and calculate coverage in O(N)."""
    started_at = datetime.now(UTC)
    raw_items, provider_metadata = _coerce_raw_quotes(raw_quotes)
    all_errors = list(provider_metadata.get("errors") or [])
    all_errors.extend(list(errors or []))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for raw in raw_items:
        quote = normalize_quote(
            raw,
            provider=provider or provider_metadata.get("provider"),
            fallback_level=fallback_level,
        )
        code = quote["code"]
        if code and code in seen:
            duplicates.append(code)
            continue
        if code:
            seen.add(code)
        normalized.append(quote)
    if duplicates:
        all_errors.append({"code": "duplicate_quote", "codes": sorted(set(duplicates))})

    requested = [_canonical_code(code) for code in (requested_codes or [])]
    requested = list(dict.fromkeys(code for code in requested if code))
    inferred_expected = provider_metadata.get("expected_count")
    try:
        expected = int(expected_count if expected_count is not None else inferred_expected if inferred_expected is not None else len(requested) or len(raw_items))
    except (TypeError, ValueError):
        expected = len(requested) or len(raw_items)
    expected = max(expected, 0)
    received = sum(1 for item in normalized if item["quality_status"] not in {"MISSING", "INVALID"} and item["code"])
    coverage = round(received / expected, 6) if expected else 0.0
    coverage = min(max(coverage, 0.0), 1.0)
    quality_status = _snapshot_quality(normalized, expected, received, all_errors)
    parsed_trade_date = _date(trade_date)
    if parsed_trade_date is None:
        for item in normalized:
            if item.get("trade_date"):
                parsed_trade_date = item["trade_date"]
                break
    completed_at = datetime.now(UTC)
    actual_provider = str(provider or provider_metadata.get("provider") or next((item["provider"] for item in normalized if item.get("provider")), "unknown"))
    snapshot_id = uuid.uuid4().hex
    result = {
        "snapshot_id": snapshot_id,
        "snapshot_key": snapshot_key or snapshot_id,
        "market": str(provider_metadata.get("market") or "CN"),
        "started_at": started_at,
        "completed_at": completed_at,
        "trade_date": parsed_trade_date,
        "provider": actual_provider,
        "fallback_level": max([fallback_level, *[int(item.get("fallback_level") or 0) for item in normalized]], default=0),
        "expected_count": expected,
        "received_count": received,
        "coverage_ratio": coverage,
        "quality_status": quality_status,
        "quotes": normalized,
        "errors": [_serializable(error) for error in all_errors],
        "metadata": {
            "duplicate_count": len(duplicates),
            "invalid_count": sum(1 for item in normalized if item["quality_status"] == "INVALID"),
            "missing_count": max(expected - received, 0),
            "requested_count": len(requested),
            "provider_metadata": _serializable({key: value for key, value in provider_metadata.items() if key != "quotes"}),
        },
    }
    return result


def get_all_a_share_quote_snapshot(
    db: Session,
    *,
    provider: str | None = None,
    trade_date: date | str | None = None,
    snapshot_key: str | None = None,
    include_etf: bool = True,
    include_suspended: bool = False,
) -> dict[str, Any]:
    """Build one batch quote snapshot from the persisted SecurityMaster universe.

    This is the Phase B all-A foundation entry point.  It performs no universe
    inference from names and no per-security HTTP loop; the selected provider
    receives the complete code list through its batch contract.
    """
    stocks = get_market_universe(
        db,
        security_type="STOCK",
        include_suspended=include_suspended,
        include_inactive=False,
    )
    rows = list(stocks)
    if include_etf:
        rows.extend(
            get_market_universe(
                db,
                security_type="ETF",
                include_suspended=include_suspended,
                include_inactive=False,
            )
        )
    codes = [row.code for row in rows]
    request = {
        "codes": codes,
        "expected_count": len(codes),
        "provider": provider or "all_a",
        "trade_date": trade_date,
    }
    raw = collect_snapshot_quotes(request)
    return build_quote_snapshot(
        raw,
        expected_count=len(codes),
        requested_codes=codes,
        provider=provider,
        trade_date=trade_date,
        snapshot_key=snapshot_key,
    )


def persist_snapshot(db: Session, snapshot: Mapping[str, Any], *, endpoint: str | None = None, operation: str = "quote_snapshot") -> MarketSnapshot:
    """Persist metadata and one snapshot-level lineage row in one transaction."""
    row = MarketSnapshot(
        snapshot_id=str(snapshot["snapshot_id"]),
        snapshot_key=str(snapshot.get("snapshot_key") or snapshot["snapshot_id"]),
        market=str(snapshot.get("market") or "CN"),
        started_at=_datetime(snapshot.get("started_at")) or datetime.now(UTC),
        completed_at=_datetime(snapshot.get("completed_at")) or datetime.now(UTC),
        trade_date=_date(snapshot.get("trade_date")),
        provider=str(snapshot.get("provider") or "unknown"),
        fallback_level=int(snapshot.get("fallback_level") or 0),
        expected_count=int(snapshot.get("expected_count") or 0),
        received_count=int(snapshot.get("received_count") or 0),
        coverage_ratio=float(snapshot.get("coverage_ratio") or 0),
        quality_status=str(snapshot.get("quality_status") or "MISSING"),
        errors_json=_serializable(snapshot.get("errors") or []),
        metadata_json=_serializable(snapshot.get("metadata") or {}),
    )
    db.add(row)
    db.add(
        SourceLineage(
            entity_type="market_snapshot",
            entity_key=row.snapshot_id,
            field_name=None,
            provider=row.provider,
            provider_endpoint=endpoint,
            operation=operation,
            source_timestamp=None,
            fetched_at=row.completed_at,
            trade_date=row.trade_date,
            fallback_level=row.fallback_level,
            quality_status=row.quality_status,
            error_code="provider_error" if snapshot.get("errors") else None,
            error_message="; ".join(str(item) for item in snapshot.get("errors") or [])[:4000] or None,
            metadata_json={
                "expected_count": row.expected_count,
                "received_count": row.received_count,
                "coverage_ratio": row.coverage_ratio,
            },
        )
    )
    db.flush()
    return row


def _health_row(db: Session, provider_name: str, data_type: str) -> ProviderHealth:
    row = (
        db.query(ProviderHealth)
        .filter(ProviderHealth.provider_name == provider_name, ProviderHealth.data_type == data_type)
        .first()
    )
    if row is None:
        row = ProviderHealth(provider_name=provider_name, data_type=data_type)
        db.add(row)
        db.flush()
    return row


def record_provider_success(db: Session, provider_name: str, data_type: str, *, latency_ms: float | None = None) -> ProviderHealth:
    now = datetime.now(UTC)
    row = _health_row(db, provider_name, data_type)
    row.success_count += 1
    row.consecutive_failures = 0
    row.status = "HEALTHY"
    row.last_success_at = now
    row.last_latency_ms = latency_ms
    row.last_error = None
    db.flush()
    return row


def record_provider_failure(
    db: Session,
    provider_name: str,
    data_type: str,
    error: Any,
    *,
    latency_ms: float | None = None,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> ProviderHealth:
    now = datetime.now(UTC)
    row = _health_row(db, provider_name, data_type)
    row.failure_count += 1
    row.consecutive_failures += 1
    row.last_failure_at = now
    row.last_error = str(error)[:4000]
    row.last_latency_ms = latency_ms
    row.status = "CIRCUIT_OPEN" if row.consecutive_failures >= max(1, failure_threshold) else "DEGRADED"
    db.flush()
    return row


def health_payload(row: ProviderHealth) -> dict[str, Any]:
    return {
        "id": row.id,
        "provider_name": row.provider_name,
        "data_type": row.data_type,
        "status": row.status,
        "success_count": row.success_count,
        "failure_count": row.failure_count,
        "consecutive_failures": row.consecutive_failures,
        "last_success_at": row.last_success_at,
        "last_failure_at": row.last_failure_at,
        "last_error": row.last_error,
        "last_latency_ms": row.last_latency_ms,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def snapshot_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Convert in-memory snapshot values to JSON-safe API values."""
    return _serializable(dict(snapshot))
