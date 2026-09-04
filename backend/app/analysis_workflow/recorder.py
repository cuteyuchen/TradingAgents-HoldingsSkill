"""Incremental workflow audit persistence used by the legacy analysis runner."""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..clock import utc_now
from ..system.logging import redact_text
from ..v2_models import AnalysisJob, AnalysisRun
from .artifacts import build_artifact
from .constants import (
    WORKFLOW_VERSION,
    ArtifactType,
    AttemptStatus,
    CheckpointName,
    ClaimStatus,
    DebateType,
    NodeStatus,
    RunStatus,
    StageStatus,
    node_spec,
    phase_spec,
)
from .hashing import sha256_content
from .models import AnalysisArtifact, AnalysisClaim, AnalysisNode, AnalysisNodeAttempt, AnalysisStage
from .serializers import redact_payload


def _now():
    value = utc_now()
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _as_unaware(value):
    if value is None:
        return None
    return value.replace(tzinfo=None) if getattr(value, "tzinfo", None) is not None else value


def _error_code(exc: BaseException) -> str:
    return str(getattr(exc, "code", None) or type(exc).__name__)[:64]


def _error_message(exc: BaseException) -> str:
    return redact_text(str(exc)[:3000])


def _claim_status(value: Any, default: str = ClaimStatus.OPEN) -> str:
    status = str(value or default).lower()
    allowed = {
        ClaimStatus.OPEN,
        ClaimStatus.ADDRESSED,
        ClaimStatus.RESOLVED,
        ClaimStatus.UNRESOLVED,
        ClaimStatus.ACCEPTED,
        ClaimStatus.REJECTED,
        ClaimStatus.PARTIALLY_ACCEPTED,
    }
    return status if status in allowed else default


