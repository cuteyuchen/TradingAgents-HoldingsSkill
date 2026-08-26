"""Application-facing Alpha Memory orchestration."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .decision import backfill_decision_memories, capture_decision_memory
from .execution import refresh_execution_alignments
from .models import DailyReviewRun, DecisionMemory, DecisionOutcome
from .outcomes import refresh_due_decision_outcomes
from .retrieval import memory_context_for_analysis, retrieve_historical_analogues
from .review import memory_stats, run_daily_review, serialize_daily_review


def _outcome_payload(row: DecisionOutcome) -> dict[str, Any]:
    return {
        "id": row.id,
        "decision_memory_id": row.decision_memory_id,
        "target_type": row.target_type,
        "target_key": row.target_key,
        "recommended_action": row.recommended_action,
        "recommended_qty": row.recommended_qty,
        "recommended_weight": row.recommended_weight,
        "target_weight": row.target_weight,
        "reference_trade_date": row.reference_trade_date,
        "reference_at": row.reference_at,
        "reference_price": row.reference_price,
        "reference_price_basis": row.reference_price_basis,
        "target_trade_date": row.target_trade_date,
        "end_price": row.end_price,
        "raw_return": row.raw_return,
        "benchmark_return": row.benchmark_return,
        "excess_return": row.excess_return,
        "mfe": row.mfe,
        "mae": row.mae,
        "directional_mfe": row.directional_mfe,
        "directional_mae": row.directional_mae,
        "directional_return": row.directional_return,
        "directional_excess_return": row.directional_excess_return,
        "actual_execution_price": row.actual_execution_price,
        "actual_executed_qty": row.actual_executed_qty,
        "actual_execution_return": row.actual_execution_return,
        "net_execution_return": row.net_execution_return,
        "execution_fees": row.execution_fees,
        "execution_taxes": row.execution_taxes,
        "execution_alignment": row.execution_alignment,
        "status": row.status,
        "quality_status": row.quality_status,
        "confidence": row.confidence,
        "source_refs": row.source_refs_json or {},
        "calculation_version": row.calculation_version,
        "computed_at": row.computed_at,
        "available_at": row.available_at,
        "recalculation_count": row.recalculation_count,
    }


def serialize_decision_memory(
    db: Session,
    row: DecisionMemory,
    *,
    include_outcomes: bool = True,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    outcomes_query = select(DecisionOutcome).where(DecisionOutcome.decision_memory_id == row.id)
    if as_of is not None:
        cutoff = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of
        outcomes_query = outcomes_query.where(
            DecisionOutcome.available_at.is_not(None),
            DecisionOutcome.available_at <= cutoff,
        )
    outcomes = db.execute(outcomes_query.order_by(DecisionOutcome.horizon_trading_days.asc(), DecisionOutcome.id.asc())).scalars().all()
    return {
        "id": row.id,
        "user_id": row.user_id,
        "portfolio_id": row.portfolio_id,
        "analysis_run_id": row.analysis_run_id,
        "analysis_job_id": row.analysis_job_id,
        "portfolio_snapshot_id": row.portfolio_snapshot_id,
        "portfolio_risk_snapshot_id": row.portfolio_risk_snapshot_id,
        "candidate_run_id": row.candidate_run_id,
        "trigger_event_id": row.trigger_event_id,
        "market_score_snapshot_id": row.market_score_snapshot_id,
        "market_metric_snapshot_id": row.market_metric_snapshot_id,
        "market_snapshot_id": row.market_snapshot_id,
        "trade_date": row.trade_date,
        "decision_at": row.decision_at,
        "available_at": row.available_at,
        "analysis_mode": row.analysis_mode,
        "decision_type": row.decision_type,
        "final_rating": row.final_rating,
        "portfolio_action": row.portfolio_action,
        "quality_status": row.quality_status,
        "confidence": row.confidence,
        "market_context": row.market_context_json or {},
        "portfolio_context": row.portfolio_context_json or {},
        "candidate_context": row.candidate_context_json or {},
        "holding_decisions": row.holding_decisions_json or [],
        "candidate_decisions": row.candidate_decisions_json or [],
        "no_action_context": row.no_action_context_json or {},
        "decision_features": row.decision_features_json or {},
        "source_refs": row.source_refs_json or {},
        "calculation_version": row.calculation_version,
        "created_at": row.created_at,
        "outcomes": [_outcome_payload(item) for item in outcomes] if include_outcomes else [],
    }


def list_decision_memories(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    limit: int = 50,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    query = select(DecisionMemory).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
    )
    if as_of is not None:
        cutoff = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of
        query = query.where(DecisionMemory.available_at <= cutoff)
    rows = db.execute(query.order_by(DecisionMemory.decision_at.desc(), DecisionMemory.id.desc()).limit(max(1, min(int(limit), 200)))).scalars().all()
    return [serialize_decision_memory(db, row, as_of=as_of) for row in rows]


def get_decision_memory(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    decision_id: int,
    as_of: datetime | None = None,
) -> dict[str, Any] | None:
    row = db.execute(select(DecisionMemory).where(
        DecisionMemory.id == decision_id,
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
    )).scalar_one_or_none()
    if row is None:
        return None
    if as_of is not None:
        cutoff = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of
        if row.available_at > cutoff:
            return None
    return serialize_decision_memory(db, row, as_of=as_of)


def list_decision_outcomes(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
    decision_id: int | None = None,
    status: str | None = None,
    as_of: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    query = select(DecisionOutcome, DecisionMemory).join(
        DecisionMemory, DecisionOutcome.decision_memory_id == DecisionMemory.id
    ).where(
        DecisionMemory.user_id == user_id,
        DecisionMemory.portfolio_id == portfolio_id,
    )
    if decision_id is not None:
        query = query.where(DecisionMemory.id == decision_id)
    if status:
        query = query.where(DecisionOutcome.status == status.upper())
    if as_of is not None:
        cutoff = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of
        query = query.where(
            DecisionOutcome.available_at.is_not(None),
            DecisionOutcome.available_at <= cutoff,
        )
    rows = db.execute(query.order_by(DecisionOutcome.created_at.desc(), DecisionOutcome.id.desc()).limit(max(1, min(int(limit), 1000)))).all()
    return [_outcome_payload(row) for row, _memory in rows]


def current_memory_features(
    *,
    portfolio_context: dict[str, Any] | None = None,
    candidate_context: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    portfolio = portfolio_context if isinstance(portfolio_context, dict) else {}
    candidate = candidate_context if isinstance(candidate_context, dict) else {}
    result = result if isinstance(result, dict) else {}
    action_rows = candidate.get("action") or candidate.get("candidates") or []
    best = max(
        (row for row in action_rows if isinstance(row, dict)),
        key=lambda row: float(row.get("decision_edge") or -1e9),
        default={},
    )
    return {
        "market_regime": portfolio.get("market_regime") or candidate.get("market_regime"),
        "market_score": portfolio.get("market_score") or portfolio.get("display_score"),
        "cash_ratio": portfolio.get("cash_ratio"),
        "gross_exposure": portfolio.get("gross_exposure"),
        "hhi": portfolio.get("hhi"),
        "portfolio_volatility": portfolio.get("portfolio_vol_60") or portfolio.get("portfolio_volatility"),
        "security_type": best.get("security_type"),
        "action_type": best.get("action") or best.get("candidate_type") or result.get("final_rating") or "no_action",
        "opportunity_score": best.get("opportunity_score"),
        "entry_score": best.get("entry_score"),
        "portfolio_fit": best.get("portfolio_fit_score") or best.get("portfolio_fit"),
        "decision_edge": best.get("decision_edge"),
    }


def serialize_outcome(row: DecisionOutcome) -> dict[str, Any]:
    return _outcome_payload(row)


__all__ = [
    "backfill_decision_memories",
    "capture_decision_memory",
    "current_memory_features",
    "get_decision_memory",
    "list_decision_memories",
    "list_decision_outcomes",
    "memory_context_for_analysis",
    "memory_stats",
    "refresh_due_decision_outcomes",
    "refresh_execution_alignments",
    "retrieve_historical_analogues",
    "run_daily_review",
    "serialize_decision_memory",
    "serialize_daily_review",
    "serialize_outcome",
]
