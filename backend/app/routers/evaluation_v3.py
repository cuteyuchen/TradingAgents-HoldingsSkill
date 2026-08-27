"""Read-only Phase I evaluation and paper-observation API."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..evaluation.service import (
    evaluation_coverage,
    evaluation_summary,
    get_evaluation_episode,
    list_evaluation_episodes,
    paper_observation_status,
)
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, User

router = APIRouter(prefix="/api/v3", tags=["v3-evaluation"])


def _portfolio(db: Session, *, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.execute(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return row


@router.get("/portfolios/{portfolio_id}/evaluation/summary")
def evaluation_summary_endpoint(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return evaluation_summary(db, user_id=current_user.id, portfolio_id=portfolio_id, as_of=as_of)


@router.get("/portfolios/{portfolio_id}/evaluation/episodes")
def evaluation_episodes_endpoint(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return list_evaluation_episodes(
        db, user_id=current_user.id, portfolio_id=portfolio_id, as_of=as_of, limit=limit
    )


@router.get("/portfolios/{portfolio_id}/evaluation/episodes/{episode_id}")
def evaluation_episode_endpoint(
    portfolio_id: int,
    episode_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    payload = get_evaluation_episode(
        db, user_id=current_user.id, portfolio_id=portfolio_id, episode_id=episode_id
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Evaluation episode not found.")
    return payload


@router.get("/portfolios/{portfolio_id}/evaluation/coverage")
def evaluation_coverage_endpoint(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return evaluation_coverage(db, user_id=current_user.id, portfolio_id=portfolio_id)


@router.get("/portfolios/{portfolio_id}/evaluation/paper-observation")
def paper_observation_endpoint(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return paper_observation_status(db, user_id=current_user.id, portfolio_id=portfolio_id)


__all__ = ["router"]
