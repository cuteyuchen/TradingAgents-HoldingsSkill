"""Persistent market-data foundation models.

These tables are deliberately independent from user portfolios and analysis
records.  They provide the shared identity and trading-day facts that later
market-data providers and engines can consume.
"""
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .clock import utc_now
from .database import Base


def utcnow() -> datetime:
    return utc_now()


class SecurityMaster(Base):
    """Canonical identity and lifecycle metadata for CN securities."""

    __tablename__ = "security_master"
    __table_args__ = (
        UniqueConstraint("market", "exchange", "code", name="uq_security_master_market_exchange_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    symbol: Mapped[str | None] = mapped_column(String(24), index=True)
    name: Mapped[str | None] = mapped_column(String(128), index=True)
    security_type: Mapped[str] = mapped_column(String(24), default="STOCK", index=True)
    etf_category: Mapped[str | None] = mapped_column(String(32), index=True)
    listing_date: Mapped[date | None] = mapped_column(Date)
    delisting_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    board: Mapped[str | None] = mapped_column(String(16), index=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, default=100)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    source: Mapped[str | None] = mapped_column(String(64))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw_metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TradingCalendar(Base):
    """Persisted open/closed trading-day facts for a market."""

    __tablename__ = "trading_calendar"
    __table_args__ = (
        UniqueConstraint("market", "trade_date", name="uq_trading_calendar_market_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    previous_trade_date: Mapped[date | None] = mapped_column(Date)
    next_trade_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
