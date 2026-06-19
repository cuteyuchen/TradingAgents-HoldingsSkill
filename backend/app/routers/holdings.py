"""Per-ticker decision + return timeline (sliding window of last N runs)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import require_token
from ..database import get_db
from ..models import Claim, HoldingSnapshot, ResearchVerdict, TraderProposal
from ..services.pnl import normalize_pnl

router = APIRouter(prefix="/api/v1/holdings", tags=["holdings"])


@router.get("/{code}/timeline")
def holding_timeline(
    code: str,
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    """Last N same-code snapshots with price/raw_return/alpha for the AlphaChart.

    Returns oldest-first so the chart plots left→right. Mirrors the skill's
    `memory_same_ticker_entries` (default 5) sliding window.
    """
    snaps = (
        db.query(HoldingSnapshot)
        .filter(HoldingSnapshot.code == code)
        .order_by(HoldingSnapshot.id.desc())
        .limit(limit)
        .all()
    )
    snaps = list(reversed(snaps))
    points = []
    for s in snaps:
        pnl, pnl_amount, _correction = normalize_pnl(s.code, s.name, s.pnl, s.price, s.cost, s.pnl_amount)
        points.append({
            "run_id": s.run_id,
            "timestamp": s.run.timestamp.isoformat() if s.run else None,
            "checkpoint": s.run.checkpoint if s.run else None,
            "price": s.price,
            "cost": s.cost,
            "pnl": pnl,
            "pnl_amount": pnl_amount,
            "raw_return": s.raw_return,
            "benchmark_return": s.benchmark_return,
            "alpha": s.alpha,
            "data_quality": s.data_quality,
        })

    # Attach the latest decision summary (verdict + trader proposal).
    latest_run_id = snaps[-1].run_id if snaps else None
    verdict = None
    proposal = None
    claims: list[dict] = []
    if latest_run_id:
        v = db.query(ResearchVerdict).filter(ResearchVerdict.run_id == latest_run_id).first()
        if v:
            verdict = {"rating": v.rating, "winner": v.winner, "rationale": v.rationale,
                       "confidence": v.confidence}
        tp = db.query(TraderProposal).filter(
            TraderProposal.run_id == latest_run_id, TraderProposal.code == code
        ).first()
        if tp:
            proposal = {"action": tp.action, "trigger_price": tp.trigger_price,
                        "qty": tp.qty, "stop_loss": tp.stop_loss,
                        "invalidation": tp.invalidation}
        for c in db.query(Claim).filter(Claim.run_id == latest_run_id).all():
            claims.append({"claim_id": c.claim_id, "speaker": c.speaker, "claim": c.claim,
                           "confidence": c.confidence, "status": c.status, "round": c.round})

    return {"code": code, "points": points, "verdict": verdict, "proposal": proposal, "claims": claims}
