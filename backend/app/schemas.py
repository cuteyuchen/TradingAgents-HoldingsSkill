"""Pydantic schemas mirroring the skill output contract (1:1 with models.py).

The upload payload (RunUpload) is what the skill POSTs at Phase 6.
All optional so a partial/degraded run still uploads cleanly.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---- Nested indicator payloads (HoldingIndicator JSON columns) ----

class QuoteSchema(BaseModel):
    price: float | None = None
    pct_change: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    turnover: float | None = None
    volume_ratio: float | None = None


class TechnicalsSchema(BaseModel):
    rsi_14: float | None = None
    macd_signal: str | None = None
    ma_5: float | None = None
    ma_20: float | None = None
    bollinger_position: str | None = None


class VPASchema(BaseModel):
    obv_trend: str | None = None
    obv_divergence: bool | None = None
    volume_ratio: float | None = None
    bar_type: str | None = None
    bearish_divergence: bool | None = None
    selling_climax: bool | None = None
    vwma_20: float | None = None


class FundFlowSchema(BaseModel):
    super_large_net: float | None = None
    large_net: float | None = None
    medium_net: float | None = None
    small_net: float | None = None
    northbound_net: float | None = None


class HoldingIndicatorsSchema(BaseModel):
    quote: QuoteSchema | None = None
    technicals: TechnicalsSchema | None = None
    vpa: VPASchema | None = None
    fund_flow: FundFlowSchema | None = None
    red_flags: list[str] | None = None


# ---- Top-level transcript sections ----

class IntentSchema(BaseModel):
    tickers: list[str] | None = None
    horizon: str | None = None          # short/medium/long
    focus: list[str] | None = None
    risk_profile: str | None = None     # 激进/稳健/保守
    objective: str | None = None


class HoldingUpload(BaseModel):
    code: str
    name: str | None = None
    qty: float | None = None
    available_qty: float | None = None
    cost: float | None = None
    price: float | None = None
    market_value: float | None = None
    pnl: float | None = None
    data_quality: str | None = None
    indicators: HoldingIndicatorsSchema | None = None


class ClaimUpload(BaseModel):
    claim_id: str
    speaker: str
    stance: str | None = None
    claim: str
    evidence: list[str] | None = None
    confidence: float | None = None
    status: str = "open"
    target_claim_ids: list[str] | None = None
    round: int | None = None


class QualityGateUpload(BaseModel):
    analyst: str
    hard_check: str | None = None
    llm_review: str | None = None
    grade: str | None = None
    gaps: str | None = None


class RiskRevisionUpload(BaseModel):
    verdict: str                         # pass/revise/reject
    hard_constraints: list[str] | None = None
    soft_constraints: list[str] | None = None
    execution_preconditions: list[str] | None = None
    de_risk_triggers: list[str] | None = None
    revision_reason: str | None = None
    revised_proposal: dict | None = None


class TraderProposalUpload(BaseModel):
    code: str
    action: str | None = None
    trigger_price: float | None = None
    qty: str | None = None
    take_profit: str | None = None
    stop_loss: str | None = None
    invalidation: str | None = None
    checkpoint_rule: str | None = None
    revision: RiskRevisionUpload | None = None


class ResearchVerdictUpload(BaseModel):
    rating: str | None = None
    winner: str | None = None
    rationale: str | None = None
    unresolved_handling: dict | None = None
    strategy: str | None = None
    confidence: str | None = None


class PMFinalUpload(BaseModel):
    rating: str | None = None
    cash_target: str | None = None
    actions: list[dict] | None = None
    priority_notes: str | None = None


class CandidateUpload(BaseModel):
    code: str
    name: str | None = None
    type: str | None = None              # ETF/stock/watch
    score: float | None = None
    score_breakdown: dict | None = None
    entry_trigger: str | None = None
    initial_size: str | None = None
    take_profit_1: str | None = None
    take_profit_2: str | None = None
    stop_loss: str | None = None
    invalidation: str | None = None
    status: str = "待触发"


class RunUpload(BaseModel):
    """The full Phase-6 upload payload."""
    timestamp: datetime
    checkpoint: str | None = None
    holdings_source: str | None = None
    data_quality_grade: str | None = None
    intent: IntentSchema | None = None
    evidence_pack: dict | None = None
    transcript: str | None = None
    sections: dict | None = None
    quality_gates: list[QualityGateUpload] = Field(default_factory=list)
    holdings: list[HoldingUpload] = Field(default_factory=list)
    claims: list[ClaimUpload] = Field(default_factory=list)
    research_verdict: ResearchVerdictUpload | None = None
    trader_proposals: list[TraderProposalUpload] = Field(default_factory=list)
    pm_final: PMFinalUpload | None = None
    candidates: list[CandidateUpload] = Field(default_factory=list)


# ---- Response schemas ----

class RunCreated(BaseModel):
    run_id: int
    alphas: dict[str, dict] = {}    # {code: {raw_return, benchmark_return, alpha}}


class RunSummary(BaseModel):
    id: int
    timestamp: datetime
    checkpoint: str | None
    data_quality_grade: str | None
    pm_rating: str | None
    holdings_count: int
    candidates_count: int

    model_config = ConfigDict(from_attributes=True)


class WatchlistItem(BaseModel):
    code: str
    name: str | None = None
    cadence: str | None = None
    enabled: bool = True

    model_config = ConfigDict(from_attributes=True)


class HealthStatus(BaseModel):
    code: str | None = None
    checkpoint: str
    consecutive_failures: int
    degraded: bool
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None
    note: str | None = None

    model_config = ConfigDict(from_attributes=True)
