"""Stable Pydantic contracts for the MARKET-1 market foundation APIs."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MarketSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str
    timezone: Literal["Asia/Shanghai"]
    session: str
    scheduled_session: str | None = None
    is_trading_day: bool | None
    is_market_open: bool
    trading_date: date
    latest_valid_trading_date: date | None = None
    previous_trading_date: date | None = None
    next_trading_date: date | None = None
    resolved_at: datetime
    data_status: str
    data_basis: Literal["live", "session_close", "previous_session_close"]
    display_label: str


class MajorIndexQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str | None = None
    code: str
    name: str
    last: float | None = None
    change: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    turnover: float | None = None
    as_of: datetime | None = None
    source: str | None = None
    quality: str
    status: Literal["available", "unavailable"]
    fallback: bool = False
    missing_fields: list[str] = Field(default_factory=list)


class AllAMedianMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]
    current_value: float | None = None
    daily_median_return: float | None = None
    trend_20d: float | None = None
    percentile_250d: float | None = None
    eligible_count: int = 0
    universe_total: int = 0
    excluded_count: int = 0
    suspended_count: int = 0
    as_of: datetime | None = None
    trading_date: date | None = None
    quality_grade: str
    quality_flags: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_status: str
    universe_version: str
    calculation_version: str


class TurnoverConcentrationMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]
    ratio: float | None = None
    avg_20d: float | None = None
    delta_vs_20d: float | None = None
    trend: Literal["rising", "falling", "flat", "unavailable"]
    percentile_250d: float | None = None
    top_n: int = 0
    eligible_count: int = 0
    total_turnover: float | None = None
    as_of: datetime | None = None
    trading_date: date | None = None
    quality_grade: str
    quality_flags: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_status: str
    calculation_version: str


class MarketBreadthMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]
    advancers: int = 0
    decliners: int = 0
    unchanged: int = 0
    suspended: int = 0
    limit_up: int = 0
    limit_down: int = 0
    total: int = 0
    eligible_count: int = 0
    advance_decline_ratio: float | None = None
    as_of: datetime | None = None
    trading_date: date | None = None
    quality_grade: str
    quality_flags: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_status: str
    calculation_version: str


class TotalTurnoverMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]
    value: float | None = None
    avg_20d: float | None = None
    delta_vs_20d: float | None = None
    unit: Literal["CNY"] = "CNY"
    as_of: datetime | None = None
    trading_date: date | None = None
    quality_grade: str
    quality_flags: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_status: str
    calculation_version: str


class SystemicRiskSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: Literal["LOW", "MEDIUM", "HIGH", "EXTREME", "UNKNOWN"]
    risk_score: float | None = None
    median_return: AllAMedianMetric
    turnover_concentration: TurnoverConcentrationMetric
    breadth: MarketBreadthMetric
    total_turnover: TotalTurnoverMetric
    major_indices: list[MajorIndexQuote] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    data_quality: str
    quality_flags: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_status: str
    as_of: datetime | None = None
    trading_date: date | None = None
    calculation_version: str


class MarketOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: MarketSessionResponse
    major_indices: list[MajorIndexQuote] = Field(default_factory=list)
    systemic_risk: SystemicRiskSnapshot
    breadth: MarketBreadthMetric
    all_a_median: AllAMedianMetric
    turnover_concentration: TurnoverConcentrationMetric
    total_turnover: TotalTurnoverMetric
    quote_as_of: datetime | None = None


__all__ = [
    "AllAMedianMetric",
    "MajorIndexQuote",
    "MarketBreadthMetric",
    "MarketOverviewResponse",
    "MarketSessionResponse",
    "SystemicRiskSnapshot",
    "TotalTurnoverMetric",
    "TurnoverConcentrationMetric",
]
