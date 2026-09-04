"""Checkpoint / resume data contract. CORE-1 does not execute resume."""
from __future__ import annotations

from typing import Any

from ..v2_models import AnalysisRun
from .constants import CheckpointName, RunStatus
from .models import AnalysisArtifact, AnalysisNode


RESUMABLE_STATUSES = {RunStatus.FAILED, RunStatus.INTERRUPTED}


def is_run_resumable(run: AnalysisRun) -> bool:
    """Return whether CORE-2 may legally resume this run from last_checkpoint."""

    if run is None:
        return False
    if str(getattr(run, "status", "") or "") not in RESUMABLE_STATUSES:
        return False
    if not bool(getattr(run, "resumable", False)):
        return False
    checkpoint = str(getattr(run, "last_checkpoint", "") or "")
    if not checkpoint or checkpoint == CheckpointName.FINALIZED:
        return False
    return True


def resume_from_checkpoint(run: AnalysisRun, db=None) -> dict[str, Any]:
    """Describe how CORE-2 should resume. This does not restart the runner."""

    checkpoint = str(getattr(run, "last_checkpoint", "") or "")
    completed_nodes: list[str] = []
    payload: dict[str, Any] = {}
    if db is not None and getattr(run, "id", None) is not None:
        completed_nodes = [
            row.node_key
            for row in db.query(AnalysisNode)
            .filter(AnalysisNode.analysis_run_id == run.id, AnalysisNode.status == "completed")
            .order_by(AnalysisNode.id.asc())
            .all()
        ]
        artifact = (
            db.query(AnalysisArtifact)
            .filter(
                AnalysisArtifact.analysis_run_id == run.id,
                AnalysisArtifact.artifact_type == "CHECKPOINT",
                AnalysisArtifact.artifact_key == f"checkpoint.{checkpoint}",
            )
            .order_by(AnalysisArtifact.id.desc())
            .first()
        )
        if artifact is not None and isinstance(artifact.content_json, dict):
            payload = dict(artifact.content_json)
    return {
        "run_id": getattr(run, "id", None),
        "checkpoint": checkpoint or None,
        "resumable": is_run_resumable(run),
        "status": getattr(run, "status", None),
        "failed_stage": getattr(run, "failed_stage", None),
        "failed_node": getattr(run, "failed_node", None),
        "completed_nodes": payload.get("completed_nodes") or completed_nodes,
        "input_hashes": payload.get("input_hashes") or {},
        "output_hashes": payload.get("output_hashes") or {},
        "executor": None,
        "note": "V3-CORE-1 defines the resume contract only; CORE-2 will attach a node executor.",
    }
