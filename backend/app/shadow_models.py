"""Phase N live decision observation and paper-only shadow facts.

The shadow tables deliberately do not share mutable state with the real
portfolio or Trade Ledger.  Decision observations, fills, and ledger entries
are append-only audit facts; materialized positions and daily snapshots can be
rebuilt from those facts.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class LiveDecisionObservation(Base):
    """Immutable observation of one finalized production decision."""

    __tablename__ = "live_decision_observations"
    __table_args__ = (
        UniqueConstraint("calculation_key", name="uq_live_decision_observation_calculation_key"),
        Index("ix_live_decision_observation_owner_date", "user_id", "portfolio_id", "trade_date"),
        Index("ix_live_decision_observation_finalized", "decision_finalized_at"),
        CheckConstraint(
            "decision_kind IN ('CHECKPOINT', 'TRIGGER', 'MANUAL')",
            name="ck_live_decision_observation_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    decision_kind: Mapped[str] = mapped_column(String(16), default="CHECKPOINT", index=True)
    decision_checkpoint: Mapped[str | None] = mapped_column(String(16), index=True)
    trigger_type: Mapped[str | None] = mapped_column(String(32), index=True)
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("trigger_events.id", ondelete="SET NULL"), index=True)
    trigger_priority: Mapped[str | None] = mapped_column(String(4), index=True)
    trigger_reason: Mapped[str | None] = mapped_column(Text)
    source_analysis_job_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="SET NULL"), index=True)
    source_analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    decision_memory_id: Mapped[int | None] = mapped_column(ForeignKey("decision_memories.id", ondelete="SET NULL"), index=True)
    candidate_run_id: Mapped[int | None] = mapped_column(ForeignKey("candidate_runs.id", ondelete="SET NULL"), index=True)
    portfolio_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="SET NULL"), index=True)
    market_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    market_score_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    market_metric_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    parameter_set_version_id: Mapped[int | None] = mapped_column(Integer, index=True)
    parameter_set_version: Mapped[str | None] = mapped_column(String(64), index=True)
    parameter_set_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_contract_version: Mapped[str] = mapped_column(String(32), default="2.4.0")
    decision_contract_version: Mapped[str] = mapped_column(String(32), default="2.4.0")
    runtime_prompt_version: Mapped[str | None] = mapped_column(String(64))
    runtime_prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    skill_version: Mapped[str | None] = mapped_column(String(64))
    skill_sha256: Mapped[str | None] = mapped_column(String(64))
    market_engine_version: Mapped[str | None] = mapped_column(String(64))
    candidate_engine_version: Mapped[str | None] = mapped_column(String(64))
    model_provider: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    final_action: Mapped[str] = mapped_column(String(24), index=True)
    raw_final_action: Mapped[str | None] = mapped_column(String(32))
    final_reason_codes_json: Mapped[list | None] = mapped_column(JSON)
    selected_actions_json: Mapped[list | None] = mapped_column(JSON)
    selected_candidate_ids_json: Mapped[list | None] = mapped_column(JSON)
    market_regime: Mapped[str | None] = mapped_column(String(32), index=True)
    market_score: Mapped[float | None] = mapped_column(Float)
    market_quality: Mapped[str | None] = mapped_column(String(24), index=True)
    portfolio_quality: Mapped[str | None] = mapped_column(String(24), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    data_coverage: Mapped[float | None] = mapped_column(Float)
    decision_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_finalized_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    deterministic_core_hash: Mapped[str | None] = mapped_column(String(64))
    observation_hash: Mapped[str] = mapped_column(String(64), index=True)
    calculation_key: Mapped[str] = mapped_column(String(255), index=True)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    live_evidence_eligibility: Mapped[str] = mapped_column(String(32), default="DIAGNOSTIC_ONLY", index=True)
    calculation_version: Mapped[str] = mapped_column(String(64), default="live-decision-observation-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LiveQuoteObservation(Base):
    """Durable, bounded quote observation used to prove future execution."""

    __tablename__ = "live_quote_observations"
    __table_args__ = (
        UniqueConstraint("quote_key", name="uq_live_quote_observation_quote_key"),
        Index("ix_live_quote_observation_code_time", "code", "captured_at"),
        Index("ix_live_quote_observation_trade_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_key: Mapped[str] = mapped_column(String(255), index=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    security_type: Mapped[str | None] = mapped_column(String(24), index=True)
    trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    captured_at_precision: Mapped[str] = mapped_column(String(16), default="EXACT")
    price: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    limit_up: Mapped[bool] = mapped_column(Boolean, default=False)
    limit_down: Mapped[bool] = mapped_column(Boolean, default=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    instrument_active: Mapped[bool] = mapped_column(Boolean, default=True)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    provider: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), index=True)
    price_basis: Mapped[str] = mapped_column(String(32), default="RAW_QUOTE")
    source_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ShadowAccount(Base):
    """Paper-only account isolated from the real portfolio."""

    __tablename__ = "shadow_accounts"
    __table_args__ = (
        Index(
            "uq_shadow_accounts_active_owner",
            "user_id",
            "source_portfolio_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_shadow_accounts_owner_status", "user_id", "source_portfolio_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    mode: Mapped[str] = mapped_column(String(32), default="FOLLOW_FINAL_ACTIONS")
    base_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    paper_only: Mapped[bool] = mapped_column(Boolean, default=True)
    initialized_from_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="SET NULL"), index=True)
    initialized_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    starting_cash: Mapped[float] = mapped_column(Float, default=0.0)
    current_cash: Mapped[float] = mapped_column(Float, default=0.0)
    reserved_cash: Mapped[float] = mapped_column(Float, default=0.0)
    shadow_generation: Mapped[int] = mapped_column(Integer, default=1, index=True)
    execution_contract_version: Mapped[str] = mapped_column(String(64), default="shadow-execution-v1")
    expires_policy: Mapped[str] = mapped_column(String(64), default="NEXT_TRADING_DAY_CLOSE")
    config_json: Mapped[dict | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ShadowPosition(Base):
    """Materialized position state for one shadow generation."""

    __tablename__ = "shadow_positions"
    __table_args__ = (
        UniqueConstraint("shadow_account_id", "shadow_generation", "code", name="uq_shadow_position_generation_code"),
        Index("ix_shadow_positions_account_generation", "shadow_account_id", "shadow_generation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shadow_account_id: Mapped[int] = mapped_column(ForeignKey("shadow_accounts.id", ondelete="CASCADE"), index=True)
    shadow_generation: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    security_type: Mapped[str | None] = mapped_column(String(24), index=True)
    etf_category: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    sellable_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    average_cost: Mapped[float] = mapped_column(Float, default=0.0)
    current_mark: Mapped[float | None] = mapped_column(Float)
    market_value: Mapped[float | None] = mapped_column(Float)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float)
    last_mark_at: Mapped[datetime | None] = mapped_column(DateTime)
    acquired_decision_ids_json: Mapped[list | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ShadowOrderIntent(Base):
    """A deterministic paper-order intent; it is not a broker order."""

    __tablename__ = "shadow_order_intents"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_shadow_order_intent_idempotency_key"),
        Index("ix_shadow_order_intent_pending_code", "status", "code"),
        Index("ix_shadow_order_intent_account_generation", "shadow_account_id", "shadow_generation"),
        CheckConstraint(
            "status IN ('PENDING', 'FILLED', 'PARTIAL', 'BLOCKED', 'EXPIRED', 'CANCELLED', 'SUPERSEDED')",
            name="ck_shadow_order_intent_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shadow_account_id: Mapped[int] = mapped_column(ForeignKey("shadow_accounts.id", ondelete="CASCADE"), index=True)
    shadow_generation: Mapped[int] = mapped_column(Integer, index=True)
    decision_observation_id: Mapped[int] = mapped_column(ForeignKey("live_decision_observations.id", ondelete="CASCADE"), index=True)
    action_index: Mapped[int] = mapped_column(Integer, default=0)
    code: Mapped[str] = mapped_column(String(16), index=True)
    security_type: Mapped[str | None] = mapped_column(String(24), index=True)
    side: Mapped[str] = mapped_column(String(8), index=True)
    target_qty: Mapped[float | None] = mapped_column(Float)
    target_notional: Mapped[float | None] = mapped_column(Float)
    target_weight: Mapped[float | None] = mapped_column(Float)
    decision_reference_price: Mapped[float | None] = mapped_column(Float)
    decision_reference_basis: Mapped[str | None] = mapped_column(String(32))
    decision_finalized_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    earliest_executable_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    reason_codes_json: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), index=True)


class ShadowFill(Base):
    """A full deterministic paper fill from one persisted future quote."""

    __tablename__ = "shadow_fills"
    __table_args__ = (
        UniqueConstraint("execution_key", name="uq_shadow_fill_execution_key"),
        Index("ix_shadow_fill_account_generation", "shadow_account_id", "shadow_generation"),
        Index("ix_shadow_fill_at", "fill_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_intent_id: Mapped[int] = mapped_column(ForeignKey("shadow_order_intents.id", ondelete="CASCADE"), index=True)
    shadow_account_id: Mapped[int] = mapped_column(ForeignKey("shadow_accounts.id", ondelete="CASCADE"), index=True)
    shadow_generation: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    gross_amount: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)
    total_cost: Mapped[float] = mapped_column(Float)
    price_basis: Mapped[str] = mapped_column(String(32), default="RAW_QUOTE")
    quote_observation_id: Mapped[int] = mapped_column(ForeignKey("live_quote_observations.id", ondelete="RESTRICT"), index=True)
    quote_source_ref: Mapped[str | None] = mapped_column(String(255))
    quote_captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    fill_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    fill_quality: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    execution_key: Mapped[str] = mapped_column(String(255), index=True)
    slippage_not_modeled: Mapped[bool] = mapped_column(Boolean, default=True)
    execution_delay_seconds: Mapped[float | None] = mapped_column(Float)
    execution_delay_price_drift: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ShadowLedgerEntry(Base):
    """Append-only shadow cash/position event."""

    __tablename__ = "shadow_ledger_entries"
    __table_args__ = (
        UniqueConstraint("entry_key", name="uq_shadow_ledger_entry_key"),
        Index("ix_shadow_ledger_account_generation", "shadow_account_id", "shadow_generation"),
        Index("ix_shadow_ledger_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_key: Mapped[str] = mapped_column(String(255), index=True)
    shadow_account_id: Mapped[int] = mapped_column(ForeignKey("shadow_accounts.id", ondelete="CASCADE"), index=True)
    shadow_generation: Mapped[int] = mapped_column(Integer, index=True)
    entry_type: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str | None] = mapped_column(String(16), index=True)
    quantity: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    gross_amount: Mapped[float | None] = mapped_column(Float)
    commission: Mapped[float | None] = mapped_column(Float)
    tax: Mapped[float | None] = mapped_column(Float)
    cash_delta: Mapped[float | None] = mapped_column(Float)
    sellable_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    decision_observation_id: Mapped[int | None] = mapped_column(ForeignKey("live_decision_observations.id", ondelete="SET NULL"), index=True)
    order_intent_id: Mapped[int | None] = mapped_column(ForeignKey("shadow_order_intents.id", ondelete="SET NULL"), index=True)
    fill_id: Mapped[int | None] = mapped_column(ForeignKey("shadow_fills.id", ondelete="SET NULL"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ShadowDailySnapshot(Base):
    """Materialized daily equity mark for one shadow generation."""

    __tablename__ = "shadow_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("shadow_account_id", "shadow_generation", "trade_date", name="uq_shadow_daily_snapshot_day"),
        Index("ix_shadow_daily_snapshot_trade_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shadow_account_id: Mapped[int] = mapped_column(ForeignKey("shadow_accounts.id", ondelete="CASCADE"), index=True)
    shadow_generation: Mapped[int] = mapped_column(Integer, index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    total_equity: Mapped[float] = mapped_column(Float, default=0.0)
    daily_return: Mapped[float | None] = mapped_column(Float)
    cumulative_return: Mapped[float | None] = mapped_column(Float)
    drawdown: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float, default=0.0)
    position_count: Mapped[int] = mapped_column(Integer, default=0)
    action_count: Mapped[int] = mapped_column(Integer, default=0)
    no_action_count: Mapped[int] = mapped_column(Integer, default=0)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    excess_return: Mapped[float | None] = mapped_column(Float)
    market_regime: Mapped[str | None] = mapped_column(String(32))
    price_basis: Mapped[str | None] = mapped_column(String(32))
    price_basis_compatible: Mapped[bool] = mapped_column(Boolean, default=True)
    source_refs_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LiveDecisionOutcome(Base):
    """Forward outcome, independent from whether a shadow fill occurred."""

    __tablename__ = "live_decision_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "decision_observation_id",
            "target_type",
            "target_key",
            "horizon_trading_days",
            "calculation_version",
            name="uq_live_decision_outcome_target_horizon",
        ),
        Index("ix_live_decision_outcome_due", "next_due_date", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_observation_id: Mapped[int] = mapped_column(ForeignKey("live_decision_observations.id", ondelete="CASCADE"), index=True)
    shadow_account_id: Mapped[int | None] = mapped_column(ForeignKey("shadow_accounts.id", ondelete="SET NULL"), index=True)
    shadow_generation: Mapped[int | None] = mapped_column(Integer, index=True)
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_key: Mapped[str] = mapped_column(String(64), index=True)
    recommended_action: Mapped[str] = mapped_column(String(24), index=True)
    horizon_trading_days: Mapped[int] = mapped_column(Integer, index=True)
    reference_trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    reference_at: Mapped[datetime | None] = mapped_column(DateTime)
    reference_price: Mapped[float | None] = mapped_column(Float)
    reference_price_basis: Mapped[str | None] = mapped_column(String(32))
    target_trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    target_price: Mapped[float | None] = mapped_column(Float)
    forward_return: Mapped[float | None] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    excess_return: Mapped[float | None] = mapped_column(Float)
    mfe: Mapped[float | None] = mapped_column(Float)
    mae: Mapped[float | None] = mapped_column(Float)
    drawdown: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str | None] = mapped_column(String(16))
    execution_eligible: Mapped[bool | None] = mapped_column(Boolean)
    shadow_filled: Mapped[bool | None] = mapped_column(Boolean)
    fill_delay_seconds: Mapped[float | None] = mapped_column(Float)
    fill_drift: Mapped[float | None] = mapped_column(Float)
    realized_pnl: Mapped[float | None] = mapped_column(Float)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float)
    candidate_opportunity_cost: Mapped[float | None] = mapped_column(Float)
    drawdown_avoided: Mapped[float | None] = mapped_column(Float)
    risk_off_correct: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    quality_status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    live_evidence_eligibility: Mapped[str] = mapped_column(String(32), default="INSUFFICIENT_SAMPLE", index=True)
    next_due_date: Mapped[date | None] = mapped_column(Date, index=True)
    source_refs_json: Mapped[dict | None] = mapped_column(JSON)
    calculation_version: Mapped[str] = mapped_column(String(64), default="live-shadow-outcome-v1")
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DecisionActualAlignment(Base):
    """Fact-only match between a decision action and the real Trade Ledger."""

    __tablename__ = "decision_actual_alignments"
    __table_args__ = (
        UniqueConstraint("decision_observation_id", "code", "side", name="uq_decision_actual_alignment_target"),
        Index("ix_decision_actual_alignment_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_observation_id: Mapped[int] = mapped_column(ForeignKey("live_decision_observations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    actual_trade_ledger_id: Mapped[int | None] = mapped_column(ForeignKey("trade_ledger_entries.id", ondelete="SET NULL"), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime)
    time_delta_seconds: Mapped[float | None] = mapped_column(Float)
    quantity_ratio: Mapped[float | None] = mapped_column(Float)
    source_refs_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


def _immutable_update(_mapper, _connection, target) -> None:
    raise RuntimeError(f"{target.__tablename__}_is_immutable")


def _immutable_delete(_mapper, _connection, target) -> None:
    raise RuntimeError(f"{target.__tablename__}_is_immutable")


# Decision, quote, fill, and ledger facts are append-only.  An order intent is
# intentionally mutable because its deterministic lifecycle is PENDING ->
# FILLED/BLOCKED/EXPIRED (and never a broker order).
for _model in (LiveDecisionObservation, LiveQuoteObservation, ShadowFill, ShadowLedgerEntry):
    event.listen(_model, "before_update", _immutable_update)
    event.listen(_model, "before_delete", _immutable_delete)


__all__ = [
    "DecisionActualAlignment",
    "LiveDecisionObservation",
    "LiveDecisionOutcome",
    "LiveQuoteObservation",
    "ShadowAccount",
    "ShadowDailySnapshot",
    "ShadowFill",
    "ShadowLedgerEntry",
    "ShadowOrderIntent",
    "ShadowPosition",
]
