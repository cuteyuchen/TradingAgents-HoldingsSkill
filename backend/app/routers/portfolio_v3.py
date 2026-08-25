"""Phase E Portfolio Operating System API, scoped to the authenticated user."""
from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..portfolio.ledger import confirm_ledger_entry, create_ledger_entry, revise_ledger_entry, void_ledger_entry
from ..portfolio.service import calculate_portfolio_risk
from ..portfolio.snapshot_diff import refresh_affected_snapshot_reconciliations
from ..portfolio_models import PortfolioRiskSnapshot, PortfolioSnapshotDiff, TradeLedgerEntry, TradeLedgerRevision
from ..portfolio_schemas import (
    PortfolioRiskCalculateRequest,
    TradeLedgerCreate,
    TradeLedgerConfirm,
    TradeLedgerEntryResponse,
    TradeLedgerRevisionCreate,
    TradeLedgerRevisionResponse,
    TradeLedgerVoid,
)
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, PortfolioSnapshot, User
from ..v2_models import AnalysisJob, AnalysisRun
from ..trigger_models import TriggerEvent

router = APIRouter(prefix="/api/v3", tags=["v3-portfolio"])


def _portfolio(db: Session, *, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.execute(select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return row


def _entry(db: Session, *, user_id: int, portfolio_id: int, entry_id: int) -> TradeLedgerEntry:
    row = db.execute(select(TradeLedgerEntry).where(
        TradeLedgerEntry.id == entry_id,
        TradeLedgerEntry.portfolio_id == portfolio_id,
        TradeLedgerEntry.user_id == user_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger entry not found.")
    return row


def _risk_response(calculated: dict) -> dict:
    return calculated


@router.get("/portfolios/{portfolio_id}/state")
def get_portfolio_state(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return calculate_portfolio_risk(db, portfolio_id=portfolio_id, user_id=current_user.id, persist=False)["state"]
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/risk")
def get_portfolio_risk(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        return _risk_response(calculate_portfolio_risk(db, portfolio_id=portfolio_id, user_id=current_user.id, persist=False))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/risk/calculate")
def calculate_risk(
    portfolio_id: int,
    payload: PortfolioRiskCalculateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    try:
        calculated = calculate_portfolio_risk(
            db,
            portfolio_id=portfolio_id,
            user_id=current_user.id,
            as_of=payload.as_of,
            persist=payload.persist,
        )
        if payload.persist:
            db.commit()
        return _risk_response(calculated)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/risk/history")
def risk_history(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    rows = db.execute(select(PortfolioRiskSnapshot).where(
        PortfolioRiskSnapshot.portfolio_id == portfolio_id,
        PortfolioRiskSnapshot.user_id == current_user.id,
    ).order_by(PortfolioRiskSnapshot.as_of.desc(), PortfolioRiskSnapshot.id.desc())).scalars().all()
    return [{
        "id": row.id, "portfolio_snapshot_id": row.portfolio_snapshot_id, "market_score_snapshot_id": row.market_score_snapshot_id,
        "as_of": row.as_of, "cash_ratio": row.cash_ratio, "gross_exposure": row.gross_exposure,
        "top1_weight": row.top1_weight, "top3_weight": row.top3_weight, "top5_weight": row.top5_weight,
        "hhi": row.hhi, "portfolio_vol_20": row.portfolio_vol_20, "portfolio_vol_60": row.portfolio_vol_60,
        "weighted_average_correlation": row.weighted_average_correlation, "max_pairwise_correlation": row.max_pairwise_correlation,
        "risk_flags": row.risk_flags_json or [], "confidence": row.confidence, "quality_status": row.quality_status,
        "calculation_version": row.calculation_version,
    } for row in rows]


@router.get("/portfolios/{portfolio_id}/snapshot-diffs")
def snapshot_diffs(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    rows = db.execute(select(PortfolioSnapshotDiff).where(
        PortfolioSnapshotDiff.portfolio_id == portfolio_id,
        PortfolioSnapshotDiff.user_id == current_user.id,
    ).order_by(PortfolioSnapshotDiff.created_at.desc(), PortfolioSnapshotDiff.id.desc())).scalars().all()
    return [{
        "id": row.id, "before_snapshot_id": row.before_snapshot_id, "after_snapshot_id": row.after_snapshot_id,
        "diff": row.diff_json, "reconciliation_status": row.reconciliation_status,
        "calculation_version": row.calculation_version, "created_at": row.created_at,
    } for row in rows]


@router.get("/portfolios/{portfolio_id}/ledger", response_model=list[TradeLedgerEntryResponse])
def list_ledger(
    portfolio_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TradeLedgerEntry]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return db.execute(select(TradeLedgerEntry).where(
        TradeLedgerEntry.portfolio_id == portfolio_id,
        TradeLedgerEntry.user_id == current_user.id,
    ).order_by(TradeLedgerEntry.executed_at.desc(), TradeLedgerEntry.id.desc())).scalars().all()


@router.post("/portfolios/{portfolio_id}/ledger", response_model=TradeLedgerEntryResponse, status_code=status.HTTP_201_CREATED)
def create_ledger(
    portfolio_id: int,
    payload: TradeLedgerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TradeLedgerEntry:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    if payload.analysis_run_id is not None:
        run = db.execute(select(AnalysisRun).join(AnalysisJob, AnalysisRun.job_id == AnalysisJob.id).where(
            AnalysisRun.id == payload.analysis_run_id,
            AnalysisRun.user_id == current_user.id,
            AnalysisJob.portfolio_id == portfolio_id,
        )).scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=422, detail="Analysis run link is invalid.")
    if payload.trigger_event_id is not None:
        event = db.execute(select(TriggerEvent).where(
            TriggerEvent.id == payload.trigger_event_id,
            TriggerEvent.user_id == current_user.id,
            TriggerEvent.portfolio_id == portfolio_id,
        )).scalar_one_or_none()
        if event is None:
            raise HTTPException(status_code=422, detail="Trigger event link is invalid.")
    try:
        row, _created = create_ledger_entry(
            db, user_id=current_user.id, portfolio_id=portfolio_id, payload=payload.model_dump()
        )
        refresh_affected_snapshot_reconciliations(
            db, portfolio_id=portfolio_id, executed_at_values=[row.executed_at]
        )
        db.commit()
        db.refresh(row)
        return row
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/ledger/{entry_id}/revise", response_model=TradeLedgerEntryResponse)
def revise_ledger(
    portfolio_id: int,
    entry_id: int,
    payload: TradeLedgerRevisionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TradeLedgerEntry:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    row = _entry(db, user_id=current_user.id, portfolio_id=portfolio_id, entry_id=entry_id)
    try:
        previous_executed_at = row.executed_at
        revise_ledger_entry(db, entry=row, user_id=current_user.id, changes=payload.changes, reason=payload.reason)
        refresh_affected_snapshot_reconciliations(
            db, portfolio_id=portfolio_id, executed_at_values=[previous_executed_at, row.executed_at]
        )
        db.commit()
        db.refresh(row)
        return row
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/ledger/{entry_id}/void", response_model=TradeLedgerEntryResponse)
def void_ledger(
    portfolio_id: int,
    entry_id: int,
    payload: TradeLedgerVoid,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TradeLedgerEntry:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    row = _entry(db, user_id=current_user.id, portfolio_id=portfolio_id, entry_id=entry_id)
    try:
        void_ledger_entry(db, entry=row, user_id=current_user.id, reason=payload.reason)
        refresh_affected_snapshot_reconciliations(
            db, portfolio_id=portfolio_id, executed_at_values=[row.executed_at]
        )
        db.commit()
        db.refresh(row)
        return row
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/portfolios/{portfolio_id}/ledger/{entry_id}/confirm", response_model=TradeLedgerEntryResponse)
def confirm_ledger(
    portfolio_id: int,
    entry_id: int,
    payload: TradeLedgerConfirm,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TradeLedgerEntry:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    row = _entry(db, user_id=current_user.id, portfolio_id=portfolio_id, entry_id=entry_id)
    try:
        confirm_ledger_entry(db, entry=row, user_id=current_user.id, reason=payload.reason)
        refresh_affected_snapshot_reconciliations(
            db, portfolio_id=portfolio_id, executed_at_values=[row.executed_at]
        )
        db.commit()
        db.refresh(row)
        return row
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/ledger/{entry_id}/revisions", response_model=list[TradeLedgerRevisionResponse])
def ledger_revisions(
    portfolio_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TradeLedgerRevision]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    _entry(db, user_id=current_user.id, portfolio_id=portfolio_id, entry_id=entry_id)
    return db.execute(select(TradeLedgerRevision).where(
        TradeLedgerRevision.ledger_entry_id == entry_id
    ).order_by(TradeLedgerRevision.revision_no.asc())).scalars().all()
