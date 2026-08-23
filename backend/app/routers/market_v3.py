"""Small authenticated V3 market-data foundation endpoints."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..market.codes import normalize_security_code
from ..market.providers.identity import build_calendar_provider, build_security_provider
from ..market_models import SecurityMaster, TradingCalendar
from ..market_runtime_models import MarketSnapshot, ProviderHealth
from ..services.market_identity_sync import (
    calendar_status,
    sync_calendar_from_provider,
    sync_security_master_from_provider,
)
from ..services.market_snapshot_service import (
    build_quote_snapshot,
    collect_snapshot_quotes,
    health_payload,
    persist_snapshot,
    snapshot_payload,
    sync_runtime_provider_health,
)
from ..services.security_master import get_market_universe, upsert_securities
from ..services.trading_calendar import CHINA_TZ, normalize_market, upsert_calendar
from ..v2_dependencies import get_current_user, require_market_identity_sync
from ..v2_models import User

router = APIRouter(prefix="/api/v3/market", tags=["v3-market"])


class MarketSnapshotRequest(BaseModel):
    """Request a server-built snapshot.

    The legacy derived fields remain accepted for wire compatibility, but are
    deliberately ignored by the service.  Coverage, fallback, trade date, and
    endpoint provenance are server-owned facts.
    """

    codes: list[str] = Field(default_factory=list, max_length=100_000)
    expected_count: int | None = Field(default=None, ge=0)
    provider: str | None = Field(default=None, max_length=64)
    fallback_level: int = Field(default=0, ge=0)
    trade_date: date | None = None
    snapshot_key: str | None = Field(default=None, max_length=128)
    provider_endpoint: str | None = Field(default=None, max_length=512)
    persist: bool = True


class SecuritySyncRequest(BaseModel):
    """Optional normalized rows; an empty object invokes the configured provider."""

    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=100_000,
        validation_alias=AliasChoices("rows", "securities", "items"),
    )
    market: str = Field(default="CN", min_length=1, max_length=16)
    source: str | None = Field(default=None, max_length=64)


class CalendarSyncRequest(BaseModel):
    """Optional normalized rows; an empty object invokes the configured provider."""

    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=10_000,
        validation_alias=AliasChoices("rows", "calendar", "days", "items"),
    )
    market: str = Field(default="CN", min_length=1, max_length=16)
    source: str | None = Field(default=None, max_length=64)
    start_date: date | None = None
    end_date: date | None = None


def _security_payload(row: SecurityMaster) -> dict[str, Any]:
    return {
        "id": row.id,
        "market": row.market,
        "exchange": row.exchange,
        "code": row.code,
        "symbol": row.symbol,
        "name": row.name,
        "security_type": row.security_type,
        "etf_category": row.etf_category,
        "listing_date": row.listing_date,
        "delisting_date": row.delisting_date,
        "status": row.status,
        "is_st": row.is_st,
        "is_suspended": row.is_suspended,
        "board": row.board,
        "lot_size": row.lot_size,
        "currency": row.currency,
        "source": row.source,
        "source_updated_at": row.source_updated_at,
        "raw_metadata_json": row.raw_metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _calendar_payload(row: TradingCalendar) -> dict[str, Any]:
    return {
        "id": row.id,
        "trade_date": row.trade_date,
        "market": row.market,
        "is_open": row.is_open,
        "previous_trade_date": row.previous_trade_date,
        "next_trade_date": row.next_trade_date,
        "source": row.source,
        "updated_at": row.updated_at,
    }


def _rows_from_sync_payload(payload: SecuritySyncRequest | CalendarSyncRequest | list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str | None]:
    """Accept the documented object form and a convenient bare rows array."""

    if isinstance(payload, list):
        return payload, "CN", None
    return payload.rows, normalize_market(payload.market), payload.source


def _metadata_payload(row: MarketSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": row.snapshot_id,
        "snapshot_key": row.snapshot_key,
        "market": row.market,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "trade_date": row.trade_date,
        "provider": row.provider,
        "fallback_level": row.fallback_level,
        "expected_count": row.expected_count,
        "received_count": row.received_count,
        "coverage_ratio": row.coverage_ratio,
        "quality_status": row.quality_status,
        "errors": row.errors_json or [],
        "metadata": row.metadata_json or {},
        "quotes": [],
    }


@router.get("/providers/health")
def list_provider_health(
    provider: str | None = Query(default=None, max_length=64),
    data_type: str | None = Query(default=None, max_length=32),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    # Keep the persisted view aligned with the process-wide circuit breaker,
    # including cooldown-driven RECOVERING transitions.
    try:
        sync_runtime_provider_health(db)
        db.commit()
    except Exception:  # pragma: no cover - defensive for pre-migration liveness
        db.rollback()
    query = db.query(ProviderHealth)
    if provider:
        query = query.filter(ProviderHealth.provider_name == provider)
    if data_type:
        query = query.filter(ProviderHealth.data_type == data_type)
    rows = query.order_by(ProviderHealth.provider_name.asc(), ProviderHealth.data_type.asc()).all()
    return [health_payload(row) for row in rows]


@router.get("/securities")
def list_securities(
    market: str = Query(default="CN", min_length=1, max_length=16),
    security_type: str | None = Query(default=None, max_length=24),
    security_kind: str | None = Query(default=None, alias="type", max_length=24),
    exchange: str | None = Query(default=None, max_length=16),
    status_filter: str | None = Query(default=None, alias="status", max_length=16),
    board: str | None = Query(default=None, max_length=16),
    include_inactive: bool = Query(default=False),
    include_suspended: bool = Query(default=True),
    include_st: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List canonical stocks/ETFs from the local SecurityMaster.

    ``security_kind=`` is accepted as the ``type`` query alias so clients can
    use either ``security_type=ETF`` or ``type=ETF``.  Pagination is page based
    by default, with optional ``limit``/``offset`` compatibility parameters.
    """

    requested_type = security_type or security_kind
    if requested_type and requested_type.upper() not in {"STOCK", "ETF", "ALL", "*"}:
        raise HTTPException(status_code=422, detail="security_type must be STOCK, ETF, or ALL")
    if status_filter and status_filter.upper() in {"ALL", "*"}:
        status_filter = None
        include_inactive = True

    normalized_market = normalize_market(market)
    try:
        rows = get_market_universe(
            db,
            market=normalized_market,
            security_type=None if not requested_type or requested_type.upper() in {"ALL", "*"} else requested_type,
            exchange=exchange,
            board=board,
            status=status_filter,
            include_inactive=include_inactive,
            include_suspended=include_suspended,
            include_st=include_st,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    total = len(rows)
    effective_size = limit or page_size
    if offset is not None:
        start = offset
        effective_page = (start // effective_size) + 1
    else:
        start = (page - 1) * effective_size
        effective_page = page
    selected = rows[start : start + effective_size]
    return {
        "items": [_security_payload(row) for row in selected],
        "total": total,
        "page": effective_page,
        "page_size": effective_size,
        "offset": start,
        "has_next": start + len(selected) < total,
    }


@router.post("/securities/sync")
def sync_securities_endpoint(
    payload: SecuritySyncRequest | list[dict[str, Any]] = Body(...),
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_market_identity_sync),
) -> dict[str, Any]:
    """Run an operator-authorized SecurityMaster synchronization."""

    if not settings.SECURITY_MASTER_SYNC_ENABLED:
        raise HTTPException(status_code=403, detail="security_master_sync_disabled")

    rows, market, source = _rows_from_sync_payload(payload)
    if not rows and not isinstance(payload, list):
        try:
            provider = build_security_provider(
                settings.SECURITY_MASTER_SYNC_PROVIDER,
                min_interval_seconds=settings.EASTMONEY_MIN_INTERVAL_SECONDS,
            )
            persisted = sync_security_master_from_provider(db, provider, market=market)
            return {
                "synced_count": len(persisted),
                "count": len(persisted),
                "provider": getattr(provider, "name", settings.SECURITY_MASTER_SYNC_PROVIDER),
                "items": [_security_payload(row) for row in persisted],
            }
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            raise HTTPException(status_code=502, detail="security_provider_sync_failed") from exc
    prepared = []
    for item in rows:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="each security row must be an object")
        row = dict(item)
        row.setdefault("market", market)
        if source is not None:
            row.setdefault("source", source)
        prepared.append(row)
    try:
        persisted = upsert_securities(db, prepared)
        db.commit()
        return {
            "synced_count": len(persisted),
            "count": len(persisted),
            "items": [_security_payload(row) for row in persisted],
        }
    except (TypeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail="security_sync_failed") from exc


