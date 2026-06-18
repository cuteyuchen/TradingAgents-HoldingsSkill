"""CSI 300 benchmark price query + manual refresh."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import require_token
from ..database import get_db
from ..models import BenchmarkPrice
from ..services import benchmark_fetcher

router = APIRouter(prefix="/api/v1/benchmark", tags=["benchmark"])


@router.get("/hs300")
def benchmark_prices(
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    limit: int = Query(250, ge=1, le=2000),
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    q = db.query(BenchmarkPrice)
    if frm:
        q = q.filter(BenchmarkPrice.date >= frm)
    if to:
        q = q.filter(BenchmarkPrice.date <= to)
    rows = q.order_by(BenchmarkPrice.date.desc()).limit(limit).all()
    return [{"date": r.date, "close": r.close, "pct_change": r.pct_change} for r in rows]


@router.post("/refresh")
def refresh(
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    written = benchmark_fetcher.refresh_today(db)
    return {"written": written}
