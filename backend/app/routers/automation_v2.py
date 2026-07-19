"""V2 scheduled analysis and notification-channel settings."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..security import encrypt_secret
from ..services.notifications import test_channel, validate_webhook
from ..services.scheduler import create_scheduled_job, next_run, run_scheduled_job, validate_timezone
from ..v2_dependencies import get_current_user
from ..v2_models import NotificationChannel, Portfolio, Schedule, User
from ..v2_schemas import (
    AnalysisJobResponse,
    NotificationChannelCreate,
    NotificationChannelResponse,
    NotificationChannelUpdate,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleUpdate,
    SimpleMessage,
)
from .analysis_v2 import _job_response

router = APIRouter(prefix="/api/v2", tags=["v2-automation"])


def _portfolio(db: Session, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return row


def _schedule(db: Session, user_id: int, schedule_id: int) -> Schedule:
    row = db.query(Schedule).filter(Schedule.id == schedule_id, Schedule.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return row


def _schedule_response(row: Schedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=row.id,
        portfolio_id=row.portfolio_id,
        name=row.name,
        timezone=row.timezone,
        hour=row.hour,
        minute=row.minute,
        checkpoint=row.checkpoint,
        mode=row.mode,
        enabled=row.enabled,
        stale_snapshot_days=row.stale_snapshot_days,
        notify=row.notify,
        max_consecutive_failures=row.max_consecutive_failures,
        consecutive_failures=row.consecutive_failures,
        last_run_at=row.last_run_at,
        next_run_at=row.next_run_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _channel(db: Session, user_id: int, channel_id: int) -> NotificationChannel:
    row = (
        db.query(NotificationChannel)
        .filter(NotificationChannel.id == channel_id, NotificationChannel.user_id == user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Notification channel not found.")
    return row


def _mask_webhook() -> str:
    return "https://••••••••/••••••••"


def _channel_response(row: NotificationChannel) -> NotificationChannelResponse:
    return NotificationChannelResponse(
        id=row.id,
        type=row.type,
        name=row.name,
        enabled=row.enabled,
        webhook_masked=_mask_webhook(),
        has_secret=bool(row.encrypted_secret),
        last_test_status=row.last_test_status,
        last_test_at=row.last_test_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/schedules", response_model=list[ScheduleResponse])
def list_schedules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ScheduleResponse]:
    rows = db.query(Schedule).filter(Schedule.user_id == current_user.id).order_by(Schedule.id.asc()).all()
    return [_schedule_response(row) for row in rows]


@router.post("/schedules", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    _portfolio(db, current_user.id, payload.portfolio_id)
    try:
        validate_timezone(payload.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    duplicate = (
        db.query(Schedule)
        .filter(
            Schedule.user_id == current_user.id,
            Schedule.portfolio_id == payload.portfolio_id,
            Schedule.name == payload.name,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Schedule name already exists for this portfolio.")
    row = Schedule(user_id=current_user.id, **payload.model_dump())
    db.add(row)
    db.flush()
    row.next_run_at = next_run(row)
    db.commit()
    db.refresh(row)
    return _schedule_response(row)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleResponse)
def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    row = _schedule(db, current_user.id, schedule_id)
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    try:
        validate_timezone(row.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row.next_run_at = next_run(row)
    if row.enabled and row.consecutive_failures >= row.max_consecutive_failures:
        row.consecutive_failures = 0
    db.commit()
    db.refresh(row)
    return _schedule_response(row)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    db.delete(_schedule(db, current_user.id, schedule_id))
    db.commit()


@router.post("/schedules/{schedule_id}/run-now", response_model=AnalysisJobResponse, status_code=status.HTTP_202_ACCEPTED)
def run_schedule_now(
    schedule_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalysisJobResponse:
    row = _schedule(db, current_user.id, schedule_id)
    try:
        job = create_scheduled_job(db, row, force=True)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row.last_run_at = datetime.now(UTC)
    row.next_run_at = next_run(row)
    db.commit()
    background_tasks.add_task(run_scheduled_job, job.id, row.id)
    return _job_response(job)


@router.get("/notifications", response_model=list[NotificationChannelResponse])
def list_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[NotificationChannelResponse]:
    rows = (
        db.query(NotificationChannel)
        .filter(NotificationChannel.user_id == current_user.id)
        .order_by(NotificationChannel.id.asc())
        .all()
    )
    return [_channel_response(row) for row in rows]


@router.post("/notifications", response_model=NotificationChannelResponse, status_code=status.HTTP_201_CREATED)
def create_notification(
    payload: NotificationChannelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationChannelResponse:
    try:
        webhook = validate_webhook(payload.type, payload.webhook)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    duplicate = (
        db.query(NotificationChannel)
        .filter(NotificationChannel.user_id == current_user.id, NotificationChannel.name == payload.name)
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Notification channel name already exists.")
    row = NotificationChannel(
        user_id=current_user.id,
        type=payload.type,
        name=payload.name,
        encrypted_webhook=encrypt_secret(webhook),
        encrypted_secret=encrypt_secret(payload.secret) if payload.secret else None,
        enabled=payload.enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _channel_response(row)


@router.patch("/notifications/{channel_id}", response_model=NotificationChannelResponse)
def update_notification(
    channel_id: int,
    payload: NotificationChannelUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationChannelResponse:
    row = _channel(db, current_user.id, channel_id)
    fields = payload.model_fields_set
    if "name" in fields and payload.name is not None:
        row.name = payload.name
    if "webhook" in fields and payload.webhook is not None:
        try:
            row.encrypted_webhook = encrypt_secret(validate_webhook(row.type, payload.webhook))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.clear_secret:
        row.encrypted_secret = None
    elif "secret" in fields and payload.secret:
        row.encrypted_secret = encrypt_secret(payload.secret)
    if "enabled" in fields and payload.enabled is not None:
        row.enabled = payload.enabled
    db.commit()
    db.refresh(row)
    return _channel_response(row)


@router.delete("/notifications/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    db.delete(_channel(db, current_user.id, channel_id))
    db.commit()


@router.post("/notifications/{channel_id}/test", response_model=SimpleMessage)
def test_notification(
    channel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SimpleMessage:
    row = _channel(db, current_user.id, channel_id)
    try:
        _status, body = test_channel(row)
        row.last_test_status = "ok"
        row.last_test_at = datetime.now(UTC)
        db.commit()
        return SimpleMessage(status="ok", message=body[:240] or "通知发送成功")
    except Exception as exc:
        row.last_test_status = "failed"
        row.last_test_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
