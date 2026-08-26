"""Deterministic daily orchestration over the existing analysis and review facts."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..decision_contract import canonicalize_analysis_mode
from ..memory.models import DailyReviewRun, DecisionMemory, DecisionOutcome
from ..market_engine_models import DailyBarCache
from ..market_models import SecurityMaster, TradingCalendar
from ..market_runtime_models import ProviderHealth
from ..portfolio_models import TradeLedgerEntry, TradeLedgerRevision
from ..memory.review import run_daily_review
from ..config import settings
from ..services.analysis_admission import AnalysisJobAdmission, active_portfolio_analysis
from ..services.analysis_engine import run_analysis_job
from ..services.realtime_monitor import get_realtime_monitor
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from ..v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot
from .config import ANALYSIS_CHECKPOINTS, CHECKPOINTS, CHECKPOINT_BY_KEY, WORKFLOW_VERSION
from .models import DailyOperationalCheckpoint, DailyOperationalRun
from .timeline import WorkflowState, base_timeline, checkpoint_moment, china_time, derive_workflow_state

logger = logging.getLogger(__name__)

CHECKPOINT_TERMINAL_STATUSES = frozenset({
    "SUCCESS", "FAILED", "REUSED", "SKIPPED", "MISSED", "BLOCKED", "NOT_AVAILABLE",
})
JOB_STATUS_MAP = {
    "queued": "RUNNING",
    "running": "RUNNING",
    "retrying": "RUNNING",
    "succeeded": "SUCCESS",
    "failed": "FAILED",
    "cancelled": "SKIPPED",
}


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _day_bounds(local_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(local_date, time.min).replace(tzinfo=CHINA_TZ)
    end = datetime.combine(local_date + timedelta(days=1), time.min).replace(tzinfo=CHINA_TZ)
    return _naive_utc(start), _naive_utc(end)


def _job_status(job: AnalysisJob | None) -> str | None:
    if job is None:
        return None
    return JOB_STATUS_MAP.get(str(job.status).lower(), str(job.status).upper())


def _checkpoint_job(
    db: Session,
    *,
    portfolio: Portfolio,
    trade_date: date,
    checkpoint: str,
    as_of: datetime,
) -> AnalysisJob | None:
    """Find an existing fixed-checkpoint job, including legacy Schedule jobs."""

    key = checkpoint_idempotency_key(portfolio.id, trade_date, checkpoint)
    job = db.execute(select(AnalysisJob).where(
        AnalysisJob.user_id == portfolio.user_id,
        AnalysisJob.portfolio_id == portfolio.id,
        AnalysisJob.idempotency_key == key,
    )).scalar_one_or_none()
    if job is not None:
        return job
    start, end = _day_bounds(trade_date)
    return db.execute(select(AnalysisJob).where(
        AnalysisJob.user_id == portfolio.user_id,
        AnalysisJob.portfolio_id == portfolio.id,
        AnalysisJob.trigger_type == "scheduled",
        AnalysisJob.checkpoint == checkpoint,
        AnalysisJob.created_at >= start,
        AnalysisJob.created_at <= min(end, _naive_utc(as_of) or end),
    ).order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc()).limit(1)).scalar_one_or_none()


def ensure_operational_run(db: Session, *, user_id: int, portfolio_id: int, trade_date: date) -> DailyOperationalRun:
    row = db.execute(select(DailyOperationalRun).where(
        DailyOperationalRun.user_id == user_id,
        DailyOperationalRun.portfolio_id == portfolio_id,
        DailyOperationalRun.trade_date == trade_date,
        DailyOperationalRun.workflow_version == WORKFLOW_VERSION,
    )).scalar_one_or_none()
    if row is not None:
        row.checkpoint_state_json = dict(row.checkpoint_state_json or {})
        return row
    row = DailyOperationalRun(
        user_id=user_id,
        portfolio_id=portfolio_id,
        trade_date=trade_date,
        workflow_version=WORKFLOW_VERSION,
        status="RUNNING",
        checkpoint_state_json={},
        maintenance_result_json={},
        notification_state_json={},
        review_state_json={},
    )
    try:
        # Keep a uniqueness collision local to this insert.  A concurrent
        # worker must not roll back unrelated scheduler state in the caller's
        # transaction.
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        row = db.execute(select(DailyOperationalRun).where(
            DailyOperationalRun.user_id == user_id,
            DailyOperationalRun.portfolio_id == portfolio_id,
            DailyOperationalRun.trade_date == trade_date,
            DailyOperationalRun.workflow_version == WORKFLOW_VERSION,
        )).scalar_one()
    return row


def latest_snapshot(db: Session, *, user_id: int, portfolio_id: int, as_of: datetime | None = None) -> PortfolioSnapshot | None:
    query = select(PortfolioSnapshot).where(
        PortfolioSnapshot.user_id == user_id,
        PortfolioSnapshot.portfolio_id == portfolio_id,
        PortfolioSnapshot.status == "confirmed",
    )
    cutoff = _naive_utc(as_of)
    if cutoff is not None:
        query = query.where(PortfolioSnapshot.snapshot_time <= cutoff)
    return db.execute(query.order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).limit(1)).scalar_one_or_none()


def checkpoint_idempotency_key(portfolio_id: int, trade_date: date, checkpoint: str) -> str:
    return f"phase-h:{portfolio_id}:{trade_date.isoformat()}:{checkpoint}"


def claim_checkpoint(
    db: Session,
    *,
    portfolio: Portfolio,
    trade_date: date,
    checkpoint_name: str,
) -> tuple[DailyOperationalCheckpoint, bool]:
    """Claim one checkpoint with a database-enforced uniqueness boundary.

    The savepoint keeps a uniqueness collision local to this claim. A losing
    worker can therefore report an already-owned checkpoint without rolling
    back unrelated scheduler state in its outer transaction.
    """

    query = select(DailyOperationalCheckpoint).where(
        DailyOperationalCheckpoint.portfolio_id == portfolio.id,
        DailyOperationalCheckpoint.trade_date == trade_date,
        DailyOperationalCheckpoint.checkpoint_name == checkpoint_name,
        DailyOperationalCheckpoint.workflow_version == WORKFLOW_VERSION,
    )
    existing = db.execute(query).scalar_one_or_none()
    if existing is not None:
        return existing, False
    candidate = DailyOperationalCheckpoint(
        user_id=portfolio.user_id,
        portfolio_id=portfolio.id,
        trade_date=trade_date,
        checkpoint_name=checkpoint_name,
        workflow_version=WORKFLOW_VERSION,
        status="CLAIMED",
        metadata_json={},
    )
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
        return candidate, True
    except IntegrityError:
        existing = db.execute(query).scalar_one_or_none()
        if existing is None:
            raise
        return existing, False


def _finish_checkpoint_claim(
    claim: DailyOperationalCheckpoint,
    *,
    status: str,
    local: datetime,
    job_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    claim.status = status
    claim.job_id = job_id
    claim.completed_at = _naive_utc(local)
    claim.last_error = error
    payload = dict(claim.metadata_json or {})
    if reason:
        payload["reason"] = reason
    if metadata:
        payload.update(metadata)
    claim.metadata_json = payload


def _admit_checkpoint_job(
    db: Session,
    *,
    portfolio: Portfolio,
    trade_date: date,
    checkpoint: str,
    mode: str,
    now: datetime,
) -> AnalysisJobAdmission | None:
    key = checkpoint_idempotency_key(portfolio.id, trade_date, checkpoint)
    existing = db.execute(select(AnalysisJob).where(AnalysisJob.idempotency_key == key)).scalar_one_or_none()
    if existing is not None:
        source = "idempotency"
        return AnalysisJobAdmission(existing, should_start=False, source=source)
    existing = _checkpoint_job(
        db,
        portfolio=portfolio,
        trade_date=trade_date,
        checkpoint=checkpoint,
        as_of=now,
    )
    if existing is not None:
        return AnalysisJobAdmission(existing, should_start=False, source="existing_schedule")
    active = active_portfolio_analysis(db, user_id=portfolio.user_id, portfolio_id=portfolio.id)
    if active is not None:
        active.context_json = {
            **dict(active.context_json or {}),
            "shared_checkpoints": [
                *dict(active.context_json or {}).get("shared_checkpoints", []),
                {"trade_date": trade_date.isoformat(), "checkpoint": checkpoint},
            ],
        }
        db.flush()
        return AnalysisJobAdmission(active, should_start=False, source="active_portfolio")
    snapshot = latest_snapshot(db, user_id=portfolio.user_id, portfolio_id=portfolio.id, as_of=now)
    if snapshot is None:
        return None
    job = AnalysisJob(
        user_id=portfolio.user_id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        trigger_type="scheduled",
        checkpoint=checkpoint,
        mode=canonicalize_analysis_mode(mode),
        status="queued",
        current_stage="queued",
        progress_percent=0,
        notify=True,
        idempotency_key=key,
        context_json={"workflow_version": WORKFLOW_VERSION, "trade_date": trade_date.isoformat()},
    )
    db.add(job)
    db.flush()
    return AnalysisJobAdmission(job, should_start=True, source="created")


def _checkpoint_status(
    *,
    checkpoint: Any,
    local: datetime,
    state: dict[str, Any],
) -> tuple[str, bool]:
    scheduled = local.replace(hour=checkpoint.at.hour, minute=checkpoint.at.minute, second=0, microsecond=0)
    if local < scheduled:
        return "PENDING", False
    existing = state.get(checkpoint.key)
    if isinstance(existing, dict) and existing.get("status") in {*CHECKPOINT_TERMINAL_STATUSES, "RUNNING"}:
        return str(existing["status"]), False
    catch_up = checkpoint.catch_up_minutes
    if catch_up is not None and local > scheduled + timedelta(minutes=catch_up):
        return "MISSED", True
    return "DUE", True


def _checkpoint_record(
    state: dict[str, Any],
    *,
    key: str,
    local: datetime,
    status: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    record: dict[str, Any] = {
        "status": status,
        "scheduled_at": CHECKPOINT_BY_KEY[key].at.strftime("%H:%M"),
        "updated_at": local.isoformat(),
    }
    if reason:
        record["reason"] = reason
    if metadata:
        record.update(metadata)
    state[key] = record


def _maintenance_component(fn: Any) -> dict[str, Any]:
    try:
        result = fn()
        if isinstance(result, dict):
            return result
        return {"status": "OK", "value": result}
    except Exception as exc:  # maintenance items fail independently
        logger.warning("daily maintenance component failed", exc_info=True)
        return {"status": "DEGRADED", "reason": type(exc).__name__, "error": str(exc)[:300]}


def run_data_maintenance(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    trade_date: date,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Run only deterministic data readiness checks and pending Outcome work.

    This hook intentionally does not call a quote provider, model client, or
    candidate scan.  Each component is isolated so one stale feed does not
    hide the status of the other inputs.
    """

    cutoff = _naive_utc(as_of) or datetime.now(UTC).replace(tzinfo=None)

    def calendar_result() -> dict[str, Any]:
        row = TradingCalendarService(db).row_for(trade_date)
        return {"status": "OK" if row and row.is_open else "BLOCKED", "is_open": bool(row and row.is_open), "reason": None if row else "CALENDAR_MISSING"}

    def security_result() -> dict[str, Any]:
        count = db.scalar(select(func.count(SecurityMaster.id))) or 0
        return {"status": "OK" if count else "UNKNOWN", "security_count": int(count)}

    def provider_result() -> dict[str, Any]:
        rows = db.execute(select(ProviderHealth).where(ProviderHealth.data_type == "quote")).scalars().all()
        if not rows:
            return {"status": "UNKNOWN", "providers": []}
        healthy = [row for row in rows if str(row.status).upper() == "HEALTHY"]
        return {
            "status": "OK" if healthy else "DEGRADED",
            "providers": [{"provider": row.provider_name, "status": row.status, "updated_at": row.updated_at} for row in rows],
        }

    def daily_bar_result() -> dict[str, Any]:
        row = db.execute(select(DailyBarCache).where(
            DailyBarCache.market == "CN",
            DailyBarCache.trade_date <= trade_date,
            DailyBarCache.available_at.is_not(None),
            DailyBarCache.available_at <= cutoff,
        ).order_by(DailyBarCache.trade_date.desc(), DailyBarCache.available_at.desc(), DailyBarCache.id.desc()).limit(1)).scalar_one_or_none()
        return {"status": "OK" if row else "UNKNOWN", "trade_date": row.trade_date if row else None, "available_at": row.available_at if row else None}

    def memory_result() -> dict[str, Any]:
        from ..memory.outcomes import refresh_due_decision_outcomes

        result = refresh_due_decision_outcomes(
            db,
            user_id=user_id,
            portfolio_id=portfolio_id,
            calculation_as_of=as_of,
            persist=True,
        )
        return {"status": "OK", "result": result}

    result = {
        "calendar": _maintenance_component(calendar_result),
        "security_master": _maintenance_component(security_result),
        "quote_provider": _maintenance_component(provider_result),
        "daily_bars": _maintenance_component(daily_bar_result),
        "factor_data": {"status": "UNKNOWN", "reason": "NO_PHASE_H_FACTOR_SYNC_HOOK"},
        "memory_outcomes": _maintenance_component(memory_result),
    }
    result["status"] = "OK" if all(item.get("status") == "OK" for key, item in result.items() if key != "factor_data") else "DEGRADED"
    return result


