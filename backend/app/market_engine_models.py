"""Compact persisted facts produced by the Phase C Market Engine."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .clock import utc_now
from .database import Base


def utcnow() -> datetime:
    return utc_now()


class MarketMetricSnapshot(Base):
    __tablename__ = "market_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_market_metric_snapshots_snapshot_id"),
        UniqueConstraint("market", "trade_date", "captured_at", name="uq_market_metric_snapshots_capture"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), index=True)
    market_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    universe_rule_version: Mapped[str] = mapped_column(String(64), default="market-universe-v1")
    calculation_version: Mapped[str] = mapped_column(String(64), default="market-engine-v1")
    score_config_version: Mapped[str] = mapped_column(String(64), default="market-score-config-v1")
    universe_total: Mapped[int] = mapped_column(Integer, default=0)
    included_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    median_return: Mapped[float | None] = mapped_column(Float)
    advance_ratio: Mapped[float | None] = mapped_column(Float)
    top5_concentration: Mapped[float | None] = mapped_column(Float)
    total_amount: Mapped[float | None] = mapped_column(Float)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    breadth_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    trend_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    liquidity_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    profitability_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    diffusion_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    crowding_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    tail_risk_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    exclusion_counts_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class MarketScoreSnapshot(Base):
    __tablename__ = "market_score_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_id", name="uq_market_score_snapshots_snapshot_id"),
        UniqueConstraint("market", "trade_date", "captured_at", name="uq_market_score_snapshots_capture"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), index=True)
    metric_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    raw_score: Mapped[float | None] = mapped_column(Float)
    display_score: Mapped[float | None] = mapped_column(Float)
    regime: Mapped[str | None] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    freeze_reason: Mapped[str | None] = mapped_column(String(128))
    previous_display_score: Mapped[float | None] = mapped_column(Float)
    available_component_weight: Mapped[float] = mapped_column(Float, default=0.0)
    breadth_score: Mapped[float | None] = mapped_column(Float)
    trend_score: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    profitability_score: Mapped[float | None] = mapped_column(Float)
    diffusion_score: Mapped[float | None] = mapped_column(Float)
    crowding_score: Mapped[float | None] = mapped_column(Float)
    tail_risk_score: Mapped[float | None] = mapped_column(Float)
    positive_drivers_json: Mapped[list | None] = mapped_column(JSON)
    negative_drivers_json: Mapped[list | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    calculation_version: Mapped[str] = mapped_column(String(64), default="market-engine-v1")
    score_config_version: Mapped[str] = mapped_column(String(64), default="market-score-config-v1")
    universe_rule_version: Mapped[str] = mapped_column(String(64), default="market-universe-v1")
    parameter_set_version_id: Mapped[int | None] = mapped_column(Integer, index=True)
    parameter_set_version: Mapped[str | None] = mapped_column(String(64), index=True)
    parameter_set_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    governance_lineage_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AllAMedianIndexDaily(Base):
    __tablename__ = "all_a_median_index_daily"
    __table_args__ = (
        UniqueConstraint("market", "trade_date", "calculation_version", name="uq_all_a_median_index_daily_day_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    median_return: Mapped[float] = mapped_column(Float)
    index_value: Mapped[float] = mapped_column(Float)
    eligible_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    calculation_version: Mapped[str] = mapped_column(String(64), default="market-engine-v1")
    available_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DailyBarCache(Base):
    """Local QFQ daily-bar cache consumed by the Market Engine."""

    __tablename__ = "daily_bar_cache"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "code",
            "trade_date",
            "adjustment",
            name="uq_daily_bar_cache_market_code_day_adjustment",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), default="CN", index=True)
    exchange: Mapped[str | None] = mapped_column(String(8), index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    adjustment: Mapped[str] = mapped_column(String(8), default="QFQ", index=True)
    provider: Mapped[str] = mapped_column(String(64), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    available_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    quality_status: Mapped[str] = mapped_column(String(24), default="VALID", index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


__all__ = ["MarketMetricSnapshot", "MarketScoreSnapshot", "AllAMedianIndexDaily", "DailyBarCache"]
