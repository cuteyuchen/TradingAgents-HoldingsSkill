"""Persistent Phase D trigger plans and event lifecycle records."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .clock import utc_now
from .database import Base


def utcnow() -> datetime:
    return utc_now()


class TriggerPlan(Base):
    __tablename__ = "trigger_plans"
    __table_args__ = (
        Index("ix_trigger_plans_target", "target_type", "target_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="USER", index=True)
    target_type: Mapped[str] = mapped_column(String(24), index=True)
    target_key: Mapped[str] = mapped_column(String(128), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    operator: Mapped[str] = mapped_column(String(16))
    threshold: Mapped[float] = mapped_column(Float)
    secondary_threshold: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[str] = mapped_column(String(4), default="P1", index=True)
    debounce_cycles: Mapped[int] = mapped_column(Integer, default=2)
    debounce_seconds: Mapped[int] = mapped_column(Integer, default=180)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="MANUAL", index=True)
    source_id: Mapped[str | None] = mapped_column(String(128), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime)


class TriggerEvent(Base):
    __tablename__ = "trigger_events"
    __table_args__ = (
        Index("ix_trigger_events_dedupe_detected", "dedupe_key", "detected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_plan_id: Mapped[int | None] = mapped_column(ForeignKey("trigger_plans.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), index=True)
    target_type: Mapped[str] = mapped_column(String(24), index=True)
    target_key: Mapped[str] = mapped_column(String(128), index=True)
    priority: Mapped[str] = mapped_column(String(4), default="P2", index=True)
    status: Mapped[str] = mapped_column(String(16), default="DETECTED", index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    consecutive_hits: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    metric: Mapped[str | None] = mapped_column(String(64))
    previous_value: Mapped[float | None] = mapped_column(Float)
    current_value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    evidence_json: Mapped[dict | None] = mapped_column(JSON)
    market_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    market_score_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    portfolio_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="SET NULL"), index=True)
    analysis_job_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="SET NULL"), index=True)
    analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    resolution: Mapped[str | None] = mapped_column(String(32), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(512), index=True)
    rule_id: Mapped[str | None] = mapped_column(String(128), index=True)
    rule_version: Mapped[str] = mapped_column(String(64), default="trigger-engine-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


__all__ = ["TriggerPlan", "TriggerEvent"]
