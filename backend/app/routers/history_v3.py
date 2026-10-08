"""Phase L historical data foundation API (authenticated; operator imports only)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..history.availability import history_manifest_items
from ..history.coverage import HISTORY_DATA_TYPES, historical_data_coverage
from ..history.models import HistoricalDataSyncRun, SecurityLifecycleEvent
from ..history.sync import (
    cancel_history_sync_run,
    run_history_sync,
    serialize_history_sync_run,
)
from ..history.universe import resolve_security_state, resolve_special_treatment
from ..v2_dependencies import get_current_user
from ..v2_models import User

router = APIRouter(prefix="/api/v3/history", tags=["v3-history"])

MAX_IMPORT_ROWS = 50_000


class SyncRequest(BaseModel):
    data_type: Literal[
        "security_lifecycle",
        "trading_status",
        "st_classification",
        "valuation",
        "fundamentals",
        "etf_metadata",
        "price_basis",
    ]
    start_date: date | None = None
    end_date: date | None = None
    market: str = Field(default="CN", max_length=16)
    provider: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=64)
    rows: list[dict[str, Any]] | None = None
    dry_run: bool = False

    model_config = ConfigDict(extra="forbid")


def _error(exc: Exception) -> HTTPException:
    message = str(exc)
    if "lease" in message.lower() or "conflict" in message.lower():
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    if "too_large" in message.lower() or "unsupported" in message.lower() or "exceed" in message.lower():
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


@router.get("/availability")
def history_availability(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    market: str = Query(default="CN"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    items = history_manifest_items(
        db, start_date=start_date, end_date=end_date, market=market
    )
    return {"items": items or {}}


@router.get("/coverage")
def history_coverage(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    data_type: str | None = Query(default=None),
    market: str = Query(default="CN"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return historical_data_coverage(
            db,
            start_date=start_date,
            end_date=end_date,
            data_type=data_type,
            market=market,
        )
    except ValueError as exc:
        raise _error(exc) from exc


@router.get("/sync-runs")
def history_sync_runs(
    data_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    statement = select(HistoricalDataSyncRun)
    if data_type:
        if data_type not in HISTORY_DATA_TYPES:
            raise HTTPException(status_code=422, detail="unsupported_history_data_type")
        statement = statement.where(HistoricalDataSyncRun.data_type == data_type)
    rows = list(
        db.execute(
            statement.order_by(HistoricalDataSyncRun.created_at.desc()).limit(limit)
        ).scalars()
    )
    return {"runs": [serialize_history_sync_run(row) for row in rows]}


@router.get("/sync-runs/{run_id}")
def history_sync_run_detail(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = db.get(HistoricalDataSyncRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sync run not found.")
    return serialize_history_sync_run(row)


@router.post("/sync", status_code=status.HTTP_201_CREATED)
def create_history_sync(
    payload: SyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if payload.rows is not None and len(payload.rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status_code=422, detail="import_row_limit_exceeded")
    try:
        result = run_history_sync(
            db,
            data_type=payload.data_type,
            start_date=payload.start_date,
            end_date=payload.end_date,
            market=payload.market,
            provider=payload.provider,
            source=payload.source,
            rows=payload.rows,
            dry_run=payload.dry_run,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise _error(exc) from exc


@router.post("/sync-runs/{run_id}/cancel")
def cancel_history_sync(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = cancel_history_sync_run(db, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sync run not found.")
    db.commit()
    return serialize_history_sync_run(row)


@router.get("/security/{code}/state")
def security_state(
    code: str,
    as_of: date = Query(...),
    market: str = Query(default="CN"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return resolve_security_state(db, code, as_of=as_of, market=market)


@router.get("/security/{code}/classification")
def security_classification(
    code: str,
    trade_date: date = Query(...),
    market: str = Query(default="CN"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return resolve_special_treatment(db, code, trade_date=trade_date, market=market)


@router.get("/security/{code}/timeline")
def security_timeline(
    code: str,
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    rows = list(
        db.execute(
            select(SecurityLifecycleEvent)
            .where(SecurityLifecycleEvent.code == code)
            .order_by(SecurityLifecycleEvent.effective_date.asc(), SecurityLifecycleEvent.id.asc())
            .limit(limit)
        ).scalars()
    )
    return {
        "code": code,
        "events": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "effective_date": row.effective_date.isoformat(),
                "effective_at": row.effective_at.isoformat() if row.effective_at else None,
                "source_available_at": row.source_available_at.isoformat() if row.source_available_at else None,
                "source": row.source,
                "source_ref": row.source_ref,
                "quality_status": row.quality_status,
            }
            for row in rows
        ],
    }


__all__ = ["router"]
