"""Phase F deterministic Candidate Engine API."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..candidates.schemas import CandidateScanRequest
from ..candidates.service import get_candidate_run, latest_candidate_context, list_candidate_runs, scan_candidates
from ..database import get_db
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3", tags=["v3-candidates"])


def _portfolio(db: Session, *, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.execute(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found.")
    return row


def _service_error(exc: ValueError) -> HTTPException:
    code = str(exc)
    if code == "confirmed_snapshot_not_found":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=code)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=code)


@router.post("/portfolios/{portfolio_id}/candidates/scan")
def scan(
    portfolio_id: int,
    payload: CandidateScanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        result = scan_candidates(
            db,
            user_id=current_user.id,
            portfolio_id=portfolio_id,
            as_of=payload.as_of,
            mode=payload.mode,
            persist=True,
        )
        try:
            from ..operations.notifications import dispatch_material_events

            dispatch_material_events(
                db,
                user_id=current_user.id,
                portfolio_id=portfolio_id,
                as_of=payload.as_of,
            )
        except Exception:
            # Candidate facts are already committed; notification delivery must
            # not turn a successful deterministic scan into an API failure.
            logger.exception("Operating notification dispatch failed after candidate scan")
        return result
    except ValueError as exc:
        db.rollback()
        raise _service_error(exc) from exc


@router.get("/portfolios/{portfolio_id}/candidates/latest")
def latest(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return latest_candidate_context(
            db,
            user_id=current_user.id,
            portfolio_id=portfolio_id,
            as_of=as_of,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc


@router.get("/portfolios/{portfolio_id}/candidates/history")
def history(
    portfolio_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return {
        "portfolio_id": portfolio_id,
        "runs": list_candidate_runs(db, user_id=current_user.id, portfolio_id=portfolio_id, limit=limit),
    }


@router.get("/portfolios/{portfolio_id}/candidates/runs/{run_id}")
def run_detail(
    portfolio_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    payload = get_candidate_run(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        run_id=run_id,
    )
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate run not found.")
    return payload


__all__ = ["router"]
