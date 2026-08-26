"""Embedded scheduler for self-hosted daily portfolio analysis."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.exc import IntegrityError
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..decision_contract import canonicalize_analysis_mode
from ..v2_models import AnalysisJob, PortfolioSnapshot, Schedule
from ..v2_models import Portfolio
from .analysis_admission import AnalysisJobAdmission, active_portfolio_analysis
from .analysis_engine import run_analysis_job
from ..memory.models import DailyReviewRun
from ..memory.review import run_daily_review, serialize_daily_review
from .trading_calendar import CHINA_TZ, TradingCalendarService, next_open_date

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Invalid IANA timezone") from exc
    return value


def _as_china_time(value: datetime | None = None) -> datetime:
    """Interpret naive scheduler inputs as Shanghai wall-clock time."""

    if value is None:
        return datetime.now(CHINA_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=CHINA_TZ)
    return value.astimezone(CHINA_TZ)


def next_run(
    schedule: Schedule,
    now: datetime | None = None,
    db: Session | None = None,
) -> datetime | None:
    """Return the next persisted CN open day at the schedule's Shanghai time."""

    current = _as_china_time(now)
    candidate = current.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)

    owns_session = db is None
    calendar_db = db or SessionLocal()
    try:
        open_date = next_open_date(calendar_db, candidate.date())
    finally:
        if owns_session:
            calendar_db.close()
    if open_date is None:
        logger.warning(
            "Trading calendar missing open date on or after %s; schedule %s is fail-closed",
            candidate.date().isoformat(),
            getattr(schedule, "id", None),
        )
        return None
    return candidate.replace(
        year=open_date.year,
        month=open_date.month,
        day=open_date.day,
    ).astimezone(UTC)


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
        schedule.next_run_at = next_run(schedule, db=db)
        db.commit()
    finally:
        db.close()


def run_scheduled_daily_review(*, user_id: int, portfolio_id: int, trade_date: date) -> None:
    """Run memory maintenance in its own Session; never creates an AnalysisJob."""

    db = SessionLocal()
    try:
        from ..operations.workflow import refresh_review_state

        metadata = refresh_review_state(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            trade_date=trade_date,
            force=True,
        )
        row = db.query(DailyReviewRun).filter(
            DailyReviewRun.user_id == user_id,
            DailyReviewRun.portfolio_id == portfolio_id,
            DailyReviewRun.trade_date == trade_date,
            DailyReviewRun.review_version == "daily-review-v1",
        ).first()
        if row is not None:
            logger.info(
                "daily_review portfolio=%s trade_date=%s decisions=%s matured_outcomes=%s quality=%s",
                portfolio_id,
                trade_date.isoformat(),
                row.decision_count,
                row.outcomes_matured_count,
                row.quality_status,
            )
            if metadata.get("refresh_count"):
                logger.info(
                    "review_refresh portfolio=%s trade_date=%s review=%s refresh_count=%s",
                    portfolio_id,
                    trade_date.isoformat(),
                    row.id,
                    metadata["refresh_count"],
                )
    except Exception:
        logger.exception("Daily Review maintenance failed for portfolio %s", portfolio_id)
    finally:
        db.close()


def _enqueue_memory_reviews(db: Session, *, trade_date: date, now_utc: datetime) -> None:
    """Claim one review per portfolio after the 15:30 checkpoint, including catch-up."""

    local = now_utc.astimezone(CHINA_TZ)
    if local.time() < time(15, 30):
        return
    for portfolio in db.query(Portfolio).order_by(Portfolio.id.asc()).all():
        existing = db.query(DailyReviewRun).filter(
            DailyReviewRun.user_id == portfolio.user_id,
            DailyReviewRun.portfolio_id == portfolio.id,
            DailyReviewRun.trade_date == trade_date,
            DailyReviewRun.review_version == "daily-review-v1",
        ).first()
        if existing is not None and existing.status == "RUNNING":
            continue
        if existing is not None and existing.status == "COMPLETED":
            from ..operations.workflow import review_staleness_reasons

            reasons = review_staleness_reasons(db, review=existing, as_of=now_utc)
            if not existing.review_stale and not reasons:
                continue
            existing.review_stale = True
            existing.status = "RUNNING"
            db.commit()
            threading.Thread(
                target=run_scheduled_daily_review,
                kwargs={
                    "user_id": portfolio.user_id,
                    "portfolio_id": portfolio.id,
                    "trade_date": trade_date,
                },
                daemon=True,
            ).start()
            continue
        if existing is None:
            existing = DailyReviewRun(
                user_id=portfolio.user_id,
                portfolio_id=portfolio.id,
                trade_date=trade_date,
                status="RUNNING",
                review_version="daily-review-v1",
            )
            db.add(existing)
        else:
            existing.status = "RUNNING"
        try:
            db.commit()
        except IntegrityError:
            # Another scheduler process may have claimed the same unique day.
            # The database constraint is the cross-process single-flight guard.
            db.rollback()
            continue
        threading.Thread(
            target=run_scheduled_daily_review,
            kwargs={
                "user_id": portfolio.user_id,
                "portfolio_id": portfolio.id,
                "trade_date": trade_date,
            },
            daemon=True,
        ).start()


