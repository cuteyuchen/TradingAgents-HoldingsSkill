"""Per-checkpoint health status (optimization #8/#15)."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import require_token
from ..database import get_db
from ..services import failure_tracker

router = APIRouter(prefix="/api/v1/health", tags=["health"])


class Outcome(BaseModel):
    checkpoint: str
    success: bool
    code: str | None = None
    note: str | None = None


@router.get("")
def health_status(
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    rows = failure_tracker.all_status(db)
    return [
        {
            "code": r.code,
            "checkpoint": r.checkpoint,
            "consecutive_failures": r.consecutive_failures,
            "degraded": failure_tracker.is_degraded(r),
            "last_failure_at": r.last_failure_at.isoformat() if r.last_failure_at else None,
            "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
            "note": r.note,
        }
        for r in rows
    ]


@router.post("/outcome")
def record_outcome(
    outcome: Outcome,
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    if outcome.success:
        row = failure_tracker.record_success(db, outcome.checkpoint, outcome.code)
    else:
        row = failure_tracker.record_failure(db, outcome.checkpoint, outcome.code, outcome.note)
    return {
        "code": row.code,
        "checkpoint": row.checkpoint,
        "consecutive_failures": row.consecutive_failures,
        "degraded": failure_tracker.is_degraded(row),
        "note": row.note,
    }
