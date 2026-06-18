"""Self-selected watchlist (optimization #8)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_token
from ..database import get_db
from ..models import Watchlist
from ..schemas import WatchlistItem

router = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItem])
def list_watchlist(db: Session = Depends(get_db)):
    return db.query(Watchlist).order_by(Watchlist.created_at).all()


@router.post("", response_model=WatchlistItem, status_code=201)
def add_watchlist(
    item: WatchlistItem,
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    existing = db.query(Watchlist).filter(Watchlist.code == item.code).first()
    if existing:
        existing.name = item.name
        existing.cadence = item.cadence
        existing.enabled = item.enabled
        db.commit()
        db.refresh(existing)
        return existing
    row = Watchlist(code=item.code, name=item.name, cadence=item.cadence, enabled=item.enabled)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{code}", status_code=204)
def remove_watchlist(
    code: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_token),
):
    row = db.query(Watchlist).filter(Watchlist.code == code).first()
    if not row:
        raise HTTPException(status_code=404, detail="not in watchlist")
    db.delete(row)
    db.commit()
