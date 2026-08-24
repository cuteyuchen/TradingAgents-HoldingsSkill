"""Persistent facts owned by the Phase E Portfolio Operating System."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class TradeLedgerEntry(Base):
    """A user-confirmed or reviewable real-world portfolio event.

    This is deliberately independent from recommendations.  A row records a
    materialized current view; revisions preserve the full audit history.
    """

    __tablename__ = "trade_ledger_entries"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "idempotency_key", name="uq_trade_ledger_portfolio_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"), index=True)
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("trigger_events.id", ondelete="SET NULL"), index=True)
    entry_type: Mapped[str] = mapped_column(String(24), index=True)
    security_code: Mapped[str | None] = mapped_column(String(16), index=True)
    security_name: Mapped[str | None] = mapped_column(String(128))
    side: Mapped[str | None] = mapped_column(String(8), index=True)
    quantity: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    gross_amount: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float | None] = mapped_column(Float)
    taxes: Mapped[float | None] = mapped_column(Float)
    net_amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    executed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(32), default="MANUAL", index=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(24), default="CONFIRMED", index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TradeLedgerRevision(Base):
    """Append-only audit record for a ledger mutation or void."""

    __tablename__ = "trade_ledger_revisions"
    __table_args__ = (
        UniqueConstraint("ledger_entry_id", "revision_no", name="uq_trade_ledger_revision_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ledger_entry_id: Mapped[int] = mapped_column(ForeignKey("trade_ledger_entries.id", ondelete="CASCADE"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    changes_json: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class PortfolioSnapshotDiff(Base):
    """Idempotent comparison of two confirmed snapshot facts."""

    __tablename__ = "portfolio_snapshot_diffs"
    __table_args__ = (
        UniqueConstraint("before_snapshot_id", "after_snapshot_id", name="uq_portfolio_snapshot_diff_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    before_snapshot_id: Mapped[int] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), index=True)
    after_snapshot_id: Mapped[int] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), index=True)
    diff_json: Mapped[dict] = mapped_column(JSON)
    reconciliation_status: Mapped[str] = mapped_column(String(32), default="UNEXPLAINED", index=True)
    calculation_version: Mapped[str] = mapped_column(String(64), default="portfolio-diff-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PortfolioRiskSnapshot(Base):
    """Versioned deterministic risk state for a confirmed portfolio snapshot."""

    __tablename__ = "portfolio_risk_snapshots"
    __table_args__ = (
        UniqueConstraint("calculation_key", name="uq_portfolio_risk_calculation_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    calculation_key: Mapped[str] = mapped_column(String(160), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    portfolio_snapshot_id: Mapped[int] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), index=True)
    market_score_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, index=True)
    total_assets: Mapped[float | None] = mapped_column(Float)
    market_value: Mapped[float | None] = mapped_column(Float)
    cash_ratio: Mapped[float | None] = mapped_column(Float)
    gross_exposure: Mapped[float | None] = mapped_column(Float)
    top1_weight: Mapped[float | None] = mapped_column(Float)
    top3_weight: Mapped[float | None] = mapped_column(Float)
    top5_weight: Mapped[float | None] = mapped_column(Float)
    hhi: Mapped[float | None] = mapped_column(Float)
    portfolio_vol_20: Mapped[float | None] = mapped_column(Float)
    portfolio_vol_60: Mapped[float | None] = mapped_column(Float)
    weighted_average_correlation: Mapped[float | None] = mapped_column(Float)
    max_pairwise_correlation: Mapped[float | None] = mapped_column(Float)
    unclassified_weight: Mapped[float | None] = mapped_column(Float)
    risk_flags_json: Mapped[list | None] = mapped_column(JSON)
    position_metrics_json: Mapped[list | None] = mapped_column(JSON)
    correlation_summary_json: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(24), default="DEGRADED", index=True)
    calculation_version: Mapped[str] = mapped_column(String(64), default="portfolio-risk-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


__all__ = [
    "TradeLedgerEntry",
    "TradeLedgerRevision",
    "PortfolioSnapshotDiff",
    "PortfolioRiskSnapshot",
]