def _sync_review_checkpoint(db: Session, *, portfolio: Portfolio, trade_date: date, now_utc: datetime) -> None:
    """Reflect the existing DailyReviewRun in the lightweight operation state."""

    from ..operations.workflow import ensure_operational_run

    op_run = ensure_operational_run(
        db,
        user_id=portfolio.user_id,
        portfolio_id=portfolio.id,
        trade_date=trade_date,
    )
    state = dict(op_run.checkpoint_state_json or {})
    review = db.query(DailyReviewRun).filter(
        DailyReviewRun.user_id == portfolio.user_id,
        DailyReviewRun.portfolio_id == portfolio.id,
        DailyReviewRun.trade_date == trade_date,
        DailyReviewRun.review_version == "daily-review-v1",
    ).order_by(DailyReviewRun.id.desc()).first()
    if review is not None:
        state["daily_review"] = {
            "status": "SUCCESS" if review.status == "COMPLETED" else review.status,
            "review_id": review.id,
            "review_stale": bool(review.review_stale),
            "updated_at": now_utc.isoformat(),
        }
    elif now_utc.astimezone(CHINA_TZ).time() >= time(15, 30):
        state.setdefault("daily_review", {"status": "PENDING", "updated_at": now_utc.isoformat()})
    op_run.checkpoint_state_json = state
    op_run.last_tick_at = now_utc.replace(tzinfo=None)
    db.flush()


def _run_daily_operations(db: Session, *, now_utc: datetime, portfolios: list[Portfolio]) -> None:
    """Run Phase H orchestration through the existing scheduler tick."""

    if not portfolios:
        return
    from ..operations.workflow import run_due_checkpoints

    local = now_utc.astimezone(CHINA_TZ)
    for portfolio in portfolios:
        try:
            run_due_checkpoints(db, portfolio=portfolio, now=local)
            _sync_review_checkpoint(db, portfolio=portfolio, trade_date=local.date(), now_utc=now_utc)
        except Exception:
            logger.exception("daily_workflow portfolio=%s failed", portfolio.id)


def _sync_monitor_lifecycle(now_utc: datetime, *, calendar: TradingCalendarService) -> None:
    """Restart-safe monitor lifecycle driven by the same scheduler clock."""

    from .realtime_monitor import get_realtime_monitor

    local = now_utc.astimezone(CHINA_TZ)
    monitor = get_realtime_monitor()
    if not calendar.is_trading_day(local.date()):
        if monitor.is_running():
            monitor.stop()
        return
    if time(9, 30) <= local.time() < time(11, 30) or time(13, 0) <= local.time() < time(15, 0):
        if settings.REALTIME_MONITOR_ENABLED:
            monitor.start()
    elif time(11, 30) <= local.time() < time(13, 0) or local.time() >= time(15, 0) or local.time() < time(9, 30):
        if monitor.is_running():
            monitor.stop()


def _fixed_checkpoint_is_missed(schedule: Schedule, *, local: datetime) -> bool:
    """Keep legacy Schedule jobs within the Phase H catch-up contract."""

    from ..operations.config import CHECKPOINT_BY_KEY

    checkpoint = CHECKPOINT_BY_KEY.get(str(schedule.checkpoint))
    if checkpoint is None or checkpoint.catch_up_minutes is None:
        return False
    scheduled = local.replace(
        hour=checkpoint.at.hour,
        minute=checkpoint.at.minute,
        second=0,
        microsecond=0,
    )
    return local > scheduled + timedelta(minutes=checkpoint.catch_up_minutes)


