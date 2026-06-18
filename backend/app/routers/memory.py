"""Phase-0 memory context for the skill."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


@router.get("/context")
def memory_context(
    code: str = Query(..., min_length=1),
    same_limit: int = Query(5, ge=1, le=50),
    cross_limit: int = Query(3, ge=0, le=20),
    db: Session = Depends(get_db),
):
    same = (
        db.query(models.HoldingSnapshot)
        .join(models.Run, models.HoldingSnapshot.run_id == models.Run.id)
        .filter(models.HoldingSnapshot.code == code)
        .order_by(models.Run.timestamp.desc(), models.HoldingSnapshot.id.desc())
        .limit(same_limit)
        .all()
    )

    same_ticker = [_same_ticker_point(s) for s in reversed(same)]

    cross_rows = (
        db.query(models.HoldingSnapshot, models.Run, models.PortfolioManagerFinal)
        .join(models.Run, models.HoldingSnapshot.run_id == models.Run.id)
        .join(models.PortfolioManagerFinal, models.PortfolioManagerFinal.run_id == models.Run.id)
        .filter(models.HoldingSnapshot.code != code)
        .filter(models.PortfolioManagerFinal.priority_notes.isnot(None))
        .filter(models.PortfolioManagerFinal.priority_notes != "")
        .order_by(models.Run.timestamp.desc(), models.HoldingSnapshot.id.desc())
        .limit(cross_limit)
        .all()
    )

    cross_ticker_lessons = [
        {
            "run_id": run.id,
            "timestamp": run.timestamp.isoformat(),
            "checkpoint": run.checkpoint,
            "code": snap.code,
            "name": snap.name,
            "raw_return": snap.raw_return,
            "benchmark_return": snap.benchmark_return,
            "alpha": snap.alpha,
            "pm_rating": pm.rating,
            "lesson": pm.priority_notes,
        }
        for snap, run, pm in cross_rows
    ]

    return {
        "code": code,
        "same_ticker": same_ticker,
        "cross_ticker_lessons": cross_ticker_lessons,
    }


def _same_ticker_point(snapshot: models.HoldingSnapshot) -> dict:
    run = snapshot.run
    pm = run.pm_final if run else None
    proposal = next((p for p in (run.trader_proposals if run else []) if p.code == snapshot.code), None)
    return {
        "run_id": snapshot.run_id,
        "timestamp": run.timestamp.isoformat() if run else None,
        "checkpoint": run.checkpoint if run else None,
        "price": snapshot.price,
        "raw_return": snapshot.raw_return,
        "benchmark_return": snapshot.benchmark_return,
        "alpha": snapshot.alpha,
        "data_quality": snapshot.data_quality,
        "pm_rating": pm.rating if pm else None,
        "pm_priority_notes": pm.priority_notes if pm else None,
        "action": proposal.action if proposal else None,
        "trigger_price": proposal.trigger_price if proposal else None,
    }
