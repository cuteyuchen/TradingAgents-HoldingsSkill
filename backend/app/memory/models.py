"""Persistent immutable decisions and derived Phase G facts."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .config import DAILY_REVIEW_VERSION, DECISION_MEMORY_VERSION, OUTCOME_VERSION


def utcnow() -> datetime:
    return datetime.now(UTC)


class DecisionMemory(Base):
    """Immutable snapshot of one successful normalized AnalysisRun."""

    __tablename__ = "decision_memories"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", name="uq_decision_memories_analysis_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    analysis_job_id: Mapped[int] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), index=True)
    portfolio_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="SET NULL"), index=True)
    portfolio_risk_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_risk_snapshots.id", ondelete="SET NULL"), index=True)
    candidate_run_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_runs.id", ondelete="SET NULL"), index=True)
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("trigger_events.id", ondelete="SET NULL"), index=True)
    market_score_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    market_metric_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    market_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    decision_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    analysis_mode: Mapped[str] = mapped_column(String(16), index=True)
    decision_type: Mapped[str] = mapped_column(String(32), index=True)
    final_rating: Mapped[str | None] = mapped_column(String(32), index=True)
    portfolio_action: Mapped[str | None] = mapped_column(String(32), index=True)
    quality_status: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    market_context_json: Mapped[dict | None] = mapped_column(JSON)
    portfolio_context_json: Mapped[dict | None] = mapped_column(JSON)
    candidate_context_json: Mapped[dict | None] = mapped_column(JSON)
    holding_decisions_json: Mapped[list | None] = mapped_column(JSON)
    candidate_decisions_json: Mapped[list | None] = mapped_column(JSON)
    no_action_context_json: Mapped[dict | None] = mapped_column(JSON)
    decision_features_json: Mapped[dict | None] = mapped_column(JSON)
    source_refs_json: Mapped[dict | None] = mapped_column(JSON)
    calculation_version: Mapped[str] = mapped_column(String(64), default=DECISION_MEMORY_VERSION)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DecisionOutcome(Base):
    """Materialized deterministic result for one DecisionTarget and horizon."""

    __tablename__ = "decision_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "decision_memory_id",
            "target_type",
            "target_key",
            "horizon_trading_days",
            "calculation_version",
            name="uq_decision_outcomes_memory_target_horizon_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_memory_id: Mapped[int] = mapped_column(ForeignKey("decision_memories.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_key: Mapped[str] = mapped_column(String(64), index=True)
    recommended_action: Mapped[str] = mapped_column(String(32), index=True)
    horizon_trading_days: Mapped[int] = mapped_column(Integer, index=True)
    recommended_qty: Mapped[float | None] = mapped_column(Float)
    recommended_weight: Mapped[float | None] = mapped_column(Float)
    target_weight: Mapped[float | None] = mapped_column(Float)
    reference_trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    reference_at: Mapped[datetime | None] = mapped_column(DateTime)
    reference_price: Mapped[float | None] = mapped_column(Float)
    reference_price_basis: Mapped[str | None] = mapped_column(String(64))
    target_trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    end_price: Mapped[float | None] = mapped_column(Float)
    raw_return: Mapped[float | None] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    excess_return: Mapped[float | None] = mapped_column(Float)
    mfe: Mapped[float | None] = mapped_column(Float)
    mae: Mapped[float | None] = mapped_column(Float)
    directional_mfe: Mapped[float | None] = mapped_column(Float)
    directional_mae: Mapped[float | None] = mapped_column(Float)
    directional_return: Mapped[float | None] = mapped_column(Float)
    directional_excess_return: Mapped[float | None] = mapped_column(Float)
    actual_execution_price: Mapped[float | None] = mapped_column(Float)
    actual_executed_qty: Mapped[float | None] = mapped_column(Float)
    actual_execution_return: Mapped[float | None] = mapped_column(Float)
    net_execution_return: Mapped[float | None] = mapped_column(Float)
    execution_fees: Mapped[float | None] = mapped_column(Float)
    execution_taxes: Mapped[float | None] = mapped_column(Float)
    execution_alignment: Mapped[str | None] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    quality_status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source_refs_json: Mapped[dict | None] = mapped_column(JSON)
    calculation_version: Mapped[str] = mapped_column(String(64), default=OUTCOME_VERSION)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    recalculation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_source_change_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DailyReviewRun(Base):
    """Idempotent deterministic daily maintenance and review result."""

    __tablename__ = "daily_review_runs"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "trade_date", "review_version", name="uq_daily_review_portfolio_day_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    decision_count: Mapped[int] = mapped_column(Integer, default=0)
    no_action_count: Mapped[int] = mapped_column(Integer, default=0)
    action_decision_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_action_count: Mapped[int] = mapped_column(Integer, default=0)
    actual_execution_count: Mapped[int] = mapped_column(Integer, default=0)
    execution_followed_count: Mapped[int] = mapped_column(Integer, default=0)
    execution_partial_count: Mapped[int] = mapped_column(Integer, default=0)
    execution_ignored_count: Mapped[int] = mapped_column(Integer, default=0)
    execution_opposite_count: Mapped[int] = mapped_column(Integer, default=0)
    execution_unresolved_count: Mapped[int] = mapped_column(Integer, default=0)
    outcomes_matured_count: Mapped[int] = mapped_column(Integer, default=0)
    market_summary_json: Mapped[dict | None] = mapped_column(JSON)
    decision_summary_json: Mapped[dict | None] = mapped_column(JSON)
    execution_summary_json: Mapped[dict | None] = mapped_column(JSON)
    outcome_summary_json: Mapped[dict | None] = mapped_column(JSON)
    reason_codes_json: Mapped[list | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(24), default="DEGRADED", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    review_version: Mapped[str] = mapped_column(String(64), default=DAILY_REVIEW_VERSION, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


def _immutable_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("decision_memory_is_immutable")


def _immutable_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("decision_memory_is_immutable")


event.listen(DecisionMemory, "before_update", _immutable_update)
event.listen(DecisionMemory, "before_delete", _immutable_delete)


__all__ = ["DailyReviewRun", "DecisionMemory", "DecisionOutcome"]