@router.get("/calendar")
def list_calendar(
    market: str = Query(default="CN", min_length=1, max_length=16),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    start_query: date | None = Query(default=None, alias="start"),
    end_query: date | None = Query(default=None, alias="end"),
    is_open: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Read persisted trading-day facts for an inclusive date range."""

    effective_start = start_date or start or start_query
    effective_end = end_date or end or end_query
    if effective_start and effective_end and effective_start > effective_end:
        raise HTTPException(status_code=422, detail="start date must not be after end date")
    statement = select(TradingCalendar).where(TradingCalendar.market == normalize_market(market))
    if effective_start:
        statement = statement.where(TradingCalendar.trade_date >= effective_start)
    if effective_end:
        statement = statement.where(TradingCalendar.trade_date <= effective_end)
    if is_open is not None:
        statement = statement.where(TradingCalendar.is_open.is_(is_open))
    rows = db.execute(statement.order_by(TradingCalendar.trade_date.asc(), TradingCalendar.id.asc())).scalars().all()
    return [_calendar_payload(row) for row in rows]


@router.get("/calendar/status")
def calendar_status_endpoint(
    market: str = Query(default="CN", min_length=1, max_length=16),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Expose explicit initialization/readiness state to UI and operators."""

    return calendar_status(db, market=normalize_market(market))


@router.post("/calendar/sync")
def sync_calendar_endpoint(
    payload: CalendarSyncRequest | list[dict[str, Any]] = Body(...),
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_market_identity_sync),
) -> dict[str, Any]:
    """Run an operator-authorized TradingCalendar synchronization."""

    if not settings.CALENDAR_SYNC_ENABLED:
        raise HTTPException(status_code=403, detail="calendar_sync_disabled")

    rows, market, source = _rows_from_sync_payload(payload)
    if not rows and not isinstance(payload, list):
        day = datetime.now(CHINA_TZ).date()
        start_date = payload.start_date or (day - timedelta(days=settings.CALENDAR_SYNC_LOOKBACK_DAYS))
        end_date = payload.end_date or day
        try:
            provider = build_calendar_provider(settings.CALENDAR_SYNC_PROVIDER)
            persisted = sync_calendar_from_provider(
                db,
                provider,
                start=start_date,
                end=end_date,
                market=market,
            )
            return {
                "synced_count": len(persisted),
                "count": len(persisted),
                "provider": getattr(provider, "name", settings.CALENDAR_SYNC_PROVIDER),
                "items": [_calendar_payload(row) for row in persisted],
            }
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            raise HTTPException(status_code=502, detail="calendar_provider_sync_failed") from exc
    prepared = []
    for item in rows:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="each calendar row must be an object")
        row = dict(item)
        row.setdefault("market", market)
        if source is not None:
            row.setdefault("source", source)
        prepared.append(row)
    try:
        persisted = upsert_calendar(db, prepared, market=market, source=source)
        db.commit()
        return {
            "synced_count": len(persisted),
            "count": len(persisted),
            "items": [_calendar_payload(row) for row in persisted],
        }
    except (TypeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail="calendar_sync_failed") from exc


