"""Read-only Phase I evaluation and paper-observation API."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
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
from ..evaluation.forward import (
    campaign_coverage,
    campaign_integrity,
    create_daily_evidence_seal,
    create_observation_campaign,
    forward_summary,
    get_observation_campaign,
    list_observation_campaigns,
    mature_campaign_outcomes,
    transition_campaign,
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


@router.get("/portfolios/{portfolio_id}/evaluation/campaigns")
def observation_campaigns_endpoint(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return list_observation_campaigns(db, user_id=current_user.id, portfolio_id=portfolio_id)


@router.post("/portfolios/{portfolio_id}/evaluation/campaigns")
def create_observation_campaign_endpoint(
    portfolio_id: int,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        start = date.fromisoformat(payload["start_date"]) if payload.get("start_date") else None
        end = date.fromisoformat(payload["end_date"]) if payload.get("end_date") else None
        return create_observation_campaign(
            db,
            user_id=current_user.id,
            portfolio_id=portfolio_id,
            start_date=start,
            end_date=end,
            config_hash=payload.get("config_hash"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}")
def observation_campaign_endpoint(
    portfolio_id: int,
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    payload = get_observation_campaign(db, campaign_id=campaign_id, user_id=current_user.id, portfolio_id=portfolio_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Observation campaign not found.")
    return payload


def _transition_campaign_endpoint(action: str, portfolio_id: int, campaign_id: str, db: Session, current_user: User) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return transition_campaign(db, campaign_id=campaign_id, user_id=current_user.id, portfolio_id=portfolio_id, action=action)
    except ValueError as exc:
        code = 404 if str(exc) == "campaign_not_found" else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/start")
def start_observation_campaign(portfolio_id: int, campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    return _transition_campaign_endpoint("start", portfolio_id, campaign_id, db, current_user)


@router.post("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/pause")
def pause_observation_campaign(portfolio_id: int, campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    return _transition_campaign_endpoint("pause", portfolio_id, campaign_id, db, current_user)


@router.post("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/resume")
def resume_observation_campaign(portfolio_id: int, campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    return _transition_campaign_endpoint("resume", portfolio_id, campaign_id, db, current_user)


@router.post("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/complete")
def complete_observation_campaign(portfolio_id: int, campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    return _transition_campaign_endpoint("complete", portfolio_id, campaign_id, db, current_user)


@router.get("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/coverage")
def observation_campaign_coverage(portfolio_id: int, campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return campaign_coverage(db, campaign_id=campaign_id, user_id=current_user.id, portfolio_id=portfolio_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/integrity")
def observation_campaign_integrity(portfolio_id: int, campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return campaign_integrity(db, campaign_id=campaign_id, user_id=current_user.id, portfolio_id=portfolio_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/forward-summary")
def observation_campaign_forward_summary(portfolio_id: int, campaign_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return forward_summary(db, campaign_id=campaign_id, user_id=current_user.id, portfolio_id=portfolio_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/seals/{trading_date}")
def seal_observation_day(portfolio_id: int, campaign_id: str, trading_date: date, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return create_daily_evidence_seal(db, campaign_id=campaign_id, user_id=current_user.id, portfolio_id=portfolio_id, trading_date=trading_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/evaluation/campaigns/{campaign_id}/mature-outcomes")
def mature_observation_outcomes(portfolio_id: int, campaign_id: str, as_of: datetime | None = Query(default=None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return mature_campaign_outcomes(db, campaign_id=campaign_id, user_id=current_user.id, portfolio_id=portfolio_id, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