class WorkflowAuditRecorder:
    """Commit-on-write audit adapter. Rollback of later work cannot erase it."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.run_id: int | None = None
        self.stage_id: int | None = None
        self.node_id: int | None = None
        self.attempt_id: int | None = None
        self.last_artifact_id: int | None = None
        self._completed_nodes: list[str] = []
        self._input_hashes: dict[str, str] = {}
        self._output_hashes: dict[str, str] = {}

    def _commit(self) -> None:
        self.db.commit()

    def _run(self) -> AnalysisRun:
        if self.run_id is None:
            raise RuntimeError("workflow_run_not_started")
        run = self.db.query(AnalysisRun).filter(AnalysisRun.id == self.run_id).first()
        if run is None:
            raise RuntimeError("workflow_run_missing")
        return run

    def _stage(self) -> AnalysisStage | None:
        if self.stage_id is None:
            return None
        return self.db.query(AnalysisStage).filter(AnalysisStage.id == self.stage_id).first()

    def _node(self) -> AnalysisNode | None:
        if self.node_id is None:
            return None
        return self.db.query(AnalysisNode).filter(AnalysisNode.id == self.node_id).first()

    def _attempt(self) -> AnalysisNodeAttempt | None:
        if self.attempt_id is None:
            return None
        return self.db.query(AnalysisNodeAttempt).filter(AnalysisNodeAttempt.id == self.attempt_id).first()

    def _purge_children(self, run_id: int) -> None:
        self.db.query(AnalysisClaim).filter(AnalysisClaim.analysis_run_id == run_id).delete(synchronize_session=False)
        self.db.query(AnalysisArtifact).filter(AnalysisArtifact.analysis_run_id == run_id).delete(synchronize_session=False)
        self.db.query(AnalysisNodeAttempt).filter(AnalysisNodeAttempt.analysis_run_id == run_id).delete(synchronize_session=False)
        self.db.query(AnalysisNode).filter(AnalysisNode.analysis_run_id == run_id).delete(synchronize_session=False)
        self.db.query(AnalysisStage).filter(AnalysisStage.analysis_run_id == run_id).delete(synchronize_session=False)

    def start_run(
        self,
        job: AnalysisJob,
        *,
        analysis_mode: str,
        skill_version: str | None = None,
        parameter_lineage: dict[str, Any] | None = None,
        model_profile_id: int | None = None,
        market_snapshot_at=None,
    ) -> AnalysisRun:
        lineage = parameter_lineage or {}
        existing = self.db.query(AnalysisRun).filter(AnalysisRun.job_id == job.id).first()
        now = _now()
        if existing is not None:
            self._purge_children(existing.id)
            existing.status = RunStatus.RUNNING
            existing.started_at = now
            existing.completed_at = None
            existing.interrupted_at = None
            existing.last_checkpoint = None
            existing.resumable = False
            existing.workflow_version = WORKFLOW_VERSION
            existing.skill_version = skill_version
            existing.analysis_mode = analysis_mode
            existing.market_snapshot_at = market_snapshot_at
            existing.failed_stage = None
            existing.failed_node = None
            existing.error_code = None
            existing.error_message = None
            existing.last_artifact_id = None
            existing.summary = None
            existing.final_rating = None
            existing.cash_target = None
            existing.confidence = None
            existing.data_quality_grade = None
            existing.structured_result_json = {}
            existing.markdown_text = ""
            existing.model_profile_id = model_profile_id
            if lineage:
                existing.parameter_set_version_id = lineage.get("parameter_set_version_id")
                existing.parameter_set_version = lineage.get("parameter_set_version")
                existing.parameter_set_hash = lineage.get("parameter_set_hash")
                existing.governance_lineage_json = lineage.get("governance_lineage_json")
            run = existing
        else:
            run = AnalysisRun(
                job_id=job.id,
                user_id=job.user_id,
                portfolio_snapshot_id=job.snapshot_id,
                model_profile_id=model_profile_id,
                markdown_text="",
                structured_result_json={},
                status=RunStatus.RUNNING,
                started_at=now,
                workflow_version=WORKFLOW_VERSION,
                skill_version=skill_version,
                analysis_mode=analysis_mode,
                market_snapshot_at=market_snapshot_at,
                resumable=False,
                parameter_set_version_id=lineage.get("parameter_set_version_id"),
                parameter_set_version=lineage.get("parameter_set_version"),
                parameter_set_hash=lineage.get("parameter_set_hash"),
                governance_lineage_json=lineage.get("governance_lineage_json"),
            )
            self.db.add(run)
        self._commit()
        self.db.refresh(run)
        self.run_id = run.id
        self.stage_id = None
        self.node_id = None
        self.attempt_id = None
        self.last_artifact_id = None
        self._completed_nodes = []
        self._input_hashes = {}
        self._output_hashes = {}
        return run

    def start_stage(self, phase_key: str, *, metadata: dict[str, Any] | None = None) -> AnalysisStage:
        spec = phase_spec(phase_key)
        stage = AnalysisStage(
            analysis_run_id=self._run().id,
            phase_key=spec.phase_key,
            phase_order=spec.phase_order,
            display_name=spec.display_name,
            status=StageStatus.RUNNING,
            criticality=spec.criticality,
            started_at=_now(),
            metadata_json=redact_payload(metadata) if metadata else None,
        )
        self.db.add(stage)
        self._commit()
        self.db.refresh(stage)
        self.stage_id = stage.id
        return stage

    def finish_stage(self, *, output: Any = None, quality_grade: str | None = None, metadata: dict[str, Any] | None = None) -> AnalysisStage | None:
        stage = self._stage()
        if stage is None:
            return None
        if output is not None:
            artifact = self.record_artifact(ArtifactType.STRUCTURED_OUTPUT, output, artifact_key=f"{stage.phase_key}.output")
            stage.output_hash = artifact.sha256
            self._output_hashes[stage.phase_key] = artifact.sha256
        stage.status = StageStatus.COMPLETED
        stage.completed_at = _now()
        stage.quality_grade = quality_grade
        if metadata:
            current = dict(stage.metadata_json or {})
            current.update(redact_payload(metadata))
            stage.metadata_json = current
        self._commit()
        spec = phase_spec(stage.phase_key)
        self.stage_id = None
        if spec.checkpoint:
            self.checkpoint(spec.checkpoint)
        return stage

    def fail_stage(self, exc: BaseException, *, blocked: bool = False, cancelled: bool = False) -> AnalysisStage | None:
        stage = self._stage()
        if stage is None:
            return None
        if cancelled:
            stage.status = StageStatus.CANCELLED
        elif blocked:
            stage.status = StageStatus.BLOCKED
        else:
            stage.status = StageStatus.FAILED
        stage.completed_at = _now()
        stage.error_code = _error_code(exc)
        stage.error_message = _error_message(exc)
        run = self._run()
        run.failed_stage = stage.phase_key
        self._commit()
        self.stage_id = None
        return stage

    def start_node(self, node_key: str, *, metadata: dict[str, Any] | None = None) -> AnalysisNode:
        if self.stage_id is None:
            raise RuntimeError("workflow_stage_not_started")
        spec = node_spec(node_key)
        node = AnalysisNode(
            analysis_run_id=self._run().id,
            stage_id=self.stage_id,
            node_key=spec.node_key,
            node_type=spec.node_type,
            agent_role=spec.agent_role,
            status=NodeStatus.RUNNING,
            criticality=spec.criticality,
            attempt_count=0,
            max_attempts=spec.max_attempts,
            started_at=_now(),
            retryable=spec.retryable,
            resumable=spec.resumable,
            metadata_json=redact_payload(metadata) if metadata else None,
        )
        self.db.add(node)
        self._commit()
        self.db.refresh(node)
        self.node_id = node.id
        return node

    def finish_node(self, *, output: Any = None) -> AnalysisNode | None:
        node = self._node()
        if node is None:
            return None
        if output is not None:
            artifact = self.record_artifact(ArtifactType.STRUCTURED_OUTPUT, output, artifact_key=f"{node.node_key}.output")
            node.output_artifact_id = artifact.id
            self._output_hashes[node.node_key] = artifact.sha256
        node.status = NodeStatus.COMPLETED
        node.completed_at = _now()
        self._completed_nodes.append(node.node_key)
        self._commit()
        self.node_id = None
        return node

    def fail_node(self, exc: BaseException, *, blocked: bool = False, cancelled: bool = False) -> AnalysisNode | None:
        node = self._node()
        if node is None:
            return None
        if cancelled:
            node.status = NodeStatus.CANCELLED
        elif blocked:
            node.status = NodeStatus.BLOCKED
        else:
            node.status = NodeStatus.FAILED
        node.completed_at = _now()
        node.error_code = _error_code(exc)
        node.error_message = _error_message(exc)
        run = self._run()
        if node.criticality != "optional":
            run.failed_node = node.node_key
            run.failed_stage = run.failed_stage or (self._stage().phase_key if self._stage() is not None else None)
        self._commit()
        self.node_id = None
        return node

    def start_attempt(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        model_profile_id: int | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AnalysisNodeAttempt:
        node = self._node()
        if node is None:
            raise RuntimeError("workflow_node_not_started")
        node.attempt_count = int(node.attempt_count or 0) + 1
        attempt = AnalysisNodeAttempt(
            analysis_run_id=self._run().id,
            stage_id=node.stage_id,
            node_id=node.id,
            attempt_no=node.attempt_count,
            status=AttemptStatus.RUNNING,
            started_at=_now(),
            provider=provider,
            model=model,
            model_profile_id=model_profile_id,
            request_id=request_id,
            metadata_json=redact_payload(metadata) if metadata else None,
        )
        self.db.add(attempt)
        self._commit()
        self.db.refresh(attempt)
        self.attempt_id = attempt.id
        return attempt

    def finish_attempt(
        self,
        *,
        output: Any = None,
        transport_retry_count: int | None = None,
        structured_retry_count: int | None = None,
        latency_ms: int | None = None,
        input_hash: str | None = None,
        output_hash: str | None = None,
    ) -> AnalysisNodeAttempt | None:
        attempt = self._attempt()
        if attempt is None:
            return None
        if output is not None:
            artifact = self.record_artifact(ArtifactType.STRUCTURED_OUTPUT, output, artifact_key=f"attempt.{attempt.attempt_no}.output")
            attempt.structured_output_artifact_id = artifact.id
            attempt.output_hash = artifact.sha256
        if output_hash:
            attempt.output_hash = output_hash
        if input_hash:
            attempt.input_hash = input_hash
        if transport_retry_count is not None:
            attempt.transport_retry_count = int(transport_retry_count)
        if structured_retry_count is not None:
            attempt.structured_retry_count = int(structured_retry_count)
        attempt.latency_ms = latency_ms
        attempt.status = AttemptStatus.COMPLETED
        attempt.completed_at = _now()
        started = _as_unaware(attempt.started_at)
        completed = _as_unaware(attempt.completed_at)
        if started is not None and completed is not None and attempt.latency_ms is None:
            attempt.latency_ms = max(0, int((completed - started).total_seconds() * 1000))
        self._commit()
        self.attempt_id = None
        return attempt

    def fail_attempt(
        self,
        exc: BaseException,
        *,
        retryable: bool = False,
        transport_retry_count: int | None = None,
        structured_retry_count: int | None = None,
    ) -> AnalysisNodeAttempt | None:
        attempt = self._attempt()
        if attempt is None:
            return None
        attempt.status = AttemptStatus.FAILED
        attempt.completed_at = _now()
        attempt.error_type = type(exc).__name__
        attempt.error_code = _error_code(exc)
        attempt.error_message = _error_message(exc)
        attempt.retryable = retryable
        if transport_retry_count is not None:
            attempt.transport_retry_count = int(transport_retry_count)
        if structured_retry_count is not None:
            attempt.structured_retry_count = int(structured_retry_count)
        self.record_artifact(
            ArtifactType.ERROR,
            {"error_type": attempt.error_type, "error_code": attempt.error_code, "error_message": attempt.error_message},
            artifact_key=f"attempt.{attempt.attempt_no}.error",
        )
        self._commit()
        self.attempt_id = None
        return attempt

    def record_artifact(
        self,
        artifact_type: str,
        content: Any,
        *,
        artifact_key: str | None = None,
        stage_id: int | None = None,
        node_id: int | None = None,
        attempt_id: int | None = None,
    ) -> AnalysisArtifact:
        run = self._run()
        key = artifact_key or f"{artifact_type.lower()}.{_now().strftime('%Y%m%d%H%M%S%f')}"
        artifact = build_artifact(
            analysis_run_id=run.id,
            artifact_type=artifact_type,
            artifact_key=key,
            content=content,
            stage_id=stage_id if stage_id is not None else self.stage_id,
            node_id=node_id if node_id is not None else self.node_id,
            attempt_id=attempt_id if attempt_id is not None else self.attempt_id,
        )
        self.db.add(artifact)
        self._commit()
        self.db.refresh(artifact)
        self.last_artifact_id = artifact.id
        run.last_artifact_id = artifact.id
        self._commit()
        return artifact

    def record_claims(
        self,
        claims: Iterable[dict[str, Any]],
        *,
        debate_type: str = DebateType.INVESTMENT,
    ) -> list[AnalysisClaim]:
        run = self._run()
        stored: list[AnalysisClaim] = []
        for raw in claims:
            if not isinstance(raw, dict):
                continue
            claim_id = str(raw.get("claim_id") or "").strip()
            if not claim_id:
                continue
            row = (
                self.db.query(AnalysisClaim)
                .filter(AnalysisClaim.analysis_run_id == run.id, AnalysisClaim.claim_id == claim_id)
                .first()
            )
            evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else raw.get("evidence_refs")
            payload = {
                "debate_type": str(raw.get("debate_type") or debate_type),
                "speaker": raw.get("speaker"),
                "stance": raw.get("stance"),
                "statement": str(raw.get("statement") or raw.get("claim") or ""),
                "evidence_refs_json": redact_payload(evidence) if evidence is not None else [],
                "confidence": raw.get("confidence"),
                "status": _claim_status(raw.get("status")),
                "parent_claim_id": raw.get("parent_claim_id"),
                "target_claim_ids_json": list(raw.get("target_claim_ids") or []),
                "stage_id": self.stage_id,
                "node_id": self.node_id,
            }
            try:
                payload["confidence"] = None if payload["confidence"] is None else float(payload["confidence"])
            except (TypeError, ValueError):
                payload["confidence"] = None
            if row is None:
                row = AnalysisClaim(analysis_run_id=run.id, claim_id=claim_id, **payload)
                self.db.add(row)
            else:
                for key, value in payload.items():
                    setattr(row, key, value)
                row.updated_at = _now()
            stored.append(row)
        self._commit()
        if stored:
            self.record_artifact(
                ArtifactType.CLAIMS,
                [
                    {
                        "claim_id": item.claim_id,
                        "speaker": item.speaker,
                        "stance": item.stance,
                        "statement": item.statement,
                        "status": item.status,
                    }
                    for item in stored
                ],
                artifact_key=f"claims.{debate_type}",
            )
        return stored

    def checkpoint(self, name: str, *, extra: dict[str, Any] | None = None) -> AnalysisArtifact:
        run = self._run()
        payload = {
            "run_id": run.id,
            "checkpoint": name,
            "completed_nodes": list(self._completed_nodes),
            "input_hashes": dict(self._input_hashes),
            "output_hashes": dict(self._output_hashes),
            "created_at": _now().isoformat(),
        }
        if extra:
            payload.update(redact_payload(extra))
        artifact = self.record_artifact(ArtifactType.CHECKPOINT, payload, artifact_key=f"checkpoint.{name}")
        run.last_checkpoint = name
        run.resumable = name != CheckpointName.FINALIZED
        self._commit()
        return artifact

    def finish_run(
        self,
        status: str,
        *,
        summary: str | None = None,
        final_rating: str | None = None,
        cash_target: str | None = None,
        confidence: str | None = None,
        data_quality_grade: str | None = None,
        markdown: str | None = None,
        structured_payload: dict[str, Any] | None = None,
        model_profile_id: int | None = None,
        error: BaseException | None = None,
        blocked: bool = False,
    ) -> AnalysisRun:
        run = self._run()
        existing_payload = dict(run.structured_result_json or {})
        if structured_payload is not None:
            payload = dict(structured_payload)
            if existing_payload.get("skill_runtime") and "skill_runtime" not in payload:
                payload["skill_runtime"] = existing_payload["skill_runtime"]
            run.structured_result_json = payload
        run.status = status
        run.completed_at = _now()
        run.summary = summary
        run.final_rating = final_rating
        run.cash_target = cash_target
        run.confidence = confidence
        run.data_quality_grade = data_quality_grade
        if markdown is not None:
            run.markdown_text = markdown
        if model_profile_id is not None:
            run.model_profile_id = model_profile_id
        if status in {RunStatus.COMPLETED, RunStatus.BLOCKED}:
            run.resumable = False
        elif status in {RunStatus.FAILED, RunStatus.INTERRUPTED}:
            run.resumable = bool(run.last_checkpoint)
        else:
            run.resumable = False
        if error is not None:
            run.error_code = _error_code(error)
            run.error_message = _error_message(error)
        if blocked:
            run.status = RunStatus.BLOCKED
        self._commit()
        return run

    def fail_open_work(self, exc: BaseException, *, cancelled: bool = False, blocked: bool = False) -> None:
        if self.attempt_id is not None:
            self.fail_attempt(exc, retryable=not cancelled and not blocked)
        if self.node_id is not None:
            self.fail_node(exc, cancelled=cancelled, blocked=blocked)
        if self.stage_id is not None:
            self.fail_stage(exc, cancelled=cancelled, blocked=blocked)

    def fail_run(self, exc: BaseException, *, cancelled: bool = False, blocked: bool = False) -> AnalysisRun | None:
        if self.run_id is None:
            return None
        self.fail_open_work(exc, cancelled=cancelled, blocked=blocked)
        if cancelled:
            status = RunStatus.CANCELLED
        elif blocked:
            status = RunStatus.BLOCKED
        else:
            status = RunStatus.FAILED
        return self.finish_run(status, error=exc, blocked=blocked)

    def bind_input_hash(self, key: str, content: Any) -> str:
        digest = sha256_content(redact_payload(content) if not isinstance(content, str) else redact_payload(content))
        self._input_hashes[key] = digest
        stage = self._stage()
        if stage is not None and stage.input_hash is None:
            stage.input_hash = digest
            self._commit()
        node = self._node()
        if node is not None:
            artifact = self.record_artifact(ArtifactType.INPUT, content, artifact_key=f"{key}.input")
            node.input_artifact_id = artifact.id
            attempt = self._attempt()
            if attempt is not None and attempt.input_hash is None:
                attempt.input_hash = artifact.sha256
            self._commit()
            return artifact.sha256
        return digest
