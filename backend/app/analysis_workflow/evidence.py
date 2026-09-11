"""One immutable, persisted input snapshot for every agent and retry."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .constants import ArtifactType
from .hashing import canonical_json
from .models import AnalysisArtifact
from .resume import ResumeRejected, hash_input, validate_resume_inputs
from .serializers import redact_payload


def _reference_paths(value: Any, path: str = "", depth: int = 0) -> list[str]:
    if value is None or value == "" or value == [] or value == {}:
        return []
    paths = [path] if path else []
    if depth < 4:
        entries = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
        for key, item in entries:
            paths.extend(_reference_paths(item, f"{path}.{key}" if path else str(key), depth + 1))
    return paths


@dataclass(frozen=True)
class FrozenEvidence:
    analysis_run_id: int
    evidence_snapshot_id: int
    evidence_hash: str
    market_snapshot_at: str | None
    portfolio_snapshot_id: int
    content: str

    @property
    def binding(self) -> dict[str, Any]:
        return {
            "analysis_run_id": self.analysis_run_id,
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "evidence_hash": self.evidence_hash,
            "market_snapshot_at": self.market_snapshot_at,
            "portfolio_snapshot_id": self.portfolio_snapshot_id,
        }

    def input(self) -> dict[str, Any]:
        return json.loads(self.content)["input"]

    def payload(self, **context: Any) -> dict[str, Any]:
        value = json.loads(self.content)
        return {**context, **self.binding, "input": value["input"], "evidence_refs": value["evidence_refs"]}


def freeze_evidence(audit, input_payload: dict[str, Any], contract: dict[str, Any]) -> FrozenEvidence:
    stored = audit.load_artifact_content("evidence_snapshot")
    if stored is not None:
        validate_resume_inputs(
            {"portfolio_snapshot": hash_input(stored["input"]["snapshot"]), "workflow_contract": hash_input(stored["contract"])},
            {"portfolio_snapshot": hash_input(input_payload["snapshot"]), "workflow_contract": hash_input(contract)},
        )
        content = stored
        artifact = audit.db.query(AnalysisArtifact).filter_by(
            analysis_run_id=audit.run_id, artifact_key="evidence_snapshot",
        ).order_by(AnalysisArtifact.id.desc()).first()
        if artifact is None or hash_input(content) != artifact.sha256:
            raise ResumeRejected("evidence_snapshot_hash_mismatch")
    else:
        instrument_market = input_payload.get("market", {}).get("instrument_market")
        if instrument_market is not None:
            from ..market.instrument_schemas import InstrumentMarketEvidence

            InstrumentMarketEvidence.model_validate(instrument_market)
        content = redact_payload({
            "schema_version": "v3-core-3.evidence.v1",
            "input": input_payload,
            "contract": contract,
            "evidence_refs": _reference_paths(input_payload),
        })
        artifact = audit.record_artifact(ArtifactType.EVIDENCE, content, artifact_key="evidence_snapshot")
    frozen = FrozenEvidence(
        analysis_run_id=audit.run_id,
        evidence_snapshot_id=artifact.id,
        evidence_hash=artifact.sha256,
        market_snapshot_at=content["input"]["market"].get("captured_at"),
        portfolio_snapshot_id=content["input"]["snapshot"]["id"],
        content=canonical_json(content),
    )
    audit.evidence_binding = frozen.binding
    audit.model_profiles = {row["id"]: row for row in content["contract"]["profiles"]}
    audit.bind_input_hash("evidence_snapshot", content)
    audit.bind_input_hash("workflow_contract", contract)
    run = audit._run()
    run_payload = dict(run.structured_result_json or {})
    run_payload["workflow_execution"] = {**run_payload.get("workflow_execution", {}), **frozen.binding}
    run.structured_result_json = run_payload
    audit._commit()
    return frozen


def model_profile_identity(profile) -> dict[str, Any]:
    return {
        "id": profile.id, "model": profile.model_name, "parameters": profile.parameters_json,
        "provider_id": profile.provider_id, "provider": profile.provider.provider,
        "base_url": profile.provider.base_url,
    }
