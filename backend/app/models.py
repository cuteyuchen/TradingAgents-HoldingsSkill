"""SQLAlchemy ORM models. One-to-one mapping with the skill output contract.

A `Run` is one execution of the skill. Every other table hangs off `run_id`
except `benchmark_prices`, `watchlist`, and `health_log` which are global.
"""
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Run(Base):
    """Root record of one skill execution."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)  # skill run time
    checkpoint: Mapped[str | None] = mapped_column(String(16), index=True)  # 09:25/10:00/12:00/14:30
    holdings_source: Mapped[str | None] = mapped_column(String(32))  # screenshot/typed/history
    data_quality_grade: Mapped[str | None] = mapped_column(String(4))  # A-F

    # Parsed intent (Phase 0).
    intent: Mapped[dict | None] = mapped_column(JSON)  # {ticker, horizon, focus, risk_profile, objective}

    # Evidence pack free-form notes + code assumptions.
    evidence_pack: Mapped[dict | None] = mapped_column(JSON)  # {code_assumptions, missing_fields, ...}

    # Full transcript persisted for dashboard review and Phase-0 context.
    transcript: Mapped[str | None] = mapped_column(Text)
    sections: Mapped[dict | None] = mapped_column(JSON)
    screenshot: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    # Relationships.
    quality_gates: Mapped[list["QualityGate"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    holdings: Mapped[list["HoldingSnapshot"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    claims: Mapped[list["Claim"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    research_verdict: Mapped["ResearchVerdict | None"] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )
    trader_proposals: Mapped[list["TraderProposal"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    pm_final: Mapped["PortfolioManagerFinal | None"] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Archive(Base):
    """File-backed analysis archive uploaded after advice is shown to the user."""

    __tablename__ = "archives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    checkpoint: Mapped[str | None] = mapped_column(String(16), index=True)
    holdings_source: Mapped[str | None] = mapped_column(String(32))
    data_quality_grade: Mapped[str | None] = mapped_column(String(4))
    title: Mapped[str | None] = mapped_column(String(128))

    # File-backed archive payload. Files live under backend/data/artifacts/<id>/.
    meta: Mapped[dict | None] = mapped_column(JSON)
    holdings_json: Mapped[list | dict | None] = mapped_column(JSON)
    advice_filename: Mapped[str] = mapped_column(String(64), default="advice.md")
    holdings_filename: Mapped[str] = mapped_column(String(64), default="holdings.json")
    screenshot_filename: Mapped[str | None] = mapped_column(String(128))
    screenshot_mime_type: Mapped[str | None] = mapped_column(String(64))
    screenshot_original_name: Mapped[str | None] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class QualityGate(Base):
    """Phase 2 quality-gate row per analyst lens."""

    __tablename__ = "run_quality_gates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    analyst: Mapped[str] = mapped_column(String(64))  # 技术分析/情绪/...
    hard_check: Mapped[str | None] = mapped_column(String(8))  # pass/fail or ✓/✗
    llm_review: Mapped[str | None] = mapped_column(String(16))  # 通过/不通过
    grade: Mapped[str | None] = mapped_column(String(4))  # A-F
    gaps: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="quality_gates")


class HoldingSnapshot(Base):
    """One holding's state as of this run (Phase 1 data + computed alpha)."""

    __tablename__ = "holdings_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(64))
    qty: Mapped[float | None] = mapped_column(Float)
    available_qty: Mapped[float | None] = mapped_column(Float)
    cost: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)  # current/advice price
    market_value: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)
    pnl_amount: Mapped[float | None] = mapped_column(Float)
    data_quality: Mapped[str | None] = mapped_column(String(4))

    # Alpha vs CSI 300, computed on upload by comparing to the previous same-code snapshot.
    raw_return: Mapped[float | None] = mapped_column(Float)
    benchmark_return: Mapped[float | None] = mapped_column(Float)
    alpha: Mapped[float | None] = mapped_column(Float)

    run: Mapped[Run] = relationship(back_populates="holdings")
    indicators: Mapped["HoldingIndicator | None"] = relationship(
        back_populates="snapshot", uselist=False, cascade="all, delete-orphan"
    )


class HoldingIndicator(Base):
    """Technical / VPA / fund-flow JSON blob for a holding snapshot."""

    __tablename__ = "holding_indicators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("holdings_snapshots.id", ondelete="CASCADE"), index=True)
    quote: Mapped[dict | None] = mapped_column(JSON)
    technicals: Mapped[dict | None] = mapped_column(JSON)
    vpa: Mapped[dict | None] = mapped_column(JSON)
    fund_flow: Mapped[dict | None] = mapped_column(JSON)
    red_flags: Mapped[list | None] = mapped_column(JSON)

    snapshot: Mapped[HoldingSnapshot] = relationship(back_populates="indicators")