def create_scheduled_job(
    db,
    schedule: Schedule,
    *,
    force: bool = False,
    with_admission: bool = False,
) -> AnalysisJob | AnalysisJobAdmission:
    """Create a scheduled job or reuse the portfolio's active full analysis."""

    local_now = datetime.now(CHINA_TZ)
    key = f"schedule:{schedule.id}:{local_now.date().isoformat()}:{schedule.checkpoint}"
    existing = db.query(AnalysisJob).filter(AnalysisJob.idempotency_key == key).first()
    if existing and not force:
        admission = AnalysisJobAdmission(existing, should_start=False, source="idempotency")
        return admission if with_admission else admission.job
    active = active_portfolio_analysis(
        db,
        user_id=schedule.user_id,
        portfolio_id=schedule.portfolio_id,
    )
    if active is not None:
        admission = AnalysisJobAdmission(active, should_start=False, source="active_portfolio")
        return admission if with_admission else admission.job
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
        mode=canonicalize_analysis_mode(schedule.mode),
        notify=schedule.notify,
        status="queued",
        current_stage="queued",
        progress_percent=0,
        idempotency_key=key,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    admission = AnalysisJobAdmission(job, should_start=True, source="created")
    return admission if with_admission else admission.job


def tick_schedules() -> None:
    db = SessionLocal()
    try:
        rows = db.query(Schedule).filter(Schedule.enabled.is_(True)).all()
        now_utc = datetime.now(UTC)
        local = now_utc.astimezone(CHINA_TZ)
        calendar = TradingCalendarService(db)
        _sync_monitor_lifecycle(now_utc, calendar=calendar)
        calendar_row = calendar.row_for(local.date())
        if calendar_row is None:
            logger.warning(
                "Trading calendar has no CN row for %s; scheduled analysis is fail-closed",
                local.date().isoformat(),
            )
            for schedule in rows:
                schedule.next_run_at = next_run(schedule, now_utc, db=db)
            db.commit()
            return
        if not calendar_row.is_open:
            for schedule in rows:
                schedule.next_run_at = next_run(schedule, now_utc, db=db)
            db.commit()
            return

        for schedule in rows:
            try:
                schedule.next_run_at = next_run(schedule, now_utc, db=db)
                current_minute = local.hour * 60 + local.minute
                checkpoint_minute = schedule.hour * 60 + schedule.minute
                if current_minute < checkpoint_minute:
                    continue
                if schedule.last_run_at and schedule.last_run_at.replace(tzinfo=UTC).astimezone(CHINA_TZ).date() == local.date():
                    continue
                if _fixed_checkpoint_is_missed(schedule, local=local):
                    schedule.last_run_at = now_utc
                    logger.info(
                        "checkpoint portfolio=%s checkpoint=%s status=MISSED",
                        schedule.portfolio_id,
                        schedule.checkpoint,
                    )
                    db.commit()
                    continue
                admission = create_scheduled_job(db, schedule, with_admission=True)
                if isinstance(admission, AnalysisJobAdmission):
                    job = admission.job
                    should_start = admission.should_start
                else:
                    # Compatibility for tests and third-party callers that
                    # still replace ``create_scheduled_job`` with a Job.
                    job = admission
                    should_start = True
                schedule.last_run_at = now_utc
                schedule.next_run_at = next_run(schedule, now_utc, db=db)
                db.commit()
                if should_start:
                    threading.Thread(target=run_scheduled_job, args=(job.id, schedule.id), daemon=True).start()
            except Exception as exc:
                logger.exception("Schedule %s failed to enqueue", schedule.id)
                schedule.consecutive_failures += 1
                schedule.next_run_at = next_run(schedule, now_utc, db=db)
                if schedule.consecutive_failures >= schedule.max_consecutive_failures:
                    schedule.enabled = False
                db.commit()
        _enqueue_memory_reviews(db, trade_date=local.date(), now_utc=now_utc)
        try:
            tables = inspect(db.bind).get_table_names() if db.bind is not None else []
            if "portfolios" in tables and "daily_operational_runs" in tables:
                portfolios = db.query(Portfolio).order_by(Portfolio.id.asc()).all()
                _run_daily_operations(db, now_utc=now_utc, portfolios=portfolios)
                db.commit()
        except Exception:
            logger.exception("daily_workflow tick failed")
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


def scheduler_running() -> bool:
    return bool(_scheduler is not None and _scheduler.running)
