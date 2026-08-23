"""Quote snapshot orchestration and runtime metadata persistence.

Normalization and quality live in :mod:`app.market`; this service only selects
the server-owned route/universe, invokes Providers, and persists provenance.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..config import settings
from ..market.codes import normalize_security_code
from ..market.models import NormalizedQuote, QuoteSnapshot
from ..market.providers.base import build_quote_snapshot as build_domain_quote_snapshot
from ..market.providers.factory import (
    build_all_a_quote_provider,
    build_critical_quote_provider,
    create_quote_provider,
)
from ..market.providers.health import (
    get_runtime_provider_health_registry,
    runtime_provider_health_snapshot,
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
    """Invoke a server-configured route and return trusted run metadata."""

    codes = list(
        dict.fromkeys(
            code
            for value in (request.get("codes") or [])
            if (code := normalize_security_code(value))
        )
    )
    requested_name = str(request.get("route") or request.get("provider") or "").strip().lower()
    sanitized_request = {"codes": codes, "route": requested_name, "provider": requested_name}
    if _snapshot_provider is not None:
        return _snapshot_provider(sanitized_request)
    known = {"tencent", "eastmoney", "eastmoney_batch", "critical", "holding", "all_a", "auto", "fallback"}
    if not requested_name:
        return {
            "quotes": [],
            "provider": "unconfigured",
            "requested_route": None,
            "errors": [{"code": "provider_not_configured", "message": "No quote provider is configured."}],
        }
    if requested_name not in known:
        return {
            "quotes": [],
            "provider": "unconfigured",
            "requested_route": requested_name,
            "errors": [{"code": "provider_not_configured", "message": "No quote provider is configured."}],
        }
    provider = None
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
        if not codes:
            return {
                "quotes": [],
                "provider": getattr(provider, "name", requested_name or "all_a"),
                "requested_route": requested_name,
                "errors": [{"code": "universe_not_supplied", "message": "Security universe is required for a quote snapshot."}],
            }
        quotes = provider.get_all_a_share_quotes(codes)
        run_metadata_method = getattr(provider, "get_run_metadata", None)
        run_metadata = run_metadata_method() if callable(run_metadata_method) else {}
        result = {
            "quotes": quotes,
            "provider": run_metadata.get("provider") or getattr(provider, "name", requested_name or "all_a"),
            "requested_route": requested_name,
            "errors": list(getattr(provider, "last_errors", []) or []),
            "provider_attempts": run_metadata.get("provider_attempts") or [],
        }
        for key in ("provider_counts", "provider_endpoints", "fallback_level", "fallback_errors"):
            if key in run_metadata:
                result[key] = run_metadata[key]
        return result
    except Exception as exc:  # noqa: BLE001
        metadata_method = getattr(provider, "get_run_metadata", None)
        run_metadata = metadata_method() if callable(metadata_method) else {}
        provider_name = (
            run_metadata.get("provider")
            or getattr(provider, "name", None)
            or requested_name
            or "unknown"
        )
        provider_errors = list(getattr(provider, "last_errors", []) or [])
        if not provider_errors:
            provider_errors = [{"provider": provider_name, "error_code": "provider_failure", "message": str(exc)}]
        result = {
            "quotes": [],
            "provider": provider_name,
            "requested_route": requested_name or None,
            "errors": provider_errors,
            "provider_attempts": run_metadata.get("provider_attempts") or [],
        }
        for key in ("provider_counts", "provider_endpoints", "fallback_level", "fallback_errors"):
            if key in run_metadata:
                result[key] = run_metadata[key]
        return result


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


def _datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed


def _runtime_datetime(value: Any) -> datetime | None:
    """Convert registry ISO timestamps to SQLAlchemy-friendly datetimes."""

    if value is None or isinstance(value, datetime):
        return value
    return _datetime(value)


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
        for key in ("provider", "requested_route", "market", "errors", "provider_counts", "provider_endpoints", "provider_attempts"):
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


def build_quote_snapshot(
    raw_quotes: Any,
    *,
    expected_count: int | None = None,
    requested_codes: Iterable[str] | None = None,
    provider: str | None = None,
    fallback_level: int = 0,
    trade_date: date | str | None = None,
    snapshot_key: str | None = None,
    requested_route: str | None = None,
    errors: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper around the canonical domain snapshot builder."""
    raw_items, provider_metadata = _coerce_raw_quotes(raw_quotes)
    requested = list(dict.fromkeys(code for code in (_canonical_code(value) for value in (requested_codes or [])) if code))
    expected = len(requested) if requested else max(int(expected_count or 0), 0)
    all_errors = list(provider_metadata.get("errors") or []) + list(errors or [])
    metadata = {key: value for key, value in provider_metadata.items() if key != "quotes"}
    effective_provider = provider or provider_metadata.get("provider")
    try:
        effective_fallback_level = max(int(fallback_level or 0), int(provider_metadata.get("fallback_level") or 0))
    except (TypeError, ValueError):
        effective_fallback_level = max(int(fallback_level or 0), 0)
    snapshot = build_domain_quote_snapshot(
        raw_items,
        expected_count=expected,
        provider=effective_provider,
        fallback_level=effective_fallback_level,
        trade_date=None if trade_date is None else _date(trade_date),
        snapshot_key=snapshot_key,
        requested_codes=requested,
        requested_route=requested_route or provider_metadata.get("requested_route"),
        max_age_seconds=settings.QUOTE_FRESHNESS_SECONDS,
        metadata=metadata,
        errors=all_errors,
    )
    return snapshot.to_dict()


