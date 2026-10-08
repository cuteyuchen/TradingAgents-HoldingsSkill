"""Provider-independent MARKET-2 contracts. Percentages are percentage points."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


InstrumentType = Literal["STOCK", "ETF", "INDEX"]
MarketStatus = Literal["available", "degraded", "stale", "unavailable", "unsupported", "empty"]
QualityGrade = Literal["A", "B", "C", "D", "F"]
DataBasis = Literal["live", "session_close", "previous_session_close"]
BarInterval = Literal["1d", "1w", "1M"]
Adjustment = Literal["none", "forward", "backward"]
MAX_BATCH_QUOTES = 100
MAX_BAR_LIMIT = 1000


class MarketContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class InstrumentIdentity(MarketContract):
    instrument_id: str
    code: str
    symbol: str
    exchange: Literal["SSE", "SZSE", "BSE"]
    name: str | None = None
    instrument_type: InstrumentType
    board: str | None = None
    currency: str
    lot_size: int | None = None
    is_st: bool
    is_suspended: bool
    status: str


class InstrumentCapabilities(MarketContract):
    quote: bool = True
    bars: bool = True
    order_book: bool = False
    capital_flow: bool = False
    fundamentals: bool = False
    etf_profile: bool = False
    bar_intervals: list[BarInterval] = Field(default_factory=lambda: ["1d", "1w", "1M"])
    adjustments: list[Adjustment] = Field(default_factory=lambda: ["none", "forward", "backward"])


class MarketDataQuality(MarketContract):
    status: MarketStatus = "available"
    quality: QualityGrade = "A"
    quality_flags: list[str] = Field(default_factory=list)
    error_code: str | None = None


class MarketDataProvenance(MarketDataQuality):
    provider: str | None = None
    provider_profile: str | None = None
    source: str | None = None
    fallback: bool = False
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    trading_date: date | None = None
    data_basis: DataBasis = "live"


class InstrumentQuoteResponse(MarketDataProvenance):
    instrument: InstrumentIdentity
    last: float | None = None
    change: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    turnover: float | None = None
    amplitude_pct: float | None = None
    turnover_rate: float | None = None
    volume_unit: Literal["shares"] = "shares"
    turnover_unit: Literal["CNY"] = "CNY"


class InstrumentBar(MarketContract):
    time: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    turnover: float | None = None


class InstrumentBarsResponse(MarketDataProvenance):
    instrument: InstrumentIdentity
    interval: BarInterval
    adjustment: Adjustment
    bars: list[InstrumentBar] = Field(default_factory=list)
    mixed_sources: bool = False
    volume_unit: Literal["shares"] = "shares"
    turnover_unit: Literal["CNY"] = "CNY"


class OrderBookLevel(MarketContract):
    level: int = Field(ge=1, le=5)
    price: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)


class InstrumentOrderBookResponse(MarketDataProvenance):
    instrument: InstrumentIdentity
    bids: list[OrderBookLevel] = Field(default_factory=list, max_length=5)
    asks: list[OrderBookLevel] = Field(default_factory=list, max_length=5)
    bid_volume_total: float | None = None
    ask_volume_total: float | None = None
    order_ratio: float | None = None
    order_difference: float | None = None
    inner_volume: float | None = None
    outer_volume: float | None = None
    derived: bool = False
    derived_fields: list[Literal["order_ratio", "order_difference"]] = Field(default_factory=list)
    volume_unit: Literal["shares"] = "shares"


class CapitalFlowSnapshot(MarketContract):
    main_net_inflow: float | None = None
    super_large_net_inflow: float | None = None
    large_net_inflow: float | None = None
    medium_net_inflow: float | None = None
    small_net_inflow: float | None = None


class CapitalFlowHistoryPoint(CapitalFlowSnapshot):
    time: date


class InstrumentCapitalFlowResponse(MarketDataProvenance):
    instrument: InstrumentIdentity
    current: CapitalFlowSnapshot | None = None
    history: list[CapitalFlowHistoryPoint] = Field(default_factory=list)
    currency: Literal["CNY"] = "CNY"
    provider_derived: Literal[True] = True
    methodology: Literal["provider_defined"] = "provider_defined"


class InstrumentMetadata(MarketContract):
    board: str | None = None
    industry: str | None = None
    concepts: list[str] | None = None
    is_st: bool
    list_date: date | None = None
    lot_size: int | None = None
    price_limit_rule: str | None = None
    available_for_trading: bool
    fund_type: str | None = None
    underlying_index: str | None = None
    management_company: str | None = None
    expense_ratio: float | None = None
    tracking_target: str | None = None
    publisher: str | None = None
    base_date: date | None = None
    base_value: float | None = None
    constituent_count: int | None = None


class InstrumentMetadataResponse(MarketDataProvenance):
    identity: InstrumentIdentity
    capabilities: InstrumentCapabilities
    metadata: InstrumentMetadata


class InstrumentMarketSnapshotResponse(MarketDataQuality):
    instrument: InstrumentIdentity
    capabilities: InstrumentCapabilities
    quote: InstrumentQuoteResponse
    order_book: InstrumentOrderBookResponse
    capital_flow: InstrumentCapitalFlowResponse
    data_quality: MarketDataQuality
    as_of: datetime


class BatchQuoteRequest(MarketContract):
    codes: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(
        min_length=1, max_length=MAX_BATCH_QUOTES,
    )


class BatchQuoteItem(InstrumentQuoteResponse):
    instrument: InstrumentIdentity | None = None
    code: str


class BatchQuoteResponse(MarketContract):
    items: list[BatchQuoteItem]
    as_of: datetime


class InstrumentEvidenceItem(MarketContract):
    snapshot: InstrumentMarketSnapshotResponse
    bars: InstrumentBarsResponse


class InstrumentMarketEvidence(MarketContract):
    schema_version: Literal["v3-market-2.evidence.v1"] = "v3-market-2.evidence.v1"
    items: list[InstrumentEvidenceItem]
    as_of: datetime
