"""Current portfolio snapshot (latest run's holdings)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import require_token
from ..database import get_db
from ..models import Run
from ..routers.runs import _serialize_holding

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])


@router.get("/current")
def current_portfolio(
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    latest = db.query(Run).order_by(Run.timestamp.desc()).first()
    if not latest:
        return {"run_id": None, "timestamp": None, "holdings": []}
    return {
        "run_id": latest.id,
        "timestamp": latest.timestamp.isoformat(),
        "holdings": [_serialize_holding(h) for h in latest.holdings],
    }
