"""Append-only Phase I evaluation evidence models.

Evaluation rows deliberately reference the existing Decision Memory and
snapshot facts.  They never become a second source of portfolio decisions.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base

EVALUATION_SCHEMA_VERSION = "1.0.0"


def utcnow() -> datetime:
    return datetime.now(UTC)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (UniqueConstraint("run_id", name="uq_evaluation_runs_run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    run_type: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    code_version: Mapped[str | None] = mapped_column(String(128))
    git_commit: Mapped[str | None] = mapped_column(String(64))
    decision_contract_version: Mapped[str] = mapped_column(String(24), default="2.4.0")
    evaluation_schema_version: Mapped[str] = mapped_column(String(24), default=EVALUATION_SCHEMA_VERSION)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    result_hash: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DecisionEpisode(Base):
    __tablename__ = "decision_evaluation_episodes"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "decision_run_id", "symbol", name="uq_decision_episode_run_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), default="__PORTFOLIO__", index=True)
    decision_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    decision_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    decision_memory_id: Mapped[int | None] = mapped_column(ForeignKey("decision_memories.id", ondelete="SET NULL"), index=True)
    market_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    portfolio_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="SET NULL"), index=True)
    candidate_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_runs.id", ondelete="SET NULL"), index=True)
    trigger_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("trigger_events.id", ondelete="SET NULL"), index=True)
    analysis_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    decision_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("decision_memories.id", ondelete="SET NULL"), index=True)
    candidate_stage: Mapped[str | None] = mapped_column(String(16), index=True)
    decision_type: Mapped[str] = mapped_column(String(32), index=True)
    portfolio_gate_result: Mapped[str | None] = mapped_column(String(32), index=True)
    no_action_reason: Mapped[str | None] = mapped_column(Text)
    source_data_cutoff: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_mode: Mapped[str] = mapped_column(String(40), default="FACT_REPLAY", index=True)
    evidence_status: Mapped[str] = mapped_column(String(40), default="READY", index=True)
    status: Mapped[str] = mapped_column(String(24), default="FROZEN", index=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    decision_contract_version: Mapped[str] = mapped_column(String(24), default="2.4.0")
    evaluation_schema_version: Mapped[str] = mapped_column(String(24), default=EVALUATION_SCHEMA_VERSION)
    code_version: Mapped[str | None] = mapped_column(String(128))
    frozen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class EvaluationSnapshot(Base):
    __tablename__ = "decision_evaluation_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "episode_id", "input_type", "source_id", "snapshot_id", "version",
            name="uq_decision_evaluation_snapshot_input",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("decision_evaluation_episodes.id", ondelete="CASCADE"), index=True)
    input_type: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str | None] = mapped_column(String(128), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(128), index=True)
    version: Mapped[str | None] = mapped_column(String(128))
    timestamp: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DecisionEvaluationOutcome(Base):
    __tablename__ = "decision_evaluation_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "episode_id", "target_key", "horizon_trading_days", "calculation_version",
            name="uq_decision_evaluation_outcome_horizon",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("decision_evaluation_episodes.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="SYMBOL", index=True)
    target_key: Mapped[str] = mapped_column(String(32), index=True)
    horizon_trading_days: Mapped[int] = mapped_column(Integer, index=True)
    reference_trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    target_trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    start_price: Mapped[float | None] = mapped_column(Float)
    end_price: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    raw_return: Mapped[float | None] = mapped_column(Float)
    directional_return: Mapped[float | None] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    sector_return: Mapped[float | None] = mapped_column(Float)
    mfe: Mapped[float | None] = mapped_column(Float)
    mae: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    price_adjustment_method: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    quality_status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    observation_complete: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    source_refs_json: Mapped[dict | None] = mapped_column(JSON)
    calculation_version: Mapped[str] = mapped_column(String(64), default="evaluation-outcome-v1", index=True)
    recalculation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_source_change_at: Mapped[datetime | None] = mapped_column(DateTime)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CandidateEvaluation(Base):
    __tablename__ = "candidate_evaluations"
    __table_args__ = (UniqueConstraint("candidate_run_id", "code", name="uq_candidate_evaluation_run_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    candidate_run_id: Mapped[int] = mapped_column(ForeignKey("candidate_runs.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("decision_evaluation_episodes.id", ondelete="SET NULL"), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(16), index=True)
    stage_entered_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    stage_exited_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_trading_days: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_data_cutoff: Mapped[datetime] = mapped_column(DateTime, index=True)
    outcome_summary_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class TriggerEvaluation(Base):
    __tablename__ = "trigger_evaluations"
    __table_args__ = (UniqueConstraint("trigger_event_id", name="uq_trigger_evaluation_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    trigger_event_id: Mapped[int] = mapped_column(ForeignKey("trigger_events.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("decision_evaluation_episodes.id", ondelete="SET NULL"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), index=True)
    priority: Mapped[str | None] = mapped_column(String(4), index=True)
    trigger_status: Mapped[str] = mapped_column(String(24), index=True)
    analysis_refreshed: Mapped[bool] = mapped_column(Boolean, default=False)
    decision_changed: Mapped[bool | None] = mapped_column(Boolean)
    resulting_decision_type: Mapped[str | None] = mapped_column(String(32), index=True)
    movement_return: Mapped[float | None] = mapped_column(Float)
    quality_status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_data_cutoff: Mapped[datetime] = mapped_column(DateTime, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class PaperObservationRun(Base):
    __tablename__ = "paper_observation_runs"
    __table_args__ = (UniqueConstraint("run_id", name="uq_paper_observation_runs_run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    observation_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True)
    source_data_cutoff: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    code_version: Mapped[str | None] = mapped_column(String(128))
    decision_contract_version: Mapped[str] = mapped_column(String(24), default="2.4.0")
    evaluation_schema_version: Mapped[str] = mapped_column(String(24), default=EVALUATION_SCHEMA_VERSION)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    missing_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class PaperObservation(Base):
    __tablename__ = "paper_observations"
    __table_args__ = (UniqueConstraint("observation_id", name="uq_paper_observations_observation_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("paper_observation_runs.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("decision_evaluation_episodes.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_data_cutoff: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    freeze_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    missing_reason: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


def _immutable_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("evaluation_episode_is_immutable")


def _immutable_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("evaluation_episode_is_immutable")


for _model in (DecisionEpisode, EvaluationSnapshot):
    event.listen(_model, "before_update", _immutable_update)
    event.listen(_model, "before_delete", _immutable_delete)


__all__ = [
    "CandidateEvaluation",
    "DecisionEpisode",
    "DecisionEvaluationOutcome",
    "EVALUATION_SCHEMA_VERSION",
    "EvaluationRun",
    "EvaluationSnapshot",
    "PaperObservation",
    "PaperObservationRun",
    "TriggerEvaluation",
]
