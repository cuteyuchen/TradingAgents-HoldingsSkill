from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, User
from ..v3_trigger_schemas import MonitorRunOnceRequest
from ..services.realtime_monitor import get_realtime_monitor

router = APIRouter(prefix="/api/v3/monitor", tags=["v3-monitor"])


@router.get("/status")
def monitor_status(_: User = Depends(get_current_user)):
    return get_realtime_monitor().status()


@router.post("/run-once")
def monitor_run_once(payload: MonitorRunOnceRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # The monitor derives every quote/score from server-side providers.
    if payload.portfolio_id is not None and db.query(Portfolio).filter(
        Portfolio.id == payload.portfolio_id, Portfolio.user_id == current_user.id
    ).first() is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return get_realtime_monitor().tick(portfolio_id=payload.portfolio_id, user_id=current_user.id, dry_run=payload.dry_run)
