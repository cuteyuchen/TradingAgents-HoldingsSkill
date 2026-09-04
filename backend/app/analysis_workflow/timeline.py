"""Build a stable audit timeline from persisted workflow rows."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..v2_models import AnalysisRun
from .models import AnalysisArtifact, AnalysisClaim, AnalysisNode, AnalysisNodeAttempt, AnalysisStage

_TYPE_ORDER = {
    "run_started": 0,
    "stage_started": 10,
    "node_started": 20,
    "attempt_started": 30,
    "artifact_recorded": 40,
    "claim_recorded": 45,
    "attempt_completed": 50,
    "attempt_failed": 51,
    "node_completed": 60,
    "node_failed": 61,
    "stage_completed": 70,
    "stage_failed": 71,
    "checkpoint": 80,
    "run_completed": 90,
    "run_blocked": 91,
    "run_failed": 92,
    "run_cancelled": 93,
    "run_interrupted": 94,
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _event(timestamp: datetime | None, event_type: str, **fields: Any) -> dict[str, Any] | None:
    if timestamp is None:
        return None
    payload = {"timestamp": timestamp.isoformat(), "type": event_type}
    payload.update({key: value for key, value in fields.items() if value is not None})
    return payload


def build_analysis_timeline(db: Session, run_id: int) -> list[dict[str, Any]]:
    run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if run is None:
        return []
    events: list[dict[str, Any]] = []
    started = _event(getattr(run, "started_at", None) or run.created_at, "run_started", run=run.id)
    if started:
        events.append(started)

    stages = db.query(AnalysisStage).filter(AnalysisStage.analysis_run_id == run_id).all()
    stage_key = {row.id: row.phase_key for row in stages}
    for stage in stages:
        item = _event(stage.started_at, "stage_started", stage=stage.phase_key, status=stage.status)
        if item:
            events.append(item)
        done_type = "stage_completed" if stage.status == "completed" else "stage_failed" if stage.status in {"failed", "blocked", "cancelled"} else None
        if done_type:
            item = _event(stage.completed_at, done_type, stage=stage.phase_key, status=stage.status, error_code=stage.error_code)
            if item:
                events.append(item)

    nodes = db.query(AnalysisNode).filter(AnalysisNode.analysis_run_id == run_id).all()
    node_key = {row.id: row.node_key for row in nodes}
    for node in nodes:
        item = _event(node.started_at, "node_started", stage=stage_key.get(node.stage_id), node=node.node_key, status=node.status)
        if item:
            events.append(item)
        done_type = "node_completed" if node.status == "completed" else "node_failed" if node.status in {"failed", "blocked", "cancelled"} else None
        if done_type:
            item = _event(node.completed_at, done_type, stage=stage_key.get(node.stage_id), node=node.node_key, status=node.status, error_code=node.error_code)
            if item:
                events.append(item)

    attempts = db.query(AnalysisNodeAttempt).filter(AnalysisNodeAttempt.analysis_run_id == run_id).all()
    for attempt in attempts:
        item = _event(
            attempt.started_at,
            "attempt_started",
            stage=stage_key.get(attempt.stage_id),
            node=node_key.get(attempt.node_id),
            attempt=attempt.attempt_no,
        )
        if item:
            events.append(item)
        done_type = "attempt_completed" if attempt.status == "completed" else "attempt_failed" if attempt.status == "failed" else None
        if done_type:
            item = _event(
                attempt.completed_at,
                done_type,
                stage=stage_key.get(attempt.stage_id),
                node=node_key.get(attempt.node_id),
                attempt=attempt.attempt_no,
                error_code=attempt.error_code,
            )
            if item:
                events.append(item)

    artifacts = db.query(AnalysisArtifact).filter(AnalysisArtifact.analysis_run_id == run_id).all()
    for artifact in artifacts:
        event_type = "checkpoint" if artifact.artifact_type == "CHECKPOINT" else "artifact_recorded"
        item = _event(
            artifact.created_at,
            event_type,
            stage=stage_key.get(artifact.stage_id) if artifact.stage_id else None,
            node=node_key.get(artifact.node_id) if artifact.node_id else None,
            attempt=None,
            artifact_type=artifact.artifact_type,
            artifact_key=artifact.artifact_key,
            sha256=artifact.sha256,
        )
        if item:
            events.append(item)

    claims = db.query(AnalysisClaim).filter(AnalysisClaim.analysis_run_id == run_id).all()
    for claim in claims:
        item = _event(
            claim.created_at,
            "claim_recorded",
            stage=stage_key.get(claim.stage_id) if claim.stage_id else None,
            node=node_key.get(claim.node_id) if claim.node_id else None,
            claim_id=claim.claim_id,
            status=claim.status,
        )
        if item:
            events.append(item)

    run_done = {
        "completed": "run_completed",
        "blocked": "run_blocked",
        "failed": "run_failed",
        "cancelled": "run_cancelled",
        "interrupted": "run_interrupted",
    }.get(str(getattr(run, "status", "") or ""))
    if run_done:
        item = _event(getattr(run, "completed_at", None), run_done, run=run.id, status=run.status)
        if item:
            events.append(item)

    events.sort(key=lambda item: (item["timestamp"], _TYPE_ORDER.get(item["type"], 50), item.get("node") or "", item.get("attempt") or 0))
    return events
