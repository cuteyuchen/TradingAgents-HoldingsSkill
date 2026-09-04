"""Daily Investment Workbench API (read-only dashboard + internal workflow hooks)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..operations.dashboard import (
    build_daily_dashboard,
    build_dashboard_diagnostics,
    build_dashboard_health,
    build_dashboard_timeline,
)
from ..operations.notifications import list_operating_notifications, mark_operating_notification_read
from ..operations.workflow import operational_timeline, reconcile_today
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, User

router = APIRouter(prefix="/api/v3", tags=["v3-operations"])


def _portfolio(db: Session, *, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.execute(select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return row


@router.get("/portfolios/{portfolio_id}/dashboard/today")
def dashboard_today(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return build_daily_dashboard(db, user_id=current_user.id, portfolio_id=portfolio_id, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/dashboard/timeline")
def dashboard_timeline(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return operational_timeline(db, user_id=current_user.id, portfolio_id=portfolio_id, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/dashboard/health")
def dashboard_health(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return build_dashboard_health(db, user_id=current_user.id, portfolio_id=portfolio_id, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/dashboard/diagnostics")
def dashboard_diagnostics(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return build_dashboard_diagnostics(
            db,
            user_id=current_user.id,
            portfolio_id=portfolio_id,
            as_of=as_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/operations/reconcile-today")
def reconcile_today_operation(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return reconcile_today(
            db,
            user_id=current_user.id,
            portfolio_id=portfolio_id,
            now=as_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/notifications")
def operating_notifications(
    portfolio_id: int | None = Query(default=None),
    as_of: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    unread_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if portfolio_id is not None:
        _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return list_operating_notifications(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        as_of=as_of,
        limit=limit,
        unread_only=unread_only,
    )


@router.post("/notifications/{notification_id}/read")
def mark_operating_notification_as_read(
    notification_id: str,
    portfolio_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if portfolio_id is not None:
        _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return mark_operating_notification_read(
            db,
            user_id=current_user.id,
            notification_id=notification_id,
            portfolio_id=portfolio_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
