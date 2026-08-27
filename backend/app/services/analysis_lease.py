"""Restart-safe lease heartbeat for checkpoint-backed analysis workers."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..operations.config import ANALYSIS_CLAIM_LEASE
from ..operations.models import DailyOperationalCheckpoint
from ..v2_models import AnalysisJob

logger = logging.getLogger(__name__)

CHECKPOINT_ACTIVE_STATUSES = ("CLAIMED", "RUNNING")


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _now_naive_utc(value: datetime | None = None) -> datetime:
    return _naive_utc(value) or datetime.now(UTC).replace(tzinfo=None)


def checkpoint_attempt_for_job(db: Session, *, job_id: int) -> int | None:
    """Return the current claim generation for a checkpoint-backed job."""

    claim = db.execute(
        select(DailyOperationalCheckpoint)
        .where(
            DailyOperationalCheckpoint.job_id == job_id,
            DailyOperationalCheckpoint.status.in_(CHECKPOINT_ACTIVE_STATUSES),
        )
        .order_by(DailyOperationalCheckpoint.id.desc())
        .limit(1)
    ).scalars().first()
    if claim is None:
        return None
    return int(claim.attempt_count or 1)


def renew_analysis_checkpoint_lease(
    db: Session,
    *,
    job_id: int,
    attempt_count: int,
    now: datetime | None = None,
) -> bool:
    """Renew a live checkpoint lease using its attempt count as a CAS token.

    A stale worker cannot renew after another process increments the claim's
    attempt count during reclaim.  The lease must still be alive when the
    heartbeat arrives, so a delayed or dead worker cannot resurrect it.
    """

    moment = _now_naive_utc(now)
    live_lease = (
        (DailyOperationalCheckpoint.lease_expires_at.is_not(None)
         & (DailyOperationalCheckpoint.lease_expires_at > moment))
        | (
            DailyOperationalCheckpoint.lease_expires_at.is_(None)
            & (
                DailyOperationalCheckpoint.claimed_at.is_not(None)
                & (DailyOperationalCheckpoint.claimed_at > moment - ANALYSIS_CLAIM_LEASE)
            )
        )
    )
    try:
        result = db.execute(
            update(DailyOperationalCheckpoint)
            .where(
                DailyOperationalCheckpoint.job_id == job_id,
                DailyOperationalCheckpoint.attempt_count == attempt_count,
                DailyOperationalCheckpoint.status.in_(CHECKPOINT_ACTIVE_STATUSES),
                live_lease,
            )
            .values(
                status="RUNNING",
                lease_expires_at=moment + ANALYSIS_CLAIM_LEASE,
                updated_at=moment,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return bool(result.rowcount)


def reclaim_running_analysis_job(db: Session, *, job_id: int) -> bool:
    """CAS a running job to retrying after its checkpoint lease expired."""

    result = db.execute(
        update(AnalysisJob)
        .where(
            AnalysisJob.id == job_id,
            AnalysisJob.status == "running",
        )
        .values(
            status="retrying",
            current_stage="retrying",
            progress_percent=0,
            started_at=None,
            finished_at=None,
            error_code="analysis_worker_lease_expired",
            error_message="Analysis worker lease expired; retrying the same job.",
            retry_count=func.coalesce(AnalysisJob.retry_count, 0) + 1,
        )
    )
    if result.rowcount:
        db.flush()
        return True
    return False


class AnalysisLeaseHeartbeat:
    """Background heartbeat owned by one analysis worker thread."""

    def __init__(
        self,
        *,
        job_id: int,
        attempt_count: int,
        interval_seconds: float | None = None,
        session_factory=SessionLocal,
    ) -> None:
        self.job_id = job_id
        self.attempt_count = attempt_count
        self.interval_seconds = interval_seconds or max(1.0, ANALYSIS_CLAIM_LEASE.total_seconds() / 3)
        self._session_factory = session_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lost = False

    @classmethod
    def for_job(
        cls,
        db: Session,
        *,
        job_id: int,
        interval_seconds: float | None = None,
        session_factory=SessionLocal,
    ) -> "AnalysisLeaseHeartbeat | None":
        attempt_count = checkpoint_attempt_for_job(db, job_id=job_id)
        if attempt_count is None:
            return None
        return cls(
            job_id=job_id,
            attempt_count=attempt_count,
            interval_seconds=interval_seconds,
            session_factory=session_factory,
        )

    @property
    def lost(self) -> bool:
        return self._lost

    def beat(self, *, now: datetime | None = None) -> bool:
        if self._lost:
            return False
        db = self._session_factory()
        try:
            renewed = renew_analysis_checkpoint_lease(
                db,
                job_id=self.job_id,
                attempt_count=self.attempt_count,
                now=now,
            )
            if not renewed:
                self._lost = True
            return renewed
        except Exception:
            # A transient database error should not permanently kill the
            # heartbeat; the next interval gets another chance to renew.
            logger.exception("Analysis lease heartbeat failed for job %s", self.job_id)
            return True
        finally:
            db.close()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"analysis-lease-{self.job_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            join = getattr(self._thread, "join", None)
            if callable(join):
                join(timeout=min(self.interval_seconds, 1.0))

    def _run(self) -> None:
        if not self.beat():
            return
        while not self._stop.wait(self.interval_seconds):
            if not self.beat():
                return


__all__ = [
    "AnalysisLeaseHeartbeat",
    "checkpoint_attempt_for_job",
    "reclaim_running_analysis_job",
    "renew_analysis_checkpoint_lease",
]
