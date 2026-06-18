"""Buy/rotation candidate tracking across runs."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Candidate

router = APIRouter(prefix="/api/v1/candidates", tags=["candidates"])


@router.get("")
def list_candidates(
    status: str | None = Query(None),  # 待触发/已命中/已取消
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(Candidate)
    if status:
        q = q.filter(Candidate.status == status)
    rows = q.order_by(Candidate.id.desc()).limit(limit).all()
    return [
        {"id": c.id, "run_id": c.run_id, "code": c.code, "name": c.name, "type": c.type,
         "score": c.score, "score_breakdown": c.score_breakdown,
         "entry_trigger": c.entry_trigger, "initial_size": c.initial_size,
         "take_profit_1": c.take_profit_1, "take_profit_2": c.take_profit_2,
         "stop_loss": c.stop_loss, "invalidation": c.invalidation, "status": c.status}
        for c in rows
    ]
