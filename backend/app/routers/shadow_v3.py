"""Phase N paper-only Live Decision Validation API.

There are deliberately no order-placement endpoints in this router.  The
only mutating operations create or control an isolated ShadowAccount.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..shadow.service import (
    create_shadow_account,
    match_actual_trade_alignment,
    rebuild_shadow_state,
    rebase_shadow_account,
    resume_shadow_account,
    shadow_account_performance,
    pause_shadow_account,
    validation_summary,
)
from ..shadow_models import (
    DecisionActualAlignment,
    LiveDecisionObservation,
    LiveDecisionOutcome,
    ShadowAccount,
    ShadowDailySnapshot,
    ShadowFill,
    ShadowOrderIntent,
    ShadowPosition,
)
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, User

router = APIRouter(prefix="/api/v3/shadow", tags=["v3-shadow"])


class ShadowAccountCreateRequest(BaseModel):
    portfolio_id: int = Field(ge=1)
    snapshot_id: int | None = Field(default=None, ge=1)
    name: str = Field(default="影子验证", min_length=1, max_length=128)

    model_config = ConfigDict(extra="forbid")


class ShadowRebaseRequest(BaseModel):
    snapshot_id: int | None = Field(default=None, ge=1)
    acknowledge: bool = True

    model_config = ConfigDict(extra="forbid")


def _portfolio(db: Session, *, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.execute(select(Portfolio).where(
        Portfolio.id == portfolio_id,
        Portfolio.user_id == user_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found.")
    return row


def _account(db: Session, *, user_id: int, account_id: int) -> ShadowAccount:
    row = db.execute(select(ShadowAccount).where(
        ShadowAccount.id == account_id,
        ShadowAccount.user_id == user_id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shadow account not found.")
    return row


def _datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _snapshot_payload(row: ShadowDailySnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.shadow_account_id,
        "shadow_generation": row.shadow_generation,
        "trade_date": row.trade_date.isoformat(),
        "cash": row.cash,
        "market_value": row.market_value,
        "total_equity": row.total_equity,
        "daily_return": row.daily_return,
        "cumulative_return": row.cumulative_return,
        "drawdown": row.drawdown,
        "turnover": row.turnover,
        "position_count": row.position_count,
        "action_count": row.action_count,
        "no_action_count": row.no_action_count,
        "benchmark_return": row.benchmark_return,
        "excess_return": row.excess_return,
        "market_regime": row.market_regime,
        "price_basis": row.price_basis,
        "price_basis_compatible": row.price_basis_compatible,
        "source_refs": row.source_refs_json or {},
        "created_at": _datetime(row.created_at),
    }


def _position_payload(row: ShadowPosition) -> dict[str, Any]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "security_type": row.security_type,
        "etf_category": row.etf_category,
        "quantity": row.quantity,
        "sellable_quantity": row.sellable_quantity,
        "average_cost": row.average_cost,
        "current_mark": row.current_mark,
        "market_value": row.market_value,
        "unrealized_pnl": row.unrealized_pnl,
        "last_mark_at": _datetime(row.last_mark_at),
        "acquired_decision_ids": row.acquired_decision_ids_json or [],
        "metadata": row.metadata_json or {},
    }


def _account_payload(db: Session, row: ShadowAccount, *, include_positions: bool = True) -> dict[str, Any]:
    state = rebuild_shadow_state(db, row)
    positions = db.execute(select(ShadowPosition).where(
        ShadowPosition.shadow_account_id == row.id,
        ShadowPosition.shadow_generation == row.shadow_generation,
        ShadowPosition.quantity > 0,
    ).order_by(ShadowPosition.code.asc())).scalars().all()
    pending = db.execute(select(ShadowOrderIntent).where(
        ShadowOrderIntent.shadow_account_id == row.id,
        ShadowOrderIntent.shadow_generation == row.shadow_generation,
        ShadowOrderIntent.status.in_(("PENDING", "PARTIAL")),
    )).scalars().all()
    return {
        "id": row.id,
        "user_id": row.user_id,
        "source_portfolio_id": row.source_portfolio_id,
        "name": row.name,
        "status": row.status,
        "mode": row.mode,
        "base_currency": row.base_currency,
        "paper_only": True,
        "initialized_from_snapshot_id": row.initialized_from_snapshot_id,
        "initialized_at": _datetime(row.initialized_at),
        "starting_cash": row.starting_cash,
        "current_cash": row.current_cash,
        "reserved_cash": row.reserved_cash,
        "shadow_generation": row.shadow_generation,
        "execution_contract_version": row.execution_contract_version,
        "expires_policy": row.expires_policy,
        "version": row.version,
        "created_at": _datetime(row.created_at),
        "paused_at": _datetime(row.paused_at),
        "closed_at": _datetime(row.closed_at),
        "pending_intent_count": len(pending),
        "shadow_state": {
            "cash": state["cash"],
            "ledger_entry_count": state["ledger_entry_count"],
            "as_of": _datetime(state["as_of"]),
        },
        "positions": [_position_payload(item) for item in positions] if include_positions else [],
    }


def _observation_payload(row: LiveDecisionObservation, *, detail: bool = False) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "user_id": row.user_id,
        "portfolio_id": row.portfolio_id,
        "trade_date": row.trade_date.isoformat(),
        "decision_kind": row.decision_kind,
        "decision_checkpoint": row.decision_checkpoint,
        "trigger_type": row.trigger_type,
        "trigger_event_id": row.trigger_event_id,
        "trigger_priority": row.trigger_priority,
        "trigger_reason": row.trigger_reason,
        "source_analysis_job_id": row.source_analysis_job_id,
        "source_analysis_run_id": row.source_analysis_run_id,
        "candidate_run_id": row.candidate_run_id,
        "portfolio_snapshot_id": row.portfolio_snapshot_id,
        "market_snapshot_id": row.market_snapshot_id,
        "market_score_snapshot_id": row.market_score_snapshot_id,
        "parameter_set_version": row.parameter_set_version,
        "parameter_set_hash": row.parameter_set_hash,
        "runtime_contract_version": row.runtime_contract_version,
        "decision_contract_version": row.decision_contract_version,
        "runtime_prompt_version": row.runtime_prompt_version,
        "runtime_prompt_sha256": row.runtime_prompt_sha256,
        "model_provider": row.model_provider,
        "model_name": row.model_name,
        "final_action": row.final_action,
        "raw_final_action": row.raw_final_action,
        "market_regime": row.market_regime,
        "market_score": row.market_score,
        "market_quality": row.market_quality,
        "portfolio_quality": row.portfolio_quality,
        "confidence": row.confidence,
        "data_coverage": row.data_coverage,
        "decision_started_at": _datetime(row.decision_started_at),
        "decision_finalized_at": _datetime(row.decision_finalized_at),
        "captured_at": _datetime(row.captured_at),
        "quality_status": row.quality_status,
        "live_evidence_eligibility": row.live_evidence_eligibility,
        "observation_hash": row.observation_hash,
        "calculation_key": row.calculation_key,
    }
    if detail:
        payload.update({
            "reason_codes": row.final_reason_codes_json or [],
            "selected_actions": row.selected_actions_json or [],
            "selected_candidate_ids": row.selected_candidate_ids_json or [],
            "source_lineage": row.source_lineage_json or {},
            "deterministic_core_hash": row.deterministic_core_hash,
            "market_metric_snapshot_id": row.market_metric_snapshot_id,
            "skill_version": row.skill_version,
            "skill_sha256": row.skill_sha256,
            "market_engine_version": row.market_engine_version,
            "candidate_engine_version": row.candidate_engine_version,
            "created_at": _datetime(row.created_at),
        })
    return payload


def _intent_payload(row: ShadowOrderIntent) -> dict[str, Any]:
    return {
        "id": row.id,
        "shadow_account_id": row.shadow_account_id,
        "shadow_generation": row.shadow_generation,
        "decision_observation_id": row.decision_observation_id,
        "action_index": row.action_index,
        "code": row.code,
        "security_type": row.security_type,
        "side": row.side,
        "target_qty": row.target_qty,
        "target_notional": row.target_notional,
        "target_weight": row.target_weight,
        "decision_reference_price": row.decision_reference_price,
        "decision_reference_basis": row.decision_reference_basis,
        "decision_finalized_at": _datetime(row.decision_finalized_at),
        "earliest_executable_at": _datetime(row.earliest_executable_at),
        "status": row.status,
        "reason_codes": row.reason_codes_json or [],
        "created_at": _datetime(row.created_at),
        "expires_at": _datetime(row.expires_at),
        "idempotency_key": row.idempotency_key,
    }


def _fill_payload(row: ShadowFill) -> dict[str, Any]:
    return {
        "id": row.id,
        "order_intent_id": row.order_intent_id,
        "shadow_account_id": row.shadow_account_id,
        "shadow_generation": row.shadow_generation,
        "code": row.code,
        "side": row.side,
        "quantity": row.quantity,
        "price": row.price,
        "gross_amount": row.gross_amount,
        "commission": row.commission,
        "tax": row.tax,
        "total_cost": row.total_cost,
        "price_basis": row.price_basis,
        "quote_observation_id": row.quote_observation_id,
        "quote_source_ref": row.quote_source_ref,
        "quote_captured_at": _datetime(row.quote_captured_at),
        "fill_at": _datetime(row.fill_at),
        "fill_quality": row.fill_quality,
        "execution_delay_seconds": row.execution_delay_seconds,
        "execution_delay_price_drift": row.execution_delay_price_drift,
        "slippage_not_modeled": True,
        "execution_key": row.execution_key,
        "created_at": _datetime(row.created_at),
    }


def _outcome_payload(row: LiveDecisionOutcome) -> dict[str, Any]:
    return {
        "id": row.id,
        "decision_observation_id": row.decision_observation_id,
        "shadow_account_id": row.shadow_account_id,
        "shadow_generation": row.shadow_generation,
        "target_type": row.target_type,
        "target_key": row.target_key,
        "recommended_action": row.recommended_action,
        "horizon_trading_days": row.horizon_trading_days,
        "reference_trade_date": row.reference_trade_date.isoformat() if row.reference_trade_date else None,
        "reference_at": _datetime(row.reference_at),
        "reference_price": row.reference_price,
        "reference_price_basis": row.reference_price_basis,
        "target_trade_date": row.target_trade_date.isoformat() if row.target_trade_date else None,
        "target_price": row.target_price,
        "forward_return": row.forward_return,
        "benchmark_return": row.benchmark_return,
        "excess_return": row.excess_return,
        "mfe": row.mfe,
        "mae": row.mae,
        "drawdown": row.drawdown,
        "direction": row.direction,
        "execution_eligible": row.execution_eligible,
        "shadow_filled": row.shadow_filled,
        "fill_delay_seconds": row.fill_delay_seconds,
        "fill_drift": row.fill_drift,
        "candidate_opportunity_cost": row.candidate_opportunity_cost,
        "drawdown_avoided": row.drawdown_avoided,
        "risk_off_correct": row.risk_off_correct,
        "status": row.status,
        "quality_status": row.quality_status,
        "live_evidence_eligibility": row.live_evidence_eligibility,
        "next_due_date": row.next_due_date.isoformat() if row.next_due_date else None,
        "computed_at": _datetime(row.computed_at),
    }


@router.get("/accounts")
def list_shadow_accounts(
    portfolio_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    query = select(ShadowAccount).where(ShadowAccount.user_id == current_user.id)
    if portfolio_id is not None:
        _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
        query = query.where(ShadowAccount.source_portfolio_id == portfolio_id)
    rows = db.execute(query.order_by(ShadowAccount.created_at.desc(), ShadowAccount.id.desc())).scalars().all()
    return [_account_payload(db, row) for row in rows]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    payload: ShadowAccountCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=payload.portfolio_id)
    try:
        row = create_shadow_account(
            db,
            user_id=current_user.id,
            portfolio_id=payload.portfolio_id,
            snapshot_id=payload.snapshot_id,
            name=payload.name,
        )
        db.commit()
        db.refresh(row)
        return _account_payload(db, row)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/accounts/{account_id}")
def get_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return _account_payload(db, _account(db, user_id=current_user.id, account_id=account_id))


@router.post("/accounts/{account_id}/pause")
def pause_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _account(db, user_id=current_user.id, account_id=account_id)
    try:
        pause_shadow_account(db, row)
        db.commit()
        return _account_payload(db, row)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/resume")
def resume_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _account(db, user_id=current_user.id, account_id=account_id)
    try:
        resume_shadow_account(db, row)
        db.commit()
        return _account_payload(db, row)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/rebase")
def rebase_account(
    account_id: int,
    payload: ShadowRebaseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if not payload.acknowledge:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rebase_acknowledgement_required")
    row = _account(db, user_id=current_user.id, account_id=account_id)
    try:
        rebase_shadow_account(db, row, snapshot_id=payload.snapshot_id)
        db.commit()
        return _account_payload(db, row)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/decisions")
def list_decisions(
    portfolio_id: int | None = Query(default=None, ge=1),
    account_id: int | None = Query(default=None, ge=1),
    final_action: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    if portfolio_id is not None:
        _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    if account_id is not None:
        account = _account(db, user_id=current_user.id, account_id=account_id)
        portfolio_id = account.source_portfolio_id
    query = select(LiveDecisionObservation).where(LiveDecisionObservation.user_id == current_user.id)
    if portfolio_id is not None:
        query = query.where(LiveDecisionObservation.portfolio_id == portfolio_id)
    if final_action:
        query = query.where(LiveDecisionObservation.final_action == final_action.upper())
    rows = db.execute(query.order_by(
        LiveDecisionObservation.decision_finalized_at.desc(),
        LiveDecisionObservation.id.desc(),
    ).limit(limit)).scalars().all()
    return [_observation_payload(row) for row in rows]


@router.get("/decisions/{observation_id}")
def decision_detail(
    observation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = db.execute(select(LiveDecisionObservation).where(
        LiveDecisionObservation.id == observation_id,
        LiveDecisionObservation.user_id == current_user.id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision observation not found.")
    intents = db.execute(select(ShadowOrderIntent).join(
        ShadowAccount, ShadowOrderIntent.shadow_account_id == ShadowAccount.id,
    ).where(
        ShadowOrderIntent.decision_observation_id == row.id,
        ShadowAccount.user_id == current_user.id,
    ).order_by(ShadowOrderIntent.action_index.asc(), ShadowOrderIntent.id.asc())).scalars().all()
    fills = db.execute(select(ShadowFill).join(
        ShadowAccount, ShadowFill.shadow_account_id == ShadowAccount.id,
    ).join(
        ShadowOrderIntent, ShadowFill.order_intent_id == ShadowOrderIntent.id,
    ).where(
        ShadowOrderIntent.decision_observation_id == row.id,
        ShadowAccount.user_id == current_user.id,
    ).order_by(ShadowFill.fill_at.asc(), ShadowFill.id.asc())).scalars().all()
    outcomes = db.execute(select(LiveDecisionOutcome).where(
        LiveDecisionOutcome.decision_observation_id == row.id,
    ).order_by(LiveDecisionOutcome.target_type.asc(), LiveDecisionOutcome.target_key.asc(), LiveDecisionOutcome.horizon_trading_days.asc())).scalars().all()
    alignments = db.execute(select(DecisionActualAlignment).where(
        DecisionActualAlignment.decision_observation_id == row.id,
        DecisionActualAlignment.user_id == current_user.id,
    ).order_by(DecisionActualAlignment.code.asc(), DecisionActualAlignment.side.asc())).scalars().all()
    return {
        **_observation_payload(row, detail=True),
        "execution": {
            "intents": [_intent_payload(item) for item in intents],
            "fills": [_fill_payload(item) for item in fills],
        },
        "outcomes": [_outcome_payload(item) for item in outcomes],
        "actual_alignment": [{
            "id": item.id,
            "code": item.code,
            "side": item.side,
            "status": item.status,
            "actual_trade_ledger_id": item.actual_trade_ledger_id,
            "matched_at": _datetime(item.matched_at),
            "time_delta_seconds": item.time_delta_seconds,
            "quantity_ratio": item.quantity_ratio,
            "window_start": _datetime(item.window_start),
            "window_end": _datetime(item.window_end),
            "source_refs": item.source_refs_json or {},
        } for item in alignments],
    }


@router.post("/decisions/{observation_id}/actual-alignment")
def align_decision_actual_trades(
    observation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = db.execute(select(LiveDecisionObservation).where(
        LiveDecisionObservation.id == observation_id,
        LiveDecisionObservation.user_id == current_user.id,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision observation not found.")
    try:
        matches = match_actual_trade_alignment(db, row)
        db.commit()
        return {"items": [{
            "id": item.id,
            "code": item.code,
            "side": item.side,
            "status": item.status,
            "actual_trade_ledger_id": item.actual_trade_ledger_id,
            "matched_at": _datetime(item.matched_at),
            "time_delta_seconds": item.time_delta_seconds,
            "quantity_ratio": item.quantity_ratio,
        } for item in matches]}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/orders")
def list_orders(
    account_id: int | None = Query(default=None, ge=1),
    portfolio_id: int | None = Query(default=None, ge=1),
    generation: int | None = Query(default=None, ge=1),
    order_status: str | None = Query(default=None, alias="status", max_length=16),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    if portfolio_id is not None:
        _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    query = select(ShadowOrderIntent).join(
        ShadowAccount, ShadowOrderIntent.shadow_account_id == ShadowAccount.id,
    ).where(ShadowAccount.user_id == current_user.id)
    if account_id is not None:
        _account(db, user_id=current_user.id, account_id=account_id)
        query = query.where(ShadowOrderIntent.shadow_account_id == account_id)
    if portfolio_id is not None:
        query = query.where(ShadowAccount.source_portfolio_id == portfolio_id)
    if generation is not None:
        query = query.where(ShadowOrderIntent.shadow_generation == generation)
    if order_status:
        query = query.where(ShadowOrderIntent.status == order_status.upper())
    rows = db.execute(query.order_by(ShadowOrderIntent.created_at.desc(), ShadowOrderIntent.id.desc()).limit(limit)).scalars().all()
    return [_intent_payload(row) for row in rows]


@router.get("/fills")
def list_fills(
    account_id: int | None = Query(default=None, ge=1),
    portfolio_id: int | None = Query(default=None, ge=1),
    generation: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    if portfolio_id is not None:
        _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    query = select(ShadowFill).join(
        ShadowAccount, ShadowFill.shadow_account_id == ShadowAccount.id,
    ).where(ShadowAccount.user_id == current_user.id)
    if account_id is not None:
        _account(db, user_id=current_user.id, account_id=account_id)
        query = query.where(ShadowFill.shadow_account_id == account_id)
    if portfolio_id is not None:
        query = query.where(ShadowAccount.source_portfolio_id == portfolio_id)
    if generation is not None:
        query = query.where(ShadowFill.shadow_generation == generation)
    rows = db.execute(query.order_by(ShadowFill.fill_at.desc(), ShadowFill.id.desc()).limit(limit)).scalars().all()
    return [_fill_payload(row) for row in rows]


@router.get("/performance")
def performance(
    account_id: int | None = Query(default=None, ge=1),
    generation: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if account_id is not None:
        accounts = [_account(db, user_id=current_user.id, account_id=account_id)]
    else:
        accounts = db.execute(select(ShadowAccount).where(
            ShadowAccount.user_id == current_user.id,
            ShadowAccount.status != "CLOSED",
        ).order_by(ShadowAccount.id.asc())).scalars().all()
    items = []
    for row in accounts:
        result = shadow_account_performance(db, row, generation=generation)
        result["snapshots"] = [_snapshot_payload(item) for item in result.get("snapshots") or []]
        items.append(result)
    return items[0] if account_id is not None and items else {"accounts": items}


@router.get("/validation")
def validation(
    portfolio_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if portfolio_id is not None:
        _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return validation_summary(db, user_id=current_user.id, portfolio_id=portfolio_id)


@router.get("/daily")
def daily_snapshots(
    account_id: int | None = Query(default=None, ge=1),
    portfolio_id: int | None = Query(default=None, ge=1),
    generation: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    if portfolio_id is not None:
        _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    query = select(ShadowDailySnapshot).join(
        ShadowAccount, ShadowDailySnapshot.shadow_account_id == ShadowAccount.id,
    ).where(ShadowAccount.user_id == current_user.id)
    if account_id is not None:
        _account(db, user_id=current_user.id, account_id=account_id)
        query = query.where(ShadowDailySnapshot.shadow_account_id == account_id)
    if portfolio_id is not None:
        query = query.where(ShadowAccount.source_portfolio_id == portfolio_id)
    if generation is not None:
        query = query.where(ShadowDailySnapshot.shadow_generation == generation)
    rows = db.execute(query.order_by(
        ShadowDailySnapshot.trade_date.desc(),
        ShadowDailySnapshot.id.desc(),
    ).limit(limit)).scalars().all()
    return [_snapshot_payload(row) for row in rows]


__all__ = ["router"]
