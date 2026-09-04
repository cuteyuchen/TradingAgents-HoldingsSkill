"""Persistent analysis workflow/audit tables for V3-CORE-1."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..clock import utc_now
from ..database import Base


def utcnow() -> datetime:
    return utc_now()


class AnalysisStage(Base):
    __tablename__ = "analysis_stages"
    __table_args__ = (UniqueConstraint("analysis_run_id", "phase_key", name="uq_analysis_stages_run_phase"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    phase_key: Mapped[str] = mapped_column(String(64), index=True)
    phase_order: Mapped[int] = mapped_column(Integer, default=0)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    criticality: Mapped[str] = mapped_column(String(16), default="important")
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    quality_grade: Mapped[str | None] = mapped_column(String(8))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    nodes: Mapped[list["AnalysisNode"]] = relationship(back_populates="stage", cascade="all, delete-orphan")


class AnalysisNode(Base):
    __tablename__ = "analysis_nodes"
    __table_args__ = (UniqueConstraint("analysis_run_id", "node_key", name="uq_analysis_nodes_run_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("analysis_stages.id", ondelete="CASCADE"), index=True)
    node_key: Mapped[str] = mapped_column(String(64), index=True)
    node_type: Mapped[str] = mapped_column(String(32), default="legacy")
    agent_role: Mapped[str] = mapped_column(String(32), default="system")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    criticality: Mapped[str] = mapped_column(String(16), default="important")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    input_artifact_id: Mapped[int | None] = mapped_column(Integer, index=True)
    output_artifact_id: Mapped[int | None] = mapped_column(Integer, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    resumable: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    stage: Mapped[AnalysisStage] = relationship(back_populates="nodes")
    attempts: Mapped[list["AnalysisNodeAttempt"]] = relationship(back_populates="node", cascade="all, delete-orphan")


class AnalysisNodeAttempt(Base):
    __tablename__ = "analysis_node_attempts"
    __table_args__ = (UniqueConstraint("node_id", "attempt_no", name="uq_analysis_node_attempts_node_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("analysis_stages.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("analysis_nodes.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    model_profile_id: Mapped[int | None] = mapped_column(Integer, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    transport_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    structured_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_output_artifact_id: Mapped[int | None] = mapped_column(Integer, index=True)
    structured_output_artifact_id: Mapped[int | None] = mapped_column(Integer, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    node: Mapped[AnalysisNode] = relationship(back_populates="attempts")


class AnalysisArtifact(Base):
    __tablename__ = "analysis_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_stages.id", ondelete="SET NULL"), index=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_nodes.id", ondelete="SET NULL"), index=True)
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_node_attempts.id", ondelete="SET NULL"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(32), index=True)
    artifact_key: Mapped[str] = mapped_column(String(128), index=True)
    content_json: Mapped[dict | list | None] = mapped_column(JSON)
    content_text: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_size: Mapped[int] = mapped_column(Integer, default=0)
    redacted: Mapped[bool] = mapped_column(Boolean, default=True)
    content_encoding: Mapped[str] = mapped_column(String(32), default="utf-8")
    mime_type: Mapped[str] = mapped_column(String(64), default="application/json")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AnalysisClaim(Base):
    __tablename__ = "analysis_claims"
    __table_args__ = (UniqueConstraint("analysis_run_id", "claim_id", name="uq_analysis_claims_run_claim"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_stages.id", ondelete="SET NULL"), index=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_nodes.id", ondelete="SET NULL"), index=True)
    claim_id: Mapped[str] = mapped_column(String(32), index=True)
    debate_type: Mapped[str] = mapped_column(String(32), default="investment", index=True)
    speaker: Mapped[str | None] = mapped_column(String(32))
    stance: Mapped[str | None] = mapped_column(String(32))
    statement: Mapped[str] = mapped_column(Text, default="")
    evidence_refs_json: Mapped[list | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    parent_claim_id: Mapped[str | None] = mapped_column(String(32))
    target_claim_ids_json: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
