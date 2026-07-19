"""Embedded scheduler for self-hosted daily portfolio analysis."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler

from ..config import settings
from ..database import SessionLocal
from ..v2_models import AnalysisJob, PortfolioSnapshot, Schedule
from .analysis_engine import run_analysis_job
from .market_data import is_a_share_trading_day

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Invalid IANA timezone") from exc
    return value


def next_run(schedule: Schedule, now: datetime | None = None) -> datetime:
    timezone = ZoneInfo(validate_timezone(schedule.timezone))
    current = (now or datetime.now(UTC)).astimezone(timezone)
    candidate = current.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def _latest_snapshot(db, schedule: Schedule) -> PortfolioSnapshot | None:
    return (
        db.query(PortfolioSnapshot)
        .filter(
            PortfolioSnapshot.user_id == schedule.user_id,
            PortfolioSnapshot.portfolio_id == schedule.portfolio_id,
            PortfolioSnapshot.status == "confirmed",
        )
        .order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc())
        .first()
    )


def run_scheduled_job(job_id: int, schedule_id: int) -> None:
    run_analysis_job(job_id)
    db = SessionLocal()
    try:
        job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not job or not schedule:
            return
        if job.status == "succeeded":
            schedule.consecutive_failures = 0
        else:
            schedule.consecutive_failures += 1
            if schedule.consecutive_failures >= schedule.max_consecutive_failures:
                schedule.enabled = False
        schedule.next_run_at = next_run(schedule)
        db.commit()
    finally:
        db.close()


def create_scheduled_job(db, schedule: Schedule, *, force: bool = False) -> AnalysisJob:
    local_now = datetime.now(ZoneInfo(schedule.timezone))
    key = f"schedule:{schedule.id}:{local_now.date().isoformat()}:{schedule.checkpoint}"
    existing = db.query(AnalysisJob).filter(AnalysisJob.idempotency_key == key).first()
    if existing and not force:
        return existing
    snapshot = _latest_snapshot(db, schedule)
    if snapshot is None:
        raise RuntimeError("no_confirmed_snapshot")
    age_days = (datetime.now(UTC).date() - snapshot.snapshot_time.replace(tzinfo=UTC).date()).days
    if age_days > schedule.stale_snapshot_days:
        raise RuntimeError(f"snapshot_stale:{age_days}d")
    if existing and force:
        key = key + f":manual:{int(datetime.now(UTC).timestamp())}"
    job = AnalysisJob(
        user_id=schedule.user_id,
        portfolio_id=schedule.portfolio_id,
        snapshot_id=snapshot.id,
        trigger_type="scheduled",
        checkpoint=schedule.checkpoint,
        mode=schedule.mode,
        notify=schedule.notify,
        status="queued",
        current_stage="queued",
        progress_percent=0,
        idempotency_key=key,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def tick_schedules() -> None:
    db = SessionLocal()
    try:
        rows = db.query(Schedule).filter(Schedule.enabled.is_(True)).all()
        now_utc = datetime.now(UTC)
        trading_day_cache: bool | None = None
        for schedule in rows:
            try:
                timezone = ZoneInfo(validate_timezone(schedule.timezone))
                local = now_utc.astimezone(timezone)
                schedule.next_run_at = next_run(schedule, now_utc)
                if local.hour != schedule.hour or local.minute != schedule.minute:
                    continue
                if schedule.last_run_at and schedule.last_run_at.replace(tzinfo=UTC).astimezone(timezone).date() == local.date():
                    continue
                if trading_day_cache is None:
                    trading_day_cache = is_a_share_trading_day(local)
                if not trading_day_cache:
                    continue
                job = create_scheduled_job(db, schedule)
                schedule.last_run_at = now_utc
                schedule.next_run_at = next_run(schedule, now_utc)
                db.commit()
                threading.Thread(target=run_scheduled_job, args=(job.id, schedule.id), daemon=True).start()
            except Exception as exc:
                logger.exception("Schedule %s failed to enqueue", schedule.id)
                schedule.consecutive_failures += 1
                schedule.next_run_at = next_run(schedule, now_utc)
                if schedule.consecutive_failures >= schedule.max_consecutive_failures:
                    schedule.enabled = False
                db.commit()
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    if not settings.SCHEDULER_ENABLED or _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
    _scheduler.add_job(
        tick_schedules,
        "interval",
        seconds=max(settings.SCHEDULER_INTERVAL_SECONDS, 30),
        id="holdings-analysis-scheduler",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Embedded analysis scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