def _run_monitor_lifecycle(local: datetime) -> dict[str, Any]:
    monitor = get_realtime_monitor()
    if not settings.REALTIME_MONITOR_ENABLED:
        if monitor.is_running():
            monitor.stop()
        return {"status": "DISABLED", "running": False}
    current = local.time()
    active = time(9, 30) <= current < time(11, 30) or time(13, 0) <= current < time(15, 0)
    if active:
        monitor.start()
    elif monitor.is_running():
        monitor.stop()
    return {"status": "RUNNING" if active and monitor.is_running() else "PAUSED" if time(11, 30) <= current < time(13, 0) else "STOPPED", "running": monitor.is_running()}


def run_due_checkpoints(db: Session, *, portfolio: Portfolio, now: datetime | None = None) -> dict[str, Any]:
    local = china_time(now)
    trade_date = local.date()
    calendar = TradingCalendarService(db)
    if not calendar.is_trading_day(trade_date):
        return {"status": "NON_TRADING_DAY", "trade_date": trade_date.isoformat(), "checkpoints": {}}
    op_run = ensure_operational_run(db, user_id=portfolio.user_id, portfolio_id=portfolio.id, trade_date=trade_date)
    state = dict(op_run.checkpoint_state_json or {})
    started_jobs: list[int] = []
    maintenance_result = None

    # Non-analysis checkpoints are state markers and deterministic hooks.  They
    # never create an AnalysisJob and never invoke an LLM.
    for checkpoint in CHECKPOINTS:
        if checkpoint.kind == "analysis":
            continue
        current, actionable = _checkpoint_status(checkpoint=checkpoint, local=local, state=state)
        if not actionable:
            continue
        # Daily Review is claimed by the existing review scheduler once a
        # review row exists; keep its pre-claim state visible as PENDING.
        if checkpoint.key != "daily_review":
            claim, owned = claim_checkpoint(
                db,
                portfolio=portfolio,
                trade_date=trade_date,
                checkpoint_name=checkpoint.key,
            )
            if not owned:
                prior_status = str(claim.status or "CLAIMED").upper()
                visible_status = prior_status if prior_status in CHECKPOINT_TERMINAL_STATUSES else "REUSED"
                _checkpoint_record(
                    state,
                    key=checkpoint.key,
                    local=local,
                    status=visible_status,
                    reason="CHECKPOINT_ALREADY_CLAIMED",
                    metadata={"checkpoint_claim_id": claim.id, "job_id": claim.job_id},
                )
                continue
        else:
            claim = None
        if current == "MISSED":
            _checkpoint_record(state, key=checkpoint.key, local=local, status="MISSED", reason="CHECKPOINT_CATCHUP_WINDOW_EXPIRED")
            if claim is not None:
                _finish_checkpoint_claim(claim, status="MISSED", local=local, reason="CHECKPOINT_CATCHUP_WINDOW_EXPIRED")
            continue
        if checkpoint.key == "maintenance":
            maintenance_result = run_data_maintenance(
                db,
                user_id=portfolio.user_id,
                portfolio_id=portfolio.id,
                trade_date=trade_date,
                as_of=local,
            )
            _checkpoint_record(
                state,
                key=checkpoint.key,
                local=local,
                status="SUCCESS" if maintenance_result.get("status") == "OK" else "DEGRADED",
                reason="DETERMINISTIC_DATA_MAINTENANCE",
            )
            op_run.maintenance_result_json = maintenance_result
            _finish_checkpoint_claim(claim, status="SUCCESS" if maintenance_result.get("status") == "OK" else "DEGRADED", local=local, reason="DETERMINISTIC_DATA_MAINTENANCE")
        elif checkpoint.key == "pre_market":
            _checkpoint_record(
                state,
                key=checkpoint.key,
                local=local,
                status="SUCCESS",
                reason="PREVIOUS_CLOSE_ONLY",
                metadata={"market_mode": "PRE_MARKET", "market_score_source": "PREVIOUS_CLOSE"},
            )
            _finish_checkpoint_claim(claim, status="SUCCESS", local=local, reason="PREVIOUS_CLOSE_ONLY", metadata={"market_mode": "PRE_MARKET", "market_score_source": "PREVIOUS_CLOSE"})
        elif checkpoint.key == "auction":
            _checkpoint_record(state, key=checkpoint.key, local=local, status="NOT_AVAILABLE", reason="AUCTION_OBSERVATION_HOOK_NOT_CONFIGURED")
            _finish_checkpoint_claim(claim, status="NOT_AVAILABLE", local=local, reason="AUCTION_OBSERVATION_HOOK_NOT_CONFIGURED")
        elif checkpoint.key == "monitor_start":
            monitor_state = _run_monitor_lifecycle(local)
            _checkpoint_record(state, key=checkpoint.key, local=local, status="SUCCESS", reason="MONITOR_LIFECYCLE_SCHEDULER_OWNED", metadata={"monitor": monitor_state})
            _finish_checkpoint_claim(claim, status="SUCCESS", local=local, reason="MONITOR_LIFECYCLE_SCHEDULER_OWNED", metadata={"monitor": monitor_state})
        elif checkpoint.key == "morning_snapshot":
            _checkpoint_record(state, key=checkpoint.key, local=local, status="SUCCESS", reason="MORNING_SNAPSHOT_HOOK")
            _finish_checkpoint_claim(claim, status="SUCCESS", local=local, reason="MORNING_SNAPSHOT_HOOK")
        elif checkpoint.key == "late_caution":
            _checkpoint_record(state, key=checkpoint.key, local=local, status="SUCCESS", reason="LATE_SESSION_REVIEW_ONLY")
            _finish_checkpoint_claim(claim, status="SUCCESS", local=local, reason="LATE_SESSION_REVIEW_ONLY")
        elif checkpoint.key == "market_close":
            monitor_state = _run_monitor_lifecycle(local)
            _checkpoint_record(state, key=checkpoint.key, local=local, status="SUCCESS", reason="CLOSE_SNAPSHOT_HOOK", metadata={"monitor": monitor_state})
            _finish_checkpoint_claim(claim, status="SUCCESS", local=local, reason="CLOSE_SNAPSHOT_HOOK", metadata={"monitor": monitor_state})
        elif checkpoint.key == "daily_review":
            review = db.execute(select(DailyReviewRun).where(
                DailyReviewRun.user_id == portfolio.user_id,
                DailyReviewRun.portfolio_id == portfolio.id,
                DailyReviewRun.trade_date == trade_date,
            ).order_by(DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
            if review is not None and review.status == "COMPLETED":
                _checkpoint_record(state, key=checkpoint.key, local=local, status="SUCCESS", reason="DAILY_REVIEW_COMPLETED", metadata={"review_id": review.id, "review_stale": bool(review.review_stale)})
            else:
                _checkpoint_record(state, key=checkpoint.key, local=local, status="PENDING", reason="DAILY_REVIEW_PENDING")
        elif checkpoint.key == "critical_event_hook":
            _checkpoint_record(state, key=checkpoint.key, local=local, status="SKIPPED", reason="NO_CRITICAL_EVENT_INTEGRATION")
            _finish_checkpoint_claim(claim, status="SKIPPED", local=local, reason="NO_CRITICAL_EVENT_INTEGRATION")

    for checkpoint in ANALYSIS_CHECKPOINTS:
        current, actionable = _checkpoint_status(checkpoint=checkpoint, local=local, state=state)
        if not actionable:
            continue
        claim, owned = claim_checkpoint(
            db,
            portfolio=portfolio,
            trade_date=trade_date,
            checkpoint_name=checkpoint.key,
        )
        if not owned:
            prior_status = str(claim.status or "CLAIMED").upper()
            visible_status = prior_status if prior_status in CHECKPOINT_TERMINAL_STATUSES else "REUSED"
            state[checkpoint.key] = {
                "status": visible_status,
                "job_id": claim.job_id,
                "source": "database_claim",
                "scheduled_at": checkpoint.at.strftime("%H:%M"),
                "updated_at": local.isoformat(),
                "reason": "CHECKPOINT_ALREADY_CLAIMED",
                "checkpoint_claim_id": claim.id,
            }
            continue
        if current == "MISSED":
            state[checkpoint.key] = {"status": "MISSED", "scheduled_at": checkpoint.at.strftime("%H:%M"), "updated_at": local.isoformat()}
            _finish_checkpoint_claim(claim, status="MISSED", local=local, reason="CHECKPOINT_CATCHUP_WINDOW_EXPIRED")
            continue
        admission = _admit_checkpoint_job(
            db,
            portfolio=portfolio,
            trade_date=trade_date,
            checkpoint=checkpoint.key,
            mode=checkpoint.mode or "standard",
            now=local,
        )
        if admission is None:
            state[checkpoint.key] = {"status": "BLOCKED", "reason": "confirmed_snapshot_not_found", "updated_at": local.isoformat()}
            _finish_checkpoint_claim(claim, status="BLOCKED", local=local, reason="confirmed_snapshot_not_found")
            continue
        job = admission.job
        status = "REUSED" if admission.source == "active_portfolio" else (_job_status(job) or "RUNNING")
        state[checkpoint.key] = {
            "status": status,
            "job_id": job.id,
            "source": admission.source,
            "scheduled_at": checkpoint.at.strftime("%H:%M"),
            "updated_at": local.isoformat(),
        }
        _finish_checkpoint_claim(
            claim,
            status=status,
            local=local,
            job_id=job.id,
            reason=admission.source,
            metadata={"source": admission.source},
        )
        if admission.should_start:
            started_jobs.append(job.id)
    op_run.checkpoint_state_json = state
    op_run.last_tick_at = _naive_utc(local)
    op_run.status = "RUNNING"
    db.flush()
    db.commit()
    for job_id in started_jobs:
        # Keep the existing analysis engine as the sole execution path without
        # blocking the scheduler's single interval worker.
        threading.Thread(
            target=run_analysis_job,
            args=(job_id,),
            name=f"daily-analysis-{job_id}",
            daemon=True,
        ).start()
    logger.info("daily_workflow portfolio=%s trade_date=%s state=%s jobs=%s", portfolio.id, trade_date.isoformat(), derive_workflow_state(db, as_of=local).value, started_jobs)
    return {"status": "OK", "trade_date": trade_date.isoformat(), "checkpoints": state, "started_jobs": started_jobs, "maintenance": maintenance_result}


def refresh_review_state(db: Session, *, user_id: int, portfolio_id: int, trade_date: date, as_of: datetime | None = None, force: bool = False) -> dict[str, Any]:
    op_run = ensure_operational_run(db, user_id=user_id, portfolio_id=portfolio_id, trade_date=trade_date)
    review = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == user_id,
        DailyReviewRun.portfolio_id == portfolio_id,
        DailyReviewRun.trade_date == trade_date,
    ).order_by(DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
    metadata = dict(op_run.review_state_json or {})
    if review is not None and review.status == "COMPLETED":
        stale_reasons = review_staleness_reasons(
            db,
            review=review,
            as_of=as_of,
        )
        if stale_reasons:
            review.review_stale = True
            stale_event = {
                "source": "review_staleness_detector",
                "reasons": stale_reasons,
                "detected_at": datetime.now(UTC).isoformat(),
            }
            stale_events = [*list(metadata.get("stale_events") or []), stale_event][-20:]
            metadata.update({
                "review_stale": True,
                "stale_reason": stale_reasons[0],
                "stale_reasons": stale_reasons,
                "stale_at": datetime.now(UTC).isoformat(),
                "stale_events": stale_events,
            })
            op_run.review_state_json = metadata
            db.flush()
            db.commit()
            force = True
    if review is None:
        review = run_daily_review(db, user_id=user_id, portfolio_id=portfolio_id, trade_date=trade_date, as_of=as_of, force=force)
    elif force or review.review_stale:
        review = run_daily_review(db, user_id=user_id, portfolio_id=portfolio_id, trade_date=trade_date, as_of=as_of, force=True)
    if review is not None:
        metadata.update({
            "review_id": review.id,
            "review_stale": bool(review.review_stale),
            "refresh_count": int(review.refresh_count or 0),
            "last_refreshed_at": review.last_refreshed_at.isoformat() if review.last_refreshed_at else (review.completed_at.isoformat() if review.completed_at else None),
        })
        if not review.review_stale:
            for key in ("stale_reason", "stale_reasons", "stale_at"):
                metadata.pop(key, None)
        op_run.review_state_json = metadata
        db.commit()
        try:
            from .notifications import dispatch_material_events

            dispatch_material_events(
                db,
                user_id=user_id,
                portfolio_id=portfolio_id,
                as_of=as_of,
            )
        except Exception:
            # Review completion is authoritative; notification delivery is not.
            logger.exception("Operating notification dispatch failed after daily review")
    return metadata


def mark_review_stale(db: Session, *, user_id: int, portfolio_id: int, trade_date: date, reason: str) -> dict[str, Any]:
    op_run = ensure_operational_run(db, user_id=user_id, portfolio_id=portfolio_id, trade_date=trade_date)
    metadata = dict(op_run.review_state_json or {})
    marked_at = datetime.now(UTC).isoformat()
    stale_events = [*list(metadata.get("stale_events") or []), {"source": "explicit_marker", "reason": reason, "marked_at": marked_at}][-20:]
    metadata.update({
        "review_stale": True,
        "stale_reason": reason,
        "stale_reasons": list(dict.fromkeys([*list(metadata.get("stale_reasons") or []), reason])),
        "stale_at": marked_at,
        "stale_events": stale_events,
    })
    review = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == user_id,
        DailyReviewRun.portfolio_id == portfolio_id,
        DailyReviewRun.trade_date == trade_date,
    ).order_by(DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
    if review is not None:
        review.review_stale = True
    op_run.review_state_json = metadata
    db.commit()
    return metadata


def review_staleness_reasons(
    db: Session,
    *,
    review: DailyReviewRun,
    as_of: datetime | None = None,
) -> list[str]:
    """Return late facts that arrived after a completed ReviewRun."""

    if review.status != "COMPLETED" or review.completed_at is None:
        return []
    completed_at = _naive_utc(review.completed_at)
    cutoff = _naive_utc(as_of) or datetime.now(UTC).replace(tzinfo=None)
    reasons: list[str] = []
    outcomes = db.execute(select(DecisionOutcome).join(
        DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id,
    ).where(
        DecisionMemory.user_id == review.user_id,
        DecisionMemory.portfolio_id == review.portfolio_id,
        DecisionOutcome.status.in_(("VALID", "DEGRADED")),
        DecisionOutcome.available_at.is_not(None),
        DecisionOutcome.available_at > completed_at,
        DecisionOutcome.available_at <= cutoff,
    )).scalars().all()
    if any(_local_china_date(row.available_at) == review.trade_date for row in outcomes):
        reasons.append("OUTCOME_MATURED_AFTER_REVIEW")
    revised_outcome = db.execute(select(DecisionOutcome).join(
        DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id,
    ).where(
        DecisionMemory.user_id == review.user_id,
        DecisionMemory.portfolio_id == review.portfolio_id,
        DecisionOutcome.last_source_change_at.is_not(None),
        DecisionOutcome.last_source_change_at > completed_at,
        DecisionOutcome.last_source_change_at <= cutoff,
    ).limit(1)).scalar_one_or_none()
    if revised_outcome is not None:
        reasons.append("OUTCOME_SOURCE_REVISED_AFTER_REVIEW")
    revisions = db.execute(select(TradeLedgerRevision).join(
        TradeLedgerEntry, TradeLedgerRevision.ledger_entry_id == TradeLedgerEntry.id,
    ).where(
        TradeLedgerEntry.user_id == review.user_id,
        TradeLedgerEntry.portfolio_id == review.portfolio_id,
        TradeLedgerRevision.created_at > completed_at,
        TradeLedgerRevision.created_at <= cutoff,
    )).scalars().all()
    if revisions:
        reasons.append("EXECUTION_RECORD_REVISED_AFTER_REVIEW")
    return reasons


def _local_china_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.astimezone(CHINA_TZ).date()


def operational_timeline(db: Session, *, portfolio_id: int, user_id: int, as_of: datetime | None = None) -> dict[str, Any]:
    local = china_time(as_of)
    calendar = TradingCalendarService(db)
    trading_day = calendar.is_trading_day(local.date())
    op_run = db.execute(select(DailyOperationalRun).where(
        DailyOperationalRun.user_id == user_id,
        DailyOperationalRun.portfolio_id == portfolio_id,
        DailyOperationalRun.trade_date == local.date(),
        DailyOperationalRun.workflow_version == WORKFLOW_VERSION,
    ).order_by(DailyOperationalRun.id.desc()).limit(1)).scalar_one_or_none()
    timeline = base_timeline(local)
    state = dict(op_run.checkpoint_state_json or {}) if op_run is not None else {}
    portfolio = db.execute(select(Portfolio).where(
        Portfolio.id == portfolio_id,
        Portfolio.user_id == user_id,
    )).scalar_one_or_none()
    for item in timeline:
        checkpoint_state = state.get(item["key"])
        if checkpoint_state:
            item.update(checkpoint_state)
            job_id = checkpoint_state.get("job_id")
            if job_id and checkpoint_state.get("status") != "REUSED":
                job = db.execute(select(AnalysisJob).where(
                    AnalysisJob.id == job_id,
                    AnalysisJob.user_id == user_id,
                    AnalysisJob.portfolio_id == portfolio_id,
                )).scalar_one_or_none()
                if job is not None:
                    item["status"] = JOB_STATUS_MAP.get(str(job.status).lower(), str(job.status).upper())
                    item["started_at"] = job.started_at.isoformat() if job.started_at else None
                    item["finished_at"] = job.finished_at.isoformat() if job.finished_at else None
        elif not trading_day:
            item["status"] = "SKIPPED"
            item["reason"] = "NON_TRADING_DAY"
        elif item["kind"] == "analysis":
            checkpoint = CHECKPOINT_BY_KEY[item["key"]]
            scheduled = checkpoint_moment(local, checkpoint.at)
            if local < scheduled:
                item["status"] = "PENDING"
                item["reason"] = "CHECKPOINT_PENDING"
            elif checkpoint.catch_up_minutes is not None and local > scheduled + timedelta(minutes=checkpoint.catch_up_minutes):
                item["status"] = "MISSED"
                item["reason"] = "CHECKPOINT_CATCHUP_WINDOW_EXPIRED"
            else:
                item["status"] = "PENDING"
                item["reason"] = "CHECKPOINT_PENDING"
        else:
            scheduled = checkpoint_moment(local, CHECKPOINT_BY_KEY[item["key"]].at)
            item["status"] = "PENDING" if local < scheduled else "SKIPPED"
            item["reason"] = "CHECKPOINT_PENDING" if item["status"] == "PENDING" else "CHECKPOINT_NOT_RECORDED"
    review = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == user_id,
        DailyReviewRun.portfolio_id == portfolio_id,
        DailyReviewRun.trade_date == local.date(),
        DailyReviewRun.created_at <= _naive_utc(local),
        or_(DailyReviewRun.completed_at.is_(None), DailyReviewRun.completed_at <= _naive_utc(local)),
        or_(DailyReviewRun.last_refreshed_at.is_(None), DailyReviewRun.last_refreshed_at <= _naive_utc(local)),
    ).order_by(DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
    state_value = derive_workflow_state(
        db,
        as_of=local,
        review_complete=bool(review and review.status == "COMPLETED"),
        review_stale=bool(review and review.review_stale),
    )
    monitor_status = get_realtime_monitor().status()
    monitor_status_value = str(monitor_status.get("status") or "stopped").upper()
    return {
        "as_of": local.isoformat(),
        "trade_date": local.date().isoformat(),
        "workflow_state": state_value.value,
        "previous_trading_day": calendar.previous_trading_day(local.date()),
        "next_trading_day": calendar.next_trading_day(local.date()),
        "monitor": {
            "status": ("PAUSED_LUNCH" if state_value == WorkflowState.LUNCH_BREAK else "RUNNING" if state_value in {WorkflowState.MORNING_SESSION, WorkflowState.AFTERNOON_SESSION, WorkflowState.LATE_SESSION} else "STOPPED"),
            "runtime": {**monitor_status, "status": monitor_status_value},
        },
        "operational_run_id": op_run.id if op_run is not None else None,
        "timeline": timeline,
    }


def reconcile_today(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recover only today's missed read-side operating state."""

    local = china_time(now)
    calendar = TradingCalendarService(db)
    if not calendar.is_trading_day(local.date()):
        return {
            "status": "NON_TRADING_DAY",
            "trade_date": local.date().isoformat(),
            "workflow_state": WorkflowState.NON_TRADING_DAY.value,
            "timeline": operational_timeline(db, user_id=user_id, portfolio_id=portfolio_id, as_of=local),
        }
    portfolio = db.execute(select(Portfolio).where(
        Portfolio.id == portfolio_id,
        Portfolio.user_id == user_id,
    )).scalar_one_or_none()
    if portfolio is None:
        raise ValueError("portfolio_not_found")
    monitor = _run_monitor_lifecycle(local)
    checkpoint_result = run_due_checkpoints(db, portfolio=portfolio, now=local)
    review_result: dict[str, Any] | None = None
    if local.time() >= time(15, 30):
        review = db.execute(select(DailyReviewRun).where(
            DailyReviewRun.user_id == user_id,
            DailyReviewRun.portfolio_id == portfolio_id,
            DailyReviewRun.trade_date == local.date(),
        ).order_by(DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
        if (review is None or review.review_stale) and (review is None or review.status != "RUNNING"):
            review_result = refresh_review_state(
                db,
                user_id=user_id,
                portfolio_id=portfolio_id,
                trade_date=local.date(),
                as_of=local,
                force=bool(review and review.review_stale),
            )
    timeline = operational_timeline(db, user_id=user_id, portfolio_id=portfolio_id, as_of=local)
    logger.info(
        "daily_workflow_reconcile portfolio=%s trade_date=%s state=%s monitor=%s",
        portfolio_id,
        local.date().isoformat(),
        timeline["workflow_state"],
        monitor.get("status"),
    )
    return {
        "status": "OK",
        "trade_date": local.date().isoformat(),
        "workflow_state": timeline["workflow_state"],
        "monitor": monitor,
        "checkpoints": checkpoint_result,
        "review": review_result,
        "timeline": timeline,
    }


__all__ = [
    "checkpoint_idempotency_key",
    "ensure_operational_run",
    "latest_snapshot",
    "mark_review_stale",
    "operational_timeline",
    "reconcile_today",
    "refresh_review_state",
    "review_staleness_reasons",
    "run_data_maintenance",
    "run_due_checkpoints",
]
