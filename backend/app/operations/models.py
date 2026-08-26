"""Lightweight persisted orchestration state; no investment facts live here."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .config import WORKFLOW_VERSION


def utcnow() -> datetime:
    return datetime.now(UTC)


class DailyOperationalRun(Base):
    __tablename__ = "daily_operational_runs"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "trade_date",
            "workflow_version",
            name="uq_daily_operational_portfolio_day_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    checkpoint_state_json: Mapped[dict | None] = mapped_column(JSON)
    maintenance_result_json: Mapped[dict | None] = mapped_column(JSON)
    notification_state_json: Mapped[dict | None] = mapped_column(JSON)
    review_state_json: Mapped[dict | None] = mapped_column(JSON)
    workflow_version: Mapped[str] = mapped_column(String(64), default=WORKFLOW_VERSION, index=True)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DailyOperationalCheckpoint(Base):
    """Database claim for one fixed checkpoint execution owner."""

    __tablename__ = "daily_operational_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "trade_date",
            "checkpoint_name",
            "workflow_version",
            name="uq_daily_operational_checkpoint_owner",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    checkpoint_name: Mapped[str] = mapped_column(String(32), index=True)
    workflow_version: Mapped[str] = mapped_column(String(64), default=WORKFLOW_VERSION, index=True)
    status: Mapped[str] = mapped_column(String(24), default="CLAIMED", index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="SET NULL"), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OperatingNotification(Base):
    """Durable material-event claim used for restart-safe at-least-once dispatch."""

    __tablename__ = "operating_notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "portfolio_id",
            "dedupe_key",
            name="uq_operating_notification_dedupe",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notification_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24), default="DISPATCHING", index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    last_error: Mapped[str | None] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


__all__ = ["DailyOperationalRun", "DailyOperationalCheckpoint", "OperatingNotification"]