@router.post("/quotes/snapshot", status_code=status.HTTP_201_CREATED)
def create_quote_snapshot(
    payload: MarketSnapshotRequest,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    codes = list(dict.fromkeys(normalize_security_code(code) for code in payload.codes if normalize_security_code(code)))
    request = {
        "codes": codes,
        "route": str(payload.provider or "").strip().lower() or None,
    }
    try:
        raw = collect_snapshot_quotes(request)
        # Provider calls update the process-wide registry.  Persist that state
        # before storing the snapshot so the two health surfaces agree.
        try:
            sync_runtime_provider_health(db)
            db.commit()
        except Exception:  # pragma: no cover - pre-migration compatibility
            db.rollback()
        snapshot = build_quote_snapshot(
            raw,
            expected_count=len(codes),
            requested_codes=codes,
            provider=None,
            fallback_level=0,
            trade_date=None,
            snapshot_key=payload.snapshot_key,
        )
        if payload.persist:
            persist_snapshot(db, snapshot)
            db.commit()
        return snapshot_payload(snapshot)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # A failed provider call is still a health event.  Project it after the
        # request transaction is rolled back so circuit-open diagnostics remain
        # visible across subsequent HTTP requests.
        try:
            sync_runtime_provider_health(db)
            db.commit()
        except Exception:  # pragma: no cover - pre-migration compatibility
            db.rollback()
        raise HTTPException(status_code=502, detail=f"market_snapshot_failed:{exc}") from exc


@router.get("/quotes/snapshots")
def list_quote_snapshots(
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    rows = db.query(MarketSnapshot).order_by(MarketSnapshot.created_at.desc(), MarketSnapshot.id.desc()).limit(limit).all()
    return [_metadata_payload(row) for row in rows]


@router.get("/quotes/snapshots/{snapshot_id}")
def get_quote_snapshot(
    snapshot_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = db.query(MarketSnapshot).filter(MarketSnapshot.snapshot_id == snapshot_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Market snapshot not found.")
    return _metadata_payload(row)
