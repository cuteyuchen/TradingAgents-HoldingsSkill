"""Point-in-time historical data foundation models.

Every historical fact records the time it became effective, the time the source
made it available, and the time this system captured/ingested it.  Current
SecurityMaster state is deliberately never projected backward into these rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..clock import utc_now
from ..database import Base


def utcnow() -> datetime:
    return utc_now()


class SecurityLifecycleEvent(Base):
    __tablename__ = "security_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "code",
            "effective_date",
            "event_type",
            "source",
            "source_ref",
            name="uq_security_lifecycle_source_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    security_type: Mapped[str | None] = mapped_column(String(24), index=True)
    security_name: Mapped[str | None] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(160), index=True)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SecurityTradingStatusDaily(Base):
    __tablename__ = "security_trading_status_daily"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "code",
            "trade_date",
            "source",
            "source_ref",
            name="uq_security_trading_status_source_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(160), index=True)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SecurityClassificationDaily(Base):
    __tablename__ = "security_classification_daily"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "code",
            "trade_date",
            "source",
            "source_ref",
            name="uq_security_classification_source_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    classification: Mapped[str] = mapped_column(String(24), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    is_name_derived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(160), index=True)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SecurityValuationDaily(Base):
    __tablename__ = "security_valuation_daily"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "code",
            "trade_date",
            "source",
            "source_ref",
            name="uq_security_valuation_source_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    pe_ttm: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    ps_ttm: Mapped[float | None] = mapped_column(Float)
    dividend_yield: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float)
    float_market_cap: Mapped[float | None] = mapped_column(Float)
    valuation_effective_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(160), index=True)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FundamentalReport(Base):
    __tablename__ = "fundamental_reports"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "code",
            "report_period",
            "report_type",
            "source",
            "source_ref",
            name="uq_fundamental_reports_source_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    report_period: Mapped[date] = mapped_column(Date, index=True)
    report_type: Mapped[str] = mapped_column(String(24), default="ANNUAL", index=True)
    announced_at: Mapped[datetime | None] = mapped_column(DateTime)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, default=0)
    is_restatement: Mapped[bool] = mapped_column(Boolean, default=False)
    roe: Mapped[float | None] = mapped_column(Float)
    revenue: Mapped[float | None] = mapped_column(Float)
    revenue_yoy: Mapped[float | None] = mapped_column(Float)
    net_profit: Mapped[float | None] = mapped_column(Float)
    net_profit_yoy: Mapped[float | None] = mapped_column(Float)
    gross_margin: Mapped[float | None] = mapped_column(Float)
    debt_ratio: Mapped[float | None] = mapped_column(Float)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    source_available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(160), index=True)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EtfMetadataHistory(Base):
    __tablename__ = "etf_metadata_history"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "code",
            "effective_date",
            "source",
            "source_ref",
            name="uq_etf_metadata_history_source_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    index_code: Mapped[str | None] = mapped_column(String(32), index=True)
    benchmark_code: Mapped[str | None] = mapped_column(String(32))
    fund_type: Mapped[str | None] = mapped_column(String(32))
    sector_theme_json: Mapped[list | None] = mapped_column(JSON)
    inception_date: Mapped[date | None] = mapped_column(Date)
    source_available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(160), index=True)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PriceBasisMetadata(Base):
    __tablename__ = "price_basis_metadata"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "code",
            "trade_date",
            "source",
            "source_ref",
            name="uq_price_basis_metadata_source_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    basis: Mapped[str] = mapped_column(String(16), default="QFQ", index=True)
    adjustment_factor: Mapped[float | None] = mapped_column(Float)
    source_available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str | None] = mapped_column(String(160), index=True)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HistoricalDataSyncRun(Base):
    __tablename__ = "historical_data_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_type: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    start_date: Mapped[date | None] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str | None] = mapped_column(String(64), index=True)
    source: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    error_summary: Mapped[str | None] = mapped_column(Text)
    source_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    coverage_summary_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


__all__ = [
    "EtfMetadataHistory",
    "FundamentalReport",
    "HistoricalDataSyncRun",
    "PriceBasisMetadata",
    "SecurityClassificationDaily",
    "SecurityLifecycleEvent",
    "SecurityTradingStatusDaily",
    "SecurityValuationDaily",
]