def get_all_a_share_quote_snapshot(
    db: Session,
    *,
    provider: str | None = None,
    trade_date: date | str | None = None,
    snapshot_key: str | None = None,
    include_etf: bool = False,
    include_bse: bool = False,
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
        exchange=None,
        include_suspended=include_suspended,
        include_inactive=False,
    )
    exchanges = {"SSE", "SZSE"}
    if include_bse:
        exchanges.add("BSE")
    rows = [row for row in stocks if str(row.exchange or "").upper() in exchanges]
    if include_etf:
        etfs = get_market_universe(
                db,
                security_type="ETF",
                include_suspended=include_suspended,
                include_inactive=False,
        )
        rows.extend(row for row in etfs if str(row.exchange or "").upper() in exchanges)
    codes = [row.code for row in rows]
    request = {
        "codes": codes,
        "route": provider or "all_a",
    }
    raw = collect_snapshot_quotes(request)
    return build_quote_snapshot(
        raw,
        expected_count=len(codes),
        requested_codes=codes,
        provider=None,
        trade_date=None,
        requested_route=provider or "all_a",
        snapshot_key=snapshot_key,
    )


def persist_snapshot(db: Session, snapshot: Mapping[str, Any], *, endpoint: str | None = None, operation: str = "quote_snapshot") -> MarketSnapshot:
    """Persist metadata and server-derived snapshot-level lineage.

    ``endpoint`` remains a compatibility-only internal fallback.  API request
    fields never reach it; real adapter references in snapshot metadata win.
    """
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
    metadata = dict(snapshot.get("metadata") or {})
    raw_counts = metadata.get("provider_counts") or {}
    provider_counts = {
        str(name): int(count)
        for name, count in raw_counts.items()
        if str(name).strip() and int(count or 0) > 0
    }
    raw_endpoints = metadata.get("provider_endpoints") or {}
    provider_attempts = metadata.get("provider_attempts") or []
    attempted_providers = list(
        dict.fromkeys(
            str(item.get("provider") or "").strip().lower()
            for item in provider_attempts
            if isinstance(item, Mapping) and str(item.get("provider") or "").strip()
        )
    )
    providers = list(provider_counts) or attempted_providers or [row.provider]
    for provider_name in providers:
        endpoints = raw_endpoints.get(provider_name) or []
        if isinstance(endpoints, str):
            endpoints = [endpoints]
        trusted_endpoint = str(endpoints[0])[:512] if endpoints else endpoint
        db.add(SourceLineage(
            entity_type="market_snapshot",
            entity_key=row.snapshot_id,
            field_name=None,
            provider=provider_name,
            provider_endpoint=trusted_endpoint,
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
                "requested_route": (snapshot.get("metadata") or {}).get("requested_route"),
                "provider_counts": (snapshot.get("metadata") or {}).get("provider_counts", {}),
                "provider_endpoints": (snapshot.get("metadata") or {}).get("provider_endpoints", {}),
                "provider_contribution_count": provider_counts.get(provider_name, row.received_count),
            },
        ))
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


def sync_runtime_provider_health(
    db: Session,
    *,
    data_type: str | None = None,
) -> list[ProviderHealth]:
    """Project the process-wide Provider registry into the metadata table.

    Provider adapters are intentionally kept free of database handles.  The
    request boundary is therefore the synchronization point: after a snapshot
    attempt, runtime counters and circuit state are copied as one small set of
    rows.  A health read also calls this helper so cooldown transitions (for
    example ``RECOVERING``) are visible without another quote request.
    """

    persisted: list[ProviderHealth] = []
    for state in runtime_provider_health_snapshot():
        state_data_type = str(state.get("data_type") or "quote").lower()
        if data_type is not None and state_data_type != str(data_type).lower():
            continue
        provider_name = str(state.get("provider_name") or "").strip().lower()
        if not provider_name:
            continue
        row = _health_row(db, provider_name, state_data_type)
        row.status = str(state.get("status") or "HEALTHY")
        row.success_count = int(state.get("success_count") or 0)
        row.failure_count = int(state.get("failure_count") or 0)
        row.consecutive_failures = int(state.get("consecutive_failures") or 0)
        row.last_success_at = _runtime_datetime(state.get("last_success_at"))
        row.last_failure_at = _runtime_datetime(state.get("last_failure_at"))
        row.last_error = str(state.get("last_error"))[:4000] if state.get("last_error") else None
        latency = state.get("last_latency_ms")
        row.last_latency_ms = float(latency) if latency is not None else None
        persisted.append(row)
    db.flush()
    return persisted


def hydrate_runtime_provider_health(db: Session) -> list[dict[str, object]]:
    """Restore durable ProviderHealth rows into a newly started process.

    The runtime registry is deliberately in-memory for hot-path Provider
    calls, while the database remains the restart-safe source of the last
    observed state.  Hydration happens once during application startup; later
    request-boundary projections always flow from runtime to storage.
    """

    rows = db.query(ProviderHealth).all()
    return [
        state.to_dict()
        for state in get_runtime_provider_health_registry().hydrate(
            (
                {
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
                }
                for row in rows
            )
        )
    ]


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
