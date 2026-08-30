"""Persistent metadata for market-data runtime health and snapshots.

The quote payload itself deliberately remains an in-memory/runtime concern.  These
tables keep the small amount of metadata needed to answer where a snapshot came
from, how complete it was, and whether a provider is currently healthy.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ProviderHealth(Base):
    """Aggregated provider health by provider and data type."""

    __tablename__ = "provider_health"
    __table_args__ = (
        UniqueConstraint("provider_name", "data_type", name="uq_provider_health_provider_data_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_name: Mapped[str] = mapped_column(String(64), index=True)
    data_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), default="HEALTHY", index=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_latency_ms: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SourceLineage(Base):
    """Snapshot-level or critical-field source lineage.

    We intentionally do not create one row per quote field.  Callers can record a
    snapshot-level row and, when needed, a small number of critical-field rows.
    """

    __tablename__ = "source_lineage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_key: Mapped[str] = mapped_column(String(128), index=True)
    field_name: Mapped[str | None] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    provider_endpoint: Mapped[str | None] = mapped_column(String(512))
    operation: Mapped[str | None] = mapped_column(String(128))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    fallback_level: Mapped[int] = mapped_column(Integer, default=0)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MarketSnapshot(Base):
    """Metadata for a normalized quote snapshot.

    The list of quotes is not persisted here; keeping 5,000 rows per polling
    cycle in SQLite would turn this metadata table into a high-frequency quote
    store, which is explicitly outside Phase B.
    """

    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    snapshot_key: Mapped[str | None] = mapped_column(String(128), index=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    fallback_level: Mapped[int] = mapped_column(Integer, default=0)
    expected_count: Mapped[int] = mapped_column(Integer, default=0)
    received_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(24), default="MISSING", index=True)
    errors_json: Mapped[list | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
