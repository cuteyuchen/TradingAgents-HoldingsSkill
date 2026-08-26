"""Auditable persistence for Phase F Candidate Engine runs."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class CandidateRun(Base):
    __tablename__ = "candidate_runs"
    __table_args__ = (
        UniqueConstraint("calculation_key", name="uq_candidate_runs_calculation_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    calculation_key: Mapped[str] = mapped_column(String(255), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    portfolio_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="SET NULL"), index=True)
    market_score_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    market_snapshot_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="COMPLETED", index=True)
    mode: Mapped[str] = mapped_column(String(16), default="standard", index=True)
    universe_count: Mapped[int] = mapped_column(Integer, default=0)
    eligible_count: Mapped[int] = mapped_column(Integer, default=0)
    prefilter_count: Mapped[int] = mapped_column(Integer, default=0)
    watchlist_count: Mapped[int] = mapped_column(Integer, default=0)
    ready_count: Mapped[int] = mapped_column(Integer, default=0)
    action_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_status: Mapped[str] = mapped_column(String(24), default="MISSING", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    calculation_version: Mapped[str] = mapped_column(String(64), default="candidate-engine-v1")
    stock_score_version: Mapped[str] = mapped_column(String(64), default="stock-opportunity-v1")
    etf_score_version: Mapped[str] = mapped_column(String(64), default="etf-opportunity-v1")
    entry_score_version: Mapped[str] = mapped_column(String(64), default="entry-score-v1")
    portfolio_fit_version: Mapped[str] = mapped_column(String(64), default="portfolio-fit-v1")
    decision_edge_version: Mapped[str] = mapped_column(String(64), default="decision-edge-v1")
    exclusion_counts_json: Mapped[dict | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    scores: Mapped[list["CandidateScore"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="CandidateScore.rank"
    )


class CandidateScore(Base):
    __tablename__ = "candidate_scores"
    __table_args__ = (
        UniqueConstraint("candidate_run_id", "code", name="uq_candidate_scores_run_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_run_id: Mapped[int] = mapped_column(ForeignKey("candidate_runs.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    security_type: Mapped[str] = mapped_column(String(24), index=True)
    etf_category: Mapped[str | None] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(16), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score: Mapped[float | None] = mapped_column(Float)
    opportunity_score: Mapped[float | None] = mapped_column(Float)
    entry_score: Mapped[float | None] = mapped_column(Float)
    portfolio_fit_score: Mapped[float | None] = mapped_column(Float)
    action_score: Mapped[float | None] = mapped_column(Float)
    edge_vs_no_action: Mapped[float | None] = mapped_column(Float)
    edge_vs_current_holdings: Mapped[float | None] = mapped_column(Float)
    decision_edge: Mapped[float | None] = mapped_column(Float)
    risk_reward_ratio: Mapped[float | None] = mapped_column(Float)
    data_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(24), default="INSUFFICIENT", index=True)
    probe_weight: Mapped[float | None] = mapped_column(Float)
    funding_mode: Mapped[str | None] = mapped_column(String(32), index=True)
    components_json: Mapped[dict | None] = mapped_column(JSON)
    portfolio_fit_json: Mapped[dict | None] = mapped_column(JSON)
    entry_json: Mapped[dict | None] = mapped_column(JSON)
    comparison_json: Mapped[dict | None] = mapped_column(JSON)
    reason_codes_json: Mapped[list | None] = mapped_column(JSON)
    risk_flags_json: Mapped[list | None] = mapped_column(JSON)
    positive_drivers_json: Mapped[list | None] = mapped_column(JSON)
    negative_drivers_json: Mapped[list | None] = mapped_column(JSON)
    blocking_reasons_json: Mapped[list | None] = mapped_column(JSON)
    lineage_json: Mapped[dict | None] = mapped_column(JSON)
    lifecycle: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    run: Mapped[CandidateRun] = relationship(back_populates="scores")


__all__ = ["CandidateRun", "CandidateScore"]
