"""Checkpoint / resume contract used by NodeExecutor and job retry."""
from __future__ import annotations

from typing import Any

from ..v2_models import AnalysisRun
from .constants import CheckpointName, NodeStatus, RunStatus
from .failures import ResumeRejected
from .hashing import sha256_content
from .models import AnalysisArtifact, AnalysisNode
from .serializers import redact_payload


RESUMABLE_STATUSES = {RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.CANCELLED}


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
    """Return the skip plan CORE-2 uses to resume a failed/interrupted run."""

    checkpoint = str(getattr(run, "last_checkpoint", "") or "")
    completed_nodes: list[str] = []
    payload: dict[str, Any] = {}
    if db is not None and getattr(run, "id", None) is not None:
        completed_nodes = [
            row.node_key
            for row in db.query(AnalysisNode)
            .filter(AnalysisNode.analysis_run_id == run.id, AnalysisNode.status.in_(list(NodeStatus.SUCCESS)))
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
        "executor": "NodeExecutor",
        "note": "Skip completed nodes, keep historical attempts, and continue the failed node with a new Attempt.",
    }


def hash_input(content: Any) -> str:
    return sha256_content(redact_payload(content))


def validate_resume_inputs(stored_hashes: dict[str, Any] | None, current_hashes: dict[str, str]) -> None:
    """Refuse resume when a previously recorded critical input hash changed."""

    mismatches: dict[str, dict[str, str]] = {}
    for key, current in current_hashes.items():
        previous = (stored_hashes or {}).get(key)
        if not previous:
            continue
        if str(previous) != str(current):
            mismatches[key] = {"stored": str(previous), "current": str(current)}
    if mismatches:
        raise ResumeRejected("resume_input_hash_mismatch", mismatches=mismatches)


def should_skip_node(node_key: str, completed_nodes: list[str], *, force_restart: bool = False) -> bool:
    if force_restart:
        return False
    return node_key in set(completed_nodes or [])
