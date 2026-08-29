"""Phase G Alpha Memory, Outcome, Retrieval, and Daily Review API."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..memory.execution import link_ledger_entry_to_decision
from ..memory.service import (
    current_memory_features,
    get_decision_memory,
    list_decision_memories,
    list_decision_outcomes,
    memory_context_for_analysis,
    memory_stats,
    refresh_due_decision_outcomes,
    refresh_execution_alignments,
    retrieve_historical_analogues,
    run_daily_review,
    serialize_daily_review,
)
from ..memory.models import DailyReviewRun, DecisionMemory
from ..portfolio_models import TradeLedgerEntry
from ..services.trading_calendar import TradingCalendarService
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, User

router = APIRouter(prefix="/api/v3", tags=["v3-memory"])


class OutcomeRefreshRequest(BaseModel):
    as_of: datetime | None = None
    persist: bool = True
    force: bool = False

    model_config = ConfigDict(extra="forbid")


class DailyReviewRequest(BaseModel):
    trade_date: date | None = None
    as_of: datetime | None = None
    force: bool = False

    model_config = ConfigDict(extra="forbid")


class LedgerLinkRequest(BaseModel):
    ledger_entry_ids: list[int] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)

    model_config = ConfigDict(extra="forbid")


def _portfolio(db: Session, *, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.execute(select(Portfolio).where(
        Portfolio.id == portfolio_id,
        Portfolio.user_id == user_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return row


def _memory(db: Session, *, user_id: int, portfolio_id: int, decision_id: int) -> DecisionMemory:
    row = db.execute(select(DecisionMemory).where(
        DecisionMemory.id == decision_id,
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Decision Memory not found.")
    return row


@router.get("/portfolios/{portfolio_id}/memory/decisions")
def decisions(
    portfolio_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return list_decision_memories(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        limit=limit,
        as_of=as_of,
    )


@router.get("/portfolios/{portfolio_id}/memory/decisions/{decision_id}")
def decision_detail(
    portfolio_id: int,
    decision_id: int,
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    row = get_decision_memory(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        decision_id=decision_id,
        as_of=as_of,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Decision Memory not found.")
    return row


@router.post("/portfolios/{portfolio_id}/memory/decisions/{decision_id}/link-ledger")
def link_ledger(
    portfolio_id: int,
    decision_id: int,
    payload: LedgerLinkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    memory = _memory(db, user_id=current_user.id, portfolio_id=portfolio_id, decision_id=decision_id)
    entries = db.execute(select(TradeLedgerEntry).where(
        TradeLedgerEntry.id.in_(payload.ledger_entry_ids),
        TradeLedgerEntry.user_id == current_user.id,
        TradeLedgerEntry.portfolio_id == portfolio_id,
    ).order_by(TradeLedgerEntry.id.asc())).scalars().all()
    if len(entries) != len(set(payload.ledger_entry_ids)):
        raise HTTPException(status_code=422, detail="One or more ledger entries do not belong to this portfolio.")
    try:
        for entry in entries:
            link_ledger_entry_to_decision(
                db,
                memory=memory,
                ledger_entry=entry,
                user_id=current_user.id,
                reason=payload.reason,
            )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "linked",
        "decision_id": memory.id,
        "ledger_entry_ids": [entry.id for entry in entries],
    }


@router.get("/portfolios/{portfolio_id}/memory/outcomes")
def outcomes(
    portfolio_id: int,
    decision_id: int | None = Query(default=None),
    outcome_status: str | None = Query(default=None, alias="status"),
    as_of: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return list_decision_outcomes(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        decision_id=decision_id,
        status=outcome_status,
        as_of=as_of,
        limit=limit,
    )


@router.post("/portfolios/{portfolio_id}/memory/outcomes/refresh")
def refresh_outcomes(
    portfolio_id: int,
    payload: OutcomeRefreshRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    result = refresh_due_decision_outcomes(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        calculation_as_of=payload.as_of,
        persist=payload.persist,
        force=payload.force,
    )
    if payload.persist:
        refresh_execution_alignments(
            db,
            user_id=current_user.id,
            portfolio_id=portfolio_id,
            calculation_as_of=payload.as_of,
            persist=True,
        )
    return result


@router.get("/portfolios/{portfolio_id}/memory/analogues")
def analogues(
    portfolio_id: int,
    as_of: datetime | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=10),
    features_json: str | None = Query(default=None),
    market_regime: str | None = Query(default=None),
    market_score: float | None = Query(default=None),
    cash_ratio: float | None = Query(default=None),
    gross_exposure: float | None = Query(default=None),
    hhi: float | None = Query(default=None),
    portfolio_volatility: float | None = Query(default=None),
    security_type: str | None = Query(default=None),
    action_type: str | None = Query(default=None),
    opportunity_score: float | None = Query(default=None),
    entry_score: float | None = Query(default=None),
    portfolio_fit: float | None = Query(default=None),
    decision_edge: float | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    features: dict[str, Any] = {}
    if features_json:
        try:
            parsed = json.loads(features_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="features_json is invalid") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=422, detail="features_json must be an object")
        features.update(parsed)
    features.update({
        key: value for key, value in {
            "market_regime": market_regime,
            "market_score": market_score,
            "cash_ratio": cash_ratio,
            "gross_exposure": gross_exposure,
            "hhi": hhi,
            "portfolio_volatility": portfolio_volatility,
            "security_type": security_type,
            "action_type": action_type,
            "opportunity_score": opportunity_score,
            "entry_score": entry_score,
            "portfolio_fit": portfolio_fit,
            "decision_edge": decision_edge,
        }.items() if value is not None
    })
    return retrieve_historical_analogues(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        current_features=features,
        as_of=as_of,
        limit=limit,
    )


@router.get("/portfolios/{portfolio_id}/memory/stats")
def stats(
    portfolio_id: int,
    horizon: int | None = Query(default=None, ge=1, le=120),
    action_type: str | None = Query(default=None),
    security_type: str | None = Query(default=None),
    market_regime: str | None = Query(default=None),
    as_of: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return memory_stats(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        horizon=horizon,
        action_type=action_type,
        security_type=security_type,
        market_regime=market_regime,
        as_of=as_of,
    )


@router.post("/portfolios/{portfolio_id}/daily-reviews/run")
def run_review(
    portfolio_id: int,
    payload: DailyReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    trade_date = payload.trade_date or TradingCalendarService(db).latest_trading_day()
    if trade_date is None:
        raise HTTPException(status_code=409, detail="trading_calendar_not_ready")
    row = run_daily_review(
        db,
        user_id=current_user.id,
        portfolio_id=portfolio_id,
        trade_date=trade_date,
        as_of=payload.as_of,
        force=payload.force,
    )
    if row is None:
        raise HTTPException(status_code=409, detail="daily_review_requires_trading_day")
    return serialize_daily_review(row)


@router.get("/portfolios/{portfolio_id}/daily-reviews/latest")
def latest_review(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    row = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == current_user.id,
        DailyReviewRun.portfolio_id == portfolio_id,
    ).order_by(DailyReviewRun.trade_date.desc(), DailyReviewRun.id.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Daily Review not found.")
    return serialize_daily_review(row)


@router.get("/portfolios/{portfolio_id}/daily-reviews/history")
def review_history(
    portfolio_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    rows = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.user_id == current_user.id,
        DailyReviewRun.portfolio_id == portfolio_id,
    ).order_by(DailyReviewRun.trade_date.desc(), DailyReviewRun.id.desc()).limit(limit)).scalars().all()
    return [serialize_daily_review(row) for row in rows]


@router.get("/portfolios/{portfolio_id}/daily-reviews/{review_id}")
def review_detail(
    portfolio_id: int,
    review_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    row = db.execute(select(DailyReviewRun).where(
        DailyReviewRun.id == review_id,
        DailyReviewRun.user_id == current_user.id,
        DailyReviewRun.portfolio_id == portfolio_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Daily Review not found.")
    return serialize_daily_review(row)


__all__ = ["router"]