class Claim(Base):
    """A claim-driven debate point (INV-* investment or RISK-* risk)."""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    claim_id: Mapped[str] = mapped_column(String(16))  # INV-1 / RISK-1
    speaker: Mapped[str] = mapped_column(String(16))  # bull/bear/aggressive/conservative/neutral
    stance: Mapped[str | None] = mapped_column(String(16))  # bullish/bearish/risk_accept/...
    claim: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list | None] = mapped_column(JSON)  # max 3
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/addressed/resolved/unresolved
    target_claim_ids: Mapped[list | None] = mapped_column(JSON)
    round: Mapped[int | None] = mapped_column(Integer)  # which debate round

    run: Mapped[Run] = relationship(back_populates="claims")


class ResearchVerdict(Base):
    """Phase 4 research-manager verdict (one per run)."""

    __tablename__ = "research_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), unique=True, index=True)
    rating: Mapped[str | None] = mapped_column(String(16))  # Buy/Overweight/Hold/Underweight/Sell
    winner: Mapped[str | None] = mapped_column(String(16))  # bull/bear
    rationale: Mapped[str | None] = mapped_column(Text)
    unresolved_handling: Mapped[dict | None] = mapped_column(JSON)
    strategy: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(String(8))  # 高/中/低

    run: Mapped[Run] = relationship(back_populates="research_verdict")


class TraderProposal(Base):
    """Phase 4 trader proposal per holding."""

    __tablename__ = "trader_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str | None] = mapped_column(String(16))  # Buy/Hold/Sell
    trigger_price: Mapped[float | None] = mapped_column(Float)
    qty: Mapped[str | None] = mapped_column(String(32))  # number or percentage string
    take_profit: Mapped[str | None] = mapped_column(String(64))
    stop_loss: Mapped[str | None] = mapped_column(String(64))
    invalidation: Mapped[str | None] = mapped_column(Text)
    checkpoint_rule: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="trader_proposals")
    revision: Mapped["RiskRevision | None"] = relationship(
        back_populates="proposal", uselist=False, cascade="all, delete-orphan"
    )


class RiskRevision(Base):
    """Phase 4 risk-manager revision loop (pass/revise/reject + constraints)."""

    __tablename__ = "risk_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("trader_proposals.id", ondelete="CASCADE"), unique=True, index=True)
    verdict: Mapped[str] = mapped_column(String(8))  # pass/revise/reject
    hard_constraints: Mapped[list | None] = mapped_column(JSON)
    soft_constraints: Mapped[list | None] = mapped_column(JSON)
    execution_preconditions: Mapped[list | None] = mapped_column(JSON)
    de_risk_triggers: Mapped[list | None] = mapped_column(JSON)
    revision_reason: Mapped[str | None] = mapped_column(Text)
    revised_proposal: Mapped[dict | None] = mapped_column(JSON)  # the corrected proposal

    proposal: Mapped[TraderProposal] = relationship(back_populates="revision")


class PortfolioManagerFinal(Base):
    """Phase 5 portfolio-manager final decision (one per run)."""

    __tablename__ = "pm_finals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), unique=True, index=True)
    rating: Mapped[str | None] = mapped_column(String(16))
    cash_target: Mapped[str | None] = mapped_column(String(32))
    actions: Mapped[list | None] = mapped_column(JSON)
    priority_notes: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="pm_final")


class Candidate(Base):
    """Buy/rotation candidate from the run."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str | None] = mapped_column(String(16))  # ETF/stock/watch
    score: Mapped[float | None] = mapped_column(Float)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON)
    entry_trigger: Mapped[str | None] = mapped_column(Text)
    initial_size: Mapped[str | None] = mapped_column(String(32))
    take_profit_1: Mapped[str | None] = mapped_column(String(64))
    take_profit_2: Mapped[str | None] = mapped_column(String(64))
    stop_loss: Mapped[str | None] = mapped_column(String(64))
    invalidation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="待触发")  # 待触发/已命中/已取消

    run: Mapped[Run] = relationship(back_populates="candidates")


class BenchmarkPrice(Base):
    """CSI 300 daily close, fetched by the benchmark service."""

    __tablename__ = "benchmark_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM-DD
    close: Mapped[float] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)


class Watchlist(Base):
    """Self-selected symbols for scheduled tracking (optimization #8)."""

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(64))
    cadence: Mapped[str | None] = mapped_column(String(64))  # e.g. 09:25/10:00/12:00/14:30
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class HealthLog(Base):
    """Per-checkpoint failure counter for degradation (optimization #8/#15)."""

    __tablename__ = "health_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str | None] = mapped_column(String(16), index=True)
    checkpoint: Mapped[str] = mapped_column(String(16), index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)
