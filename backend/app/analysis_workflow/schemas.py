"""Read-only Pydantic schemas for workflow audit APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowRunSummary(BaseModel):
    id: int
    job_id: int
    status: str | None = None
    workflow_version: str | None = None
    skill_version: str | None = None
    analysis_mode: str | None = None
    last_checkpoint: str | None = None
    resumable: bool = False
    failed_stage: str | None = None
    failed_node: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class ArtifactMetadata(BaseModel):
    id: int
    artifact_type: str
    artifact_key: str
    sha256: str
    content_size: int
    redacted: bool
    mime_type: str
    stage_id: int | None = None
    node_id: int | None = None
    attempt_id: int | None = None
    created_at: datetime


class ArtifactDetail(ArtifactMetadata):
    content_json: dict | list | None = None
    content_text: str | None = None


class AttemptSummary(BaseModel):
    id: int
    node_id: int
    attempt_no: int
    status: str
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    transport_retry_count: int = 0
    structured_retry_count: int = 0
    failure_class: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class NodeSummary(BaseModel):
    id: int
    stage_id: int
    node_key: str
    node_type: str
    agent_role: str
    status: str
    criticality: str
    attempt_count: int
    retryable: bool
    resumable: bool
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempts: list[AttemptSummary] = Field(default_factory=list)


class StageSummary(BaseModel):
    id: int
    phase_key: str
    phase_order: int
    display_name: str
    status: str
    criticality: str
    quality_grade: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    nodes: list[NodeSummary] = Field(default_factory=list)


class ClaimSummary(BaseModel):
    id: int
    claim_id: str
    debate_type: str
    speaker: str | None = None
    stance: str | None = None
    statement: str
    evidence_refs: list[Any] = Field(default_factory=list)
    confidence: float | None = None
    status: str
    target_claim_ids: list[Any] = Field(default_factory=list)
    created_at: datetime


class WorkflowTree(BaseModel):
    run: WorkflowRunSummary
    stages: list[StageSummary] = Field(default_factory=list)
