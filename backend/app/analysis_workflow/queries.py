"""Load user-scoped workflow audit graphs for read APIs."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..v2_models import AnalysisRun
from .models import AnalysisArtifact, AnalysisClaim, AnalysisNode, AnalysisNodeAttempt, AnalysisStage
from .resume import is_run_resumable, resume_from_checkpoint
from .schemas import (
    ArtifactDetail,
    ArtifactMetadata,
    AttemptSummary,
    ClaimSummary,
    NodeSummary,
    StageSummary,
    WorkflowRunSummary,
    WorkflowTree,
)
from .timeline import build_analysis_timeline


def _run_summary(run: AnalysisRun) -> WorkflowRunSummary:
    return WorkflowRunSummary(
        id=run.id,
        job_id=run.job_id,
        status=getattr(run, "status", None),
        workflow_version=getattr(run, "workflow_version", None),
        skill_version=getattr(run, "skill_version", None),
        analysis_mode=getattr(run, "analysis_mode", None),
        last_checkpoint=getattr(run, "last_checkpoint", None),
        resumable=is_run_resumable(run),
        failed_stage=getattr(run, "failed_stage", None),
        failed_node=getattr(run, "failed_node", None),
        error_code=getattr(run, "error_code", None),
        error_message=getattr(run, "error_message", None),
        started_at=getattr(run, "started_at", None),
        completed_at=getattr(run, "completed_at", None),
        created_at=run.created_at,
    )


def _attempt_summary(row: AnalysisNodeAttempt) -> AttemptSummary:
    return AttemptSummary(
        id=row.id,
        node_id=row.node_id,
        attempt_no=row.attempt_no,
        status=row.status,
        provider=row.provider,
        model=row.model,
        latency_ms=row.latency_ms,
        transport_retry_count=row.transport_retry_count or 0,
        structured_retry_count=row.structured_retry_count or 0,
        failure_class=getattr(row, "failure_class", None),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        request_id=row.request_id,
        error_code=row.error_code,
        error_message=row.error_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _node_summary(row: AnalysisNode, attempts: list[AnalysisNodeAttempt]) -> NodeSummary:
    return NodeSummary(
        id=row.id,
        stage_id=row.stage_id,
        node_key=row.node_key,
        node_type=row.node_type,
        agent_role=row.agent_role,
        status=row.status,
        criticality=row.criticality,
        attempt_count=row.attempt_count,
        retryable=bool(row.retryable),
        resumable=bool(row.resumable),
        error_code=row.error_code,
        error_message=row.error_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
        attempts=[_attempt_summary(item) for item in attempts],
    )


def load_workflow_tree(db: Session, run: AnalysisRun) -> WorkflowTree:
    stages = (
        db.query(AnalysisStage)
        .filter(AnalysisStage.analysis_run_id == run.id)
        .order_by(AnalysisStage.phase_order.asc(), AnalysisStage.id.asc())
        .all()
    )
    nodes = db.query(AnalysisNode).filter(AnalysisNode.analysis_run_id == run.id).order_by(AnalysisNode.id.asc()).all()
    attempts = db.query(AnalysisNodeAttempt).filter(AnalysisNodeAttempt.analysis_run_id == run.id).order_by(AnalysisNodeAttempt.attempt_no.asc()).all()
    attempts_by_node: dict[int, list[AnalysisNodeAttempt]] = {}
    for item in attempts:
        attempts_by_node.setdefault(item.node_id, []).append(item)
    nodes_by_stage: dict[int, list[AnalysisNode]] = {}
    for item in nodes:
        nodes_by_stage.setdefault(item.stage_id, []).append(item)
    return WorkflowTree(
        run=_run_summary(run),
        stages=[
            StageSummary(
                id=stage.id,
                phase_key=stage.phase_key,
                phase_order=stage.phase_order,
                display_name=stage.display_name,
                status=stage.status,
                criticality=stage.criticality,
                quality_grade=stage.quality_grade,
                error_code=stage.error_code,
                error_message=stage.error_message,
                started_at=stage.started_at,
                completed_at=stage.completed_at,
                nodes=[_node_summary(node, attempts_by_node.get(node.id, [])) for node in nodes_by_stage.get(stage.id, [])],
            )
            for stage in stages
        ],
    )


def load_artifact_metadata(db: Session, run_id: int) -> list[ArtifactMetadata]:
    rows = (
        db.query(AnalysisArtifact)
        .filter(AnalysisArtifact.analysis_run_id == run_id)
        .order_by(AnalysisArtifact.id.asc())
        .all()
    )
    return [
        ArtifactMetadata(
            id=row.id,
            artifact_type=row.artifact_type,
            artifact_key=row.artifact_key,
            sha256=row.sha256,
            content_size=row.content_size,
            redacted=bool(row.redacted),
            mime_type=row.mime_type,
            stage_id=row.stage_id,
            node_id=row.node_id,
            attempt_id=row.attempt_id,
            created_at=row.created_at,
        )
        for row in rows
    ]


def load_artifact_detail(db: Session, run_id: int, artifact_id: int) -> ArtifactDetail | None:
    row = (
        db.query(AnalysisArtifact)
        .filter(AnalysisArtifact.id == artifact_id, AnalysisArtifact.analysis_run_id == run_id)
        .first()
    )
    if row is None:
        return None
    return ArtifactDetail(
        id=row.id,
        artifact_type=row.artifact_type,
        artifact_key=row.artifact_key,
        sha256=row.sha256,
        content_size=row.content_size,
        redacted=bool(row.redacted),
        mime_type=row.mime_type,
        stage_id=row.stage_id,
        node_id=row.node_id,
        attempt_id=row.attempt_id,
        created_at=row.created_at,
        content_json=row.content_json,
        content_text=row.content_text,
    )


def load_claims(db: Session, run_id: int) -> list[ClaimSummary]:
    rows = db.query(AnalysisClaim).filter(AnalysisClaim.analysis_run_id == run_id).order_by(AnalysisClaim.id.asc()).all()
    return [
        ClaimSummary(
            id=row.id,
            claim_id=row.claim_id,
            debate_type=row.debate_type,
            speaker=row.speaker,
            stance=row.stance,
            statement=row.statement,
            evidence_refs=list(row.evidence_refs_json or []),
            confidence=row.confidence,
            status=row.status,
            target_claim_ids=list(row.target_claim_ids_json or []),
            created_at=row.created_at,
        )
        for row in rows
    ]


def load_timeline(db: Session, run_id: int) -> list[dict]:
    return build_analysis_timeline(db, run_id)


def load_resume_contract(db: Session, run: AnalysisRun) -> dict:
    return resume_from_checkpoint(run, db)
