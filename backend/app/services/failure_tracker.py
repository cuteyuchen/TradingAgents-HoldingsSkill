"""Health/failure tracking for the consecutive-failure degradation rule (#8/#15).

The skill reports fetch outcomes per checkpoint; this service keeps a rolling
counter. After `consecutive_failure_threshold` (default 3) failures, the
checkpoint is marked degraded.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..models import HealthLog

FAILURE_THRESHOLD = 3


def _get_or_create(db: Session, checkpoint: str) -> HealthLog:
    row = db.query(HealthLog).filter(HealthLog.checkpoint == checkpoint).first()
    if not row:
        row = HealthLog(checkpoint=checkpoint, consecutive_failures=0)
        db.add(row)
        db.flush()
    return row


def record_failure(db: Session, checkpoint: str, note: str | None = None) -> HealthLog:
    row = _get_or_create(db, checkpoint)
    row.consecutive_failures += 1
    row.last_failure_at = datetime.now(UTC)
    if note:
        row.note = note
    db.commit()
    return row


def record_success(db: Session, checkpoint: str) -> HealthLog:
    row = _get_or_create(db, checkpoint)
    row.consecutive_failures = 0
    row.last_success_at = datetime.now(UTC)
    db.commit()
    return row


def is_degraded(row: HealthLog) -> bool:
    return row.consecutive_failures >= FAILURE_THRESHOLD


def all_status(db: Session) -> list[HealthLog]:
    return db.query(HealthLog).order_by(HealthLog.checkpoint).all()
