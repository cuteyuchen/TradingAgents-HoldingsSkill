"""Build safe, verifiable, read-only AnalysisRun export packages."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..config import settings
from ..system.logging import redact_text
from ..v2_models import AnalysisJob, AnalysisRun, HoldingItem, PortfolioSnapshot
from .constants import ArtifactType
from .hashing import canonical_json, sha256_content
from .models import AnalysisArtifact, AnalysisClaim, AnalysisNode, AnalysisNodeAttempt, AnalysisStage
from .serializers import redact_payload
from .timeline import build_analysis_timeline_from_records


PACKAGE_VERSION = "v1"
EXPORT_MODES = {"standard", "debug"}
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_-]+")
_PRIVATE_REASONING = re.compile(r"(?i)private[_ -]?reasoning(?:[_ -]?[a-z0-9]+)?")
_HIDDEN_REASONING_LINE = re.compile(
    r"(?im)^\s*(?:hidden[_ -]?)?(?:chain[_ -]?of[_ -]?thought|reasoning(?:[_ -]?(?:content|details))?|"
    r"thinking|thoughts|scratchpad|internal[_ -]?analysis)\s*[:=].*$"
)
_HIDDEN_REASONING_KEYS = {
    "analysis",
    "reasoning",
    "reasoningcontent",
    "reasoningdetails",
    "reasoningsummary",
    "hiddenreasoning",
    "internalreasoning",
    "chainofthought",
    "chainofthoughts",
    "thinking",
    "thoughts",
    "scratchpad",
    "internalanalysis",
}
_EXCLUDED_EXPORT_KEYS = {"baseurl", "encryptedapikey", "encryptedwebhook", "encryptedsecret"}
_NOT_AVAILABLE = "not_available"


class AnalysisExportError(RuntimeError):
    """A user-facing AnalysisRun export failure."""

    code = "analysis_export_error"


class AnalysisExportSizeLimitExceeded(AnalysisExportError):
    code = "analysis_export_size_limit_exceeded"


@dataclass(frozen=True)
class ExportPackage:
    """A dynamically generated ZIP that must be deleted after it is sent."""

    path: Path
    filename: str
    mode: str
    manifest: dict[str, Any]

    def cleanup(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True)
class _RunBundle:
    run: AnalysisRun
    job: AnalysisJob | None
    snapshot: PortfolioSnapshot | None
    holdings: list[HoldingItem]
    stages: list[AnalysisStage]
    nodes: list[AnalysisNode]
    attempts: list[AnalysisNodeAttempt]
    artifacts: list[AnalysisArtifact]
    claims: list[AnalysisClaim]
    timeline: list[dict[str, Any]]
    structured: dict[str, Any]
    workflow: dict[str, Any]
    result: dict[str, Any]
    frozen_evidence: dict[str, Any] | None


class _PackageWriter:
    """Writes server-owned paths to a short-lived staging directory."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max(1, int(max_bytes))
        self.total_bytes = 0
        self.entries: list[dict[str, Any]] = []
        self._paths: set[str] = set()

    def add_json(self, path: str, value: Any, *, required: bool = True) -> None:
        self.add_bytes(path, _json_bytes(value), required=required)

    def add_text(self, path: str, value: Any, *, required: bool = True) -> None:
        self.add_bytes(path, _safe_text(value).encode("utf-8"), required=required)

    def add_bytes(self, path: str, data: bytes, *, required: bool = True) -> None:
        safe_path = _safe_export_path(path)
        if safe_path in self._paths:
            raise AnalysisExportError(f"duplicate_export_path:{safe_path}")
        next_size = self.total_bytes + len(data)
        if next_size > self.max_bytes:
            raise AnalysisExportSizeLimitExceeded(
                f"Analysis export exceeds ANALYSIS_EXPORT_MAX_BYTES ({self.max_bytes} bytes)."
            )
        destination = self.root.joinpath(*safe_path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        self.total_bytes = next_size
        self._paths.add(safe_path)
        self.entries.append(
            {
                "path": safe_path,
                "sha256": sha256_content(data),
                "size": len(data),
                "required": bool(required),
            }
        )

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        path = self.root / "manifest.json"
        data = _json_bytes(manifest)
        if self.total_bytes + len(data) > self.max_bytes:
            raise AnalysisExportSizeLimitExceeded(
                f"Analysis export exceeds ANALYSIS_EXPORT_MAX_BYTES ({self.max_bytes} bytes)."
            )
        path.write_bytes(data)
        self.total_bytes += len(data)

    def staged_paths(self) -> list[Path]:
        return sorted((path for path in self.root.rglob("*") if path.is_file()), key=lambda item: item.as_posix())


class AnalysisRunExporter:
    """Build Standard and Debug packages from persisted AnalysisRun data only."""

    def __init__(
        self,
        db: Session,
        *,
        max_bytes: int | None = None,
        temp_dir: str | Path | None = None,
    ) -> None:
        self.db = db
        self.max_bytes = max(1, int(max_bytes if max_bytes is not None else settings.ANALYSIS_EXPORT_MAX_BYTES))
        configured_temp = temp_dir if temp_dir is not None else settings.ANALYSIS_EXPORT_TEMP_DIR
        self.temp_dir = Path(configured_temp).expanduser() if configured_temp else None

    def build_standard_package(self, run_id: int) -> ExportPackage:
        return self.build_package(run_id, mode="standard")

    def build_debug_package(self, run_id: int) -> ExportPackage:
        return self.build_package(run_id, mode="debug")

    def build_package(self, run_id: int, *, mode: str) -> ExportPackage:
        mode = str(mode or "").lower()
        if mode not in EXPORT_MODES:
            raise AnalysisExportError("export_mode_must_be_standard_or_debug")
        bundle = self._load_bundle(run_id)
        exported_at = datetime.now().astimezone().isoformat()
        staging = self._new_staging_directory()
        archive_path: Path | None = None
        try:
            writer = _PackageWriter(staging, self.max_bytes)
            self._write_standard(writer, bundle, mode=mode)
            if mode == "debug":
                self._write_debug(writer, bundle)
            manifest = self.build_manifest(bundle, mode=mode, files=writer.entries, exported_at=exported_at)
            writer.write_manifest(manifest)
            archive_path = self._new_archive_path()
            self._write_zip(staging, archive_path)
            if archive_path.stat().st_size > self.max_bytes:
                raise AnalysisExportSizeLimitExceeded(
                    f"Analysis export ZIP exceeds ANALYSIS_EXPORT_MAX_BYTES ({self.max_bytes} bytes)."
                )
            return ExportPackage(
                path=archive_path,
                filename=_export_filename(bundle, mode),
                mode=mode,
                manifest=manifest,
            )
        except Exception:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def build_manifest(
        self,
        bundle: _RunBundle,
        *,
        mode: str,
        files: Iterable[dict[str, Any]],
        exported_at: str,
    ) -> dict[str, Any]:
        return {
            "package_version": PACKAGE_VERSION,
            "run_id": bundle.run.id,
            "job_id": bundle.run.job_id,
            "trading_date": _trading_date(bundle),
            "checkpoint": _value_or_not_available(getattr(bundle.job, "checkpoint", None)),
            "analysis_mode": _value_or_not_available(bundle.run.analysis_mode),
            "workflow_version": _value_or_not_available(bundle.run.workflow_version),
            "skill_version": _value_or_not_available(bundle.run.skill_version),
            "run_status": _value_or_not_available(bundle.run.status),
            "data_quality_grade": _value_or_not_available(bundle.run.data_quality_grade),
            "created_at": _iso(bundle.run.created_at),
            "completed_at": _iso(bundle.run.completed_at),
            "export_mode": mode,
            "exported_at": exported_at,
            "files": list(files),
        }

    def _load_bundle(self, run_id: int) -> _RunBundle:
        run = self.db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if run is None:
            raise AnalysisExportError("analysis_run_not_found")
        job = self.db.query(AnalysisJob).filter(AnalysisJob.id == run.job_id).first()
        snapshot = self.db.query(PortfolioSnapshot).filter(PortfolioSnapshot.id == run.portfolio_snapshot_id).first()
        holdings = (
            self.db.query(HoldingItem)
            .filter(HoldingItem.snapshot_id == run.portfolio_snapshot_id)
            .order_by(HoldingItem.id.asc())
            .all()
        )
        stages = (
            self.db.query(AnalysisStage)
            .filter(AnalysisStage.analysis_run_id == run.id)
            .order_by(AnalysisStage.phase_order.asc(), AnalysisStage.id.asc())
            .all()
        )
        nodes = (
            self.db.query(AnalysisNode)
            .filter(AnalysisNode.analysis_run_id == run.id)
            .order_by(AnalysisNode.id.asc())
            .all()
        )
        attempts = (
            self.db.query(AnalysisNodeAttempt)
            .filter(AnalysisNodeAttempt.analysis_run_id == run.id)
            .order_by(AnalysisNodeAttempt.node_id.asc(), AnalysisNodeAttempt.attempt_no.asc(), AnalysisNodeAttempt.id.asc())
            .all()
        )
        artifacts = (
            self.db.query(AnalysisArtifact)
            .filter(AnalysisArtifact.analysis_run_id == run.id)
            .order_by(AnalysisArtifact.id.asc())
            .all()
        )
        claims = (
            self.db.query(AnalysisClaim)
            .filter(AnalysisClaim.analysis_run_id == run.id)
            .order_by(AnalysisClaim.id.asc())
            .all()
        )
        structured = dict(run.structured_result_json or {}) if isinstance(run.structured_result_json, dict) else {}
        workflow = dict(structured.get("workflow") or {}) if isinstance(structured.get("workflow"), dict) else {}
        result = dict(structured.get("result") or {}) if isinstance(structured.get("result"), dict) else {}
        frozen_evidence = _artifact_json(_latest_artifact(artifacts, "evidence_snapshot"))
        timeline = build_analysis_timeline_from_records(run, stages, nodes, attempts, artifacts, claims)
        return _RunBundle(
            run=run,
            job=job,
            snapshot=snapshot,
            holdings=holdings,
            stages=stages,
            nodes=nodes,
            attempts=attempts,
            artifacts=artifacts,
            claims=claims,
            timeline=timeline,
            structured=structured,
            workflow=workflow,
            result=result,
            frozen_evidence=frozen_evidence,
        )

    def _write_standard(self, writer: _PackageWriter, bundle: _RunBundle, *, mode: str) -> None:
        evidence_index, sources, legacy_mapping = _build_evidence_index(bundle)
        writer.add_text("README.md", _readme(bundle, mode=mode))
        writer.add_json("run/analysis-run.json", _analysis_run_payload(bundle))
        writer.add_json("run/workflow.json", _workflow_payload(bundle))
        writer.add_json("run/configuration.json", _configuration_payload(bundle))
        writer.add_json("run/timeline.json", {"events": bundle.timeline})
        writer.add_json("portfolio/portfolio-snapshot.json", _portfolio_snapshot_payload(bundle))
        writer.add_json("portfolio/holdings.json", {"holdings": _holdings_payload(bundle)})
        writer.add_json("market/market-snapshot.json", _market_snapshot_payload(bundle))
        writer.add_json("market/final-quote-refresh.json", _final_quote_payload(bundle))
        writer.add_json("evidence/evidence-index.json", evidence_index)
        writer.add_json("evidence/sources.json", sources)
        writer.add_json("evidence/quality-gate.json", _quality_gate_payload(bundle))
        writer.add_json("agents/summary.json", _agent_summary(bundle))
        writer.add_json("debate/investment-claims.json", _claims_payload(bundle, "investment", legacy_mapping))
        writer.add_json("debate/risk-claims.json", _claims_payload(bundle, "risk", legacy_mapping))
        resolution = _claim_resolution_payload(bundle)
        if resolution is not None:
            writer.add_json("debate/claim-resolution.json", resolution)
        writer.add_json("decision/research-manager.json", _decision_payload(bundle, "research_manager"))
        writer.add_json("decision/trader.json", _decision_payload(bundle, "trader"))
        writer.add_json("decision/risk-manager.json", _decision_payload(bundle, "risk_manager"))
        writer.add_json("decision/risk-synthesis.json", _decision_payload(bundle, "risk_synthesis"))
        writer.add_json("decision/portfolio-manager.json", _decision_payload(bundle, "portfolio_manager"))
        writer.add_json("decision/portfolio-decision-gate.json", _decision_payload(bundle, "portfolio_decision_gate"))
        writer.add_json("decision/candidates.json", _candidate_payload(bundle))
        writer.add_json("decision/final-decision.json", _final_decision_payload(bundle))
        writer.add_json("errors/failures.json", _failures_payload(bundle))
        writer.add_json("errors/retries.json", _retries_payload(bundle))
        writer.add_text("reports/summary.md", _summary_markdown(bundle))
        writer.add_text("reports/full-analysis.md", bundle.run.markdown_text or "Not available for this run.\n")

    def _write_debug(self, writer: _PackageWriter, bundle: _RunBundle) -> None:
        artifacts_index = _artifact_index_payload(bundle)
        partial_debug = bundle.frozen_evidence is None or not bundle.artifacts
        writer.add_json(
            "evidence/evidence-snapshot.json",
            bundle.frozen_evidence or _not_available("evidence_snapshot_missing"),
        )
        writer.add_json("evidence/raw-artifacts-index.json", _raw_evidence_artifact_index(bundle))
        writer.add_json("artifacts/index.json", {"partial_debug": partial_debug, **artifacts_index})
        writer.add_json("prompts/manifest.json", _prompt_manifest_payload(bundle))
        writer.add_json("checkpoints/checkpoints.json", _checkpoint_payload(bundle))
        writer.add_json("workflow/node-attempts.json", {"attempts": [_attempt_payload(item) for item in bundle.attempts]})
        writer.add_json("workflow/stage-records.json", {"stages": [_stage_payload(item) for item in bundle.stages]})
        writer.add_json("errors/raw-errors.json", _raw_errors_payload(bundle))
        attempts_by_node = _group_by(bundle.attempts, lambda item: item.node_id)
        stages_by_id = {item.id: item for item in bundle.stages}
        for node in bundle.nodes:
            node_dir = f"agents/{_safe_component(node.node_key, fallback=f'node-{node.id}') }"
            node_artifacts = [item for item in bundle.artifacts if item.node_id == node.id]
            node_attempts = attempts_by_node.get(node.id, [])
            writer.add_json(f"{node_dir}/input.json", _node_input_payload(node, node_artifacts, bundle.artifacts))
            writer.add_json(f"{node_dir}/prompt-template.json", _node_artifact_or_missing(node_artifacts, ArtifactType.PROMPT_TEMPLATE))
            writer.add_json(f"{node_dir}/rendered-prompt.json", _node_artifact_or_missing(node_artifacts, ArtifactType.RENDERED_PROMPT))
            writer.add_json(f"{node_dir}/structured-output.json", _node_output_payload(node, node_artifacts, bundle.artifacts))
            writer.add_text(f"{node_dir}/raw-output.txt", _node_raw_output(node_attempts, bundle.artifacts))
            writer.add_json(f"{node_dir}/attempts.json", {"attempts": [_attempt_payload(item) for item in node_attempts]})
            writer.add_json(
                f"{node_dir}/metadata.json",
                {
                    "node": _node_payload(node),
                    "stage": _stage_payload(stages_by_id[node.stage_id]) if node.stage_id in stages_by_id else _not_available("stage_missing"),
                    "artifacts": [_artifact_metadata(item) for item in node_artifacts],
                },
            )

    def _new_staging_directory(self) -> Path:
        directory = self._prepare_temp_dir()
        return Path(tempfile.mkdtemp(prefix="analysis-export-", dir=str(directory) if directory else None))

    def _new_archive_path(self) -> Path:
        directory = self._prepare_temp_dir()
        handle = tempfile.NamedTemporaryFile(
            prefix="analysis-run-",
            suffix=".zip",
            dir=str(directory) if directory else None,
            delete=False,
        )
        handle.close()
        return Path(handle.name)

    def _prepare_temp_dir(self) -> Path | None:
        if self.temp_dir is None:
            return None
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        return self.temp_dir

    @staticmethod
    def _write_zip(staging: Path, destination: Path) -> None:
        with zipfile.ZipFile(destination, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for source in sorted((item for item in staging.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
                relative = source.relative_to(staging).as_posix()
                archive.write(source, arcname=_safe_export_path(relative))


def verify_export_package(package: str | Path) -> dict[str, Any]:
    """Verify every manifest-listed file and reject tampering or stray entries."""

    target = Path(package)
    errors: list[str] = []
    files_verified = 0
    if target.is_dir():
        return _verify_directory(target)
    try:
        with zipfile.ZipFile(target, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                return {"valid": False, "files_verified": 0, "errors": ["duplicate_zip_members"]}
            invalid = [name for name in names if not _is_safe_export_path(name)]
            if invalid:
                return {"valid": False, "files_verified": 0, "errors": [f"unsafe_zip_path:{invalid[0]}"]}
            if "manifest.json" not in names:
                return {"valid": False, "files_verified": 0, "errors": ["manifest_missing"]}
            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {"valid": False, "files_verified": 0, "errors": ["manifest_invalid"]}
            expected = _manifest_entries(manifest, errors)
            expected_paths = {entry["path"] for entry in expected}
            extras = set(names) - expected_paths - {"manifest.json"}
            if extras:
                errors.append(f"unlisted_zip_member:{sorted(extras)[0]}")
            for entry in expected:
                path = entry["path"]
                if path not in names:
                    errors.append(f"missing:{path}")
                    continue
                payload = archive.read(path)
                entry_valid = True
                if len(payload) != entry["size"]:
                    errors.append(f"size_mismatch:{path}")
                    entry_valid = False
                if sha256_content(payload) != entry["sha256"]:
                    errors.append(f"sha256_mismatch:{path}")
                    entry_valid = False
                if entry_valid:
                    files_verified += 1
    except (OSError, zipfile.BadZipFile):
        return {"valid": False, "files_verified": 0, "errors": ["zip_unreadable"]}
    return {"valid": not errors, "files_verified": files_verified, "errors": errors}


def _verify_directory(root: Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return {"valid": False, "files_verified": 0, "errors": ["manifest_missing"]}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"valid": False, "files_verified": 0, "errors": ["manifest_invalid"]}
    errors: list[str] = []
    expected = _manifest_entries(manifest, errors)
    expected_paths = {entry["path"] for entry in expected}
    actual_paths = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file()
    }
    extras = actual_paths - expected_paths - {"manifest.json"}
    if extras:
        errors.append(f"unlisted_file:{sorted(extras)[0]}")
    files_verified = 0
    for entry in expected:
        path = entry["path"]
        target = root.joinpath(*path.split("/"))
        if not target.is_file():
            errors.append(f"missing:{path}")
            continue
        try:
            payload = target.read_bytes()
        except OSError:
            errors.append(f"unreadable:{path}")
            continue
        entry_valid = True
        if len(payload) != entry["size"]:
            errors.append(f"size_mismatch:{path}")
            entry_valid = False
        if sha256_content(payload) != entry["sha256"]:
            errors.append(f"sha256_mismatch:{path}")
            entry_valid = False
        if entry_valid:
            files_verified += 1
    return {"valid": not errors, "files_verified": files_verified, "errors": errors}


def _manifest_entries(manifest: Any, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        errors.append("manifest_invalid")
        return []
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list):
        errors.append("manifest_files_invalid")
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            errors.append("manifest_entry_invalid")
            continue
        path = raw.get("path")
        digest = raw.get("sha256")
        size = raw.get("size")
        if not isinstance(path, str) or not _is_safe_export_path(path):
            errors.append("manifest_path_invalid")
            continue
        if path in seen:
            errors.append(f"manifest_duplicate:{path}")
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            errors.append(f"manifest_sha256_invalid:{path}")
            continue
        if not isinstance(size, int) or size < 0:
            errors.append(f"manifest_size_invalid:{path}")
            continue
        seen.add(path)
        entries.append({"path": path, "sha256": digest, "size": size})
    return entries


def _not_available(reason: str | None = None) -> dict[str, str]:
    payload = {"status": _NOT_AVAILABLE}
    if reason:
        payload["reason"] = reason
    return payload


def _safe_export_path(path: str) -> str:
    if not _is_safe_export_path(path):
        raise AnalysisExportError(f"unsafe_export_path:{path}")
    return PurePosixPath(path).as_posix()


def _is_safe_export_path(path: str) -> bool:
    if not path or "\\" in path:
        return False
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts and all(part not in {"", "."} for part in pure.parts)


def _safe_component(value: Any, *, fallback: str) -> str:
    cleaned = _SAFE_COMPONENT.sub("-", str(value or "")).strip("-")
    return cleaned or fallback


def _json_bytes(value: Any) -> bytes:
    sanitized = _sanitize_for_export(value)
    return canonical_json(sanitized).encode("utf-8")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return _sanitize_text(str(value))


def _sanitize_for_export(value: Any) -> Any:
    # Persisted artifacts are already redacted, but an export boundary must
    # distrust old rows and defense-in-depth redact secret-shaped fields again.
    return _strip_hidden_reasoning(redact_payload(value))


def _strip_hidden_reasoning(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            normalized = re.sub(r"[^a-z0-9]", "", name.lower())
            if normalized in _HIDDEN_REASONING_KEYS:
                continue
            if normalized in _EXCLUDED_EXPORT_KEYS:
                continue
            result[name] = _strip_hidden_reasoning(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_strip_hidden_reasoning(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    redacted = redact_text(value)
    stripped = redacted.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(redacted)
        except (TypeError, ValueError):
            pass
        else:
            return canonical_json(_strip_hidden_reasoning(redact_payload(parsed)))
    redacted = _HIDDEN_REASONING_LINE.sub("[HIDDEN_REASONING_OMITTED]", redacted)
    return _PRIVATE_REASONING.sub("[HIDDEN_REASONING_OMITTED]", redacted)


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _value_or_not_available(value: Any) -> Any:
    return value if value is not None and value != "" else _NOT_AVAILABLE


def _artifact_content(artifact: AnalysisArtifact | None) -> Any:
    if artifact is None:
        return None
    if artifact.content_json is not None:
        return artifact.content_json
    return artifact.content_text


def _artifact_json(artifact: AnalysisArtifact | None) -> dict[str, Any] | None:
    content = _artifact_content(artifact)
    return dict(content) if isinstance(content, dict) else None


def _latest_artifact(
    artifacts: Iterable[AnalysisArtifact],
    key: str,
    artifact_type: str | None = None,
) -> AnalysisArtifact | None:
    for artifact in reversed(list(artifacts)):
        if artifact.artifact_key == key and (artifact_type is None or artifact.artifact_type == artifact_type):
            return artifact
    return None


def _latest_node_artifact(
    artifacts: Iterable[AnalysisArtifact],
    *,
    node_id: int,
    artifact_type: str | None = None,
) -> AnalysisArtifact | None:
    rows = list(artifacts)
    for artifact in reversed(rows):
        if artifact.node_id == node_id and (artifact_type is None or artifact.artifact_type == artifact_type):
            return artifact
    return None


def _by_id(artifacts: Iterable[AnalysisArtifact]) -> dict[int, AnalysisArtifact]:
    return {artifact.id: artifact for artifact in artifacts}


def _trading_datetime(bundle: _RunBundle) -> datetime | None:
    for value in (
        bundle.run.market_snapshot_at,
        _input_market_captured_at(bundle),
        getattr(bundle.snapshot, "snapshot_time", None),
        bundle.run.created_at,
    ):
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def _trading_date(bundle: _RunBundle) -> str:
    timestamp = _trading_datetime(bundle)
    return timestamp.date().isoformat() if timestamp is not None else _NOT_AVAILABLE


def _input_market_captured_at(bundle: _RunBundle) -> Any:
    frozen_input = _frozen_input(bundle)
    market = frozen_input.get("market") if isinstance(frozen_input, dict) else None
    if isinstance(market, dict):
        return market.get("captured_at")
    market = bundle.structured.get("market_snapshot")
    return market.get("captured_at") if isinstance(market, dict) else None


def _frozen_input(bundle: _RunBundle) -> dict[str, Any]:
    frozen = bundle.frozen_evidence or {}
    value = frozen.get("input") if isinstance(frozen, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def _frozen_contract(bundle: _RunBundle) -> dict[str, Any]:
    frozen = bundle.frozen_evidence or {}
    value = frozen.get("contract") if isinstance(frozen, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def _export_filename(bundle: _RunBundle, mode: str) -> str:
    timestamp = _trading_datetime(bundle) or bundle.run.created_at
    if timestamp is None:
        stamp = "unknown"
    else:
        stamp = timestamp.strftime("%Y%m%d-%H%M")
    return f"analysis-run-{stamp}-{bundle.run.id}-{mode}.zip"


def _analysis_run_payload(bundle: _RunBundle) -> dict[str, Any]:
    result = bundle.result
    gate = result.get("decision_gate") if isinstance(result.get("decision_gate"), dict) else {}
    portfolio_context = bundle.workflow.get("portfolio_context") if isinstance(bundle.workflow.get("portfolio_context"), dict) else {}
    portfolio_action = _first_present(gate.get("portfolio_action"), result.get("outcome"), result.get("final_rating"))
    current_exposure = _first_present(
        gate.get("current_exposure"),
        portfolio_context.get("current_exposure"),
        result.get("current_exposure"),
    )
    target_exposure = _first_present(
        gate.get("target_exposure"),
        portfolio_context.get("target_exposure"),
        result.get("target_exposure"),
    )
    actual_execution = result.get("actual_execution")
    return {
        "run_id": bundle.run.id,
        "job_id": bundle.run.job_id,
        "trading_date": _trading_date(bundle),
        "checkpoint": _value_or_not_available(getattr(bundle.job, "checkpoint", None)),
        "trigger_type": _value_or_not_available(getattr(bundle.job, "trigger_type", None)),
        "analysis_mode": _value_or_not_available(bundle.run.analysis_mode),
        "workflow_version": _value_or_not_available(bundle.run.workflow_version),
        "skill_version": _value_or_not_available(bundle.run.skill_version),
        "status": _value_or_not_available(bundle.run.status),
        "started_at": _iso(bundle.run.started_at),
        "completed_at": _iso(bundle.run.completed_at),
        "created_at": _iso(bundle.run.created_at),
        "market_snapshot_at": _iso(bundle.run.market_snapshot_at),
        "data_quality_grade": _value_or_not_available(bundle.run.data_quality_grade),
        "confidence": _value_or_not_available(bundle.run.confidence),
        "portfolio_action": _value_or_not_available(portfolio_action),
        "current_exposure": _value_or_not_available(current_exposure),
        "target_exposure": _value_or_not_available(target_exposure),
        "system_decision": {
            "final_rating": _value_or_not_available(bundle.run.final_rating),
            "portfolio_action": _value_or_not_available(portfolio_action),
            "final_decision_status": "available" if bundle.result else _NOT_AVAILABLE,
        },
        "actual_execution": actual_execution if actual_execution is not None else _not_available("not_persisted_by_analysis_run"),
        "parameter_set_version": _value_or_not_available(bundle.run.parameter_set_version),
        "parameter_set_hash": _value_or_not_available(bundle.run.parameter_set_hash),
        "portfolio_snapshot_id": bundle.run.portfolio_snapshot_id,
        "evidence_hash": _evidence_hash(bundle),
    }


def _workflow_payload(bundle: _RunBundle) -> dict[str, Any]:
    contract = _frozen_contract(bundle)
    plan = contract.get("plan") if isinstance(contract.get("plan"), dict) else {}
    plan_nodes = {
        str(node.get("node_key")): node
        for phase in plan.get("phases") or []
        if isinstance(phase, dict)
        for node in phase.get("nodes") or []
        if isinstance(node, dict) and node.get("node_key")
    }
    nodes = []
    dependencies: dict[str, Any] = {}
    parallel_groups: dict[str, Any] = {}
    for node in bundle.nodes:
        plan_node = plan_nodes.get(node.node_key, {})
        deps = plan_node.get("dependencies") if isinstance(plan_node, dict) else None
        group = plan_node.get("parallel_group") if isinstance(plan_node, dict) else None
        dependencies[node.node_key] = deps if isinstance(deps, (list, tuple)) else _NOT_AVAILABLE
        parallel_groups[node.node_key] = group if group else _NOT_AVAILABLE
        nodes.append(
            {
                **_node_payload(node),
                "dependencies": dependencies[node.node_key],
                "parallel_group": parallel_groups[node.node_key],
                "analysis_modes": plan_node.get("analysis_modes", _NOT_AVAILABLE) if isinstance(plan_node, dict) else _NOT_AVAILABLE,
            }
        )
    execution = (bundle.structured.get("workflow_execution") or {}) if isinstance(bundle.structured.get("workflow_execution"), dict) else {}
    return {
        "phases": plan.get("phases") if plan.get("phases") else [_stage_payload(stage) for stage in bundle.stages],
        "nodes": nodes,
        "dependencies": dependencies,
        "parallel_groups": parallel_groups,
        "analysis_mode": _value_or_not_available(bundle.run.analysis_mode),
        "legacy_fallback_used": bool(execution.get("legacy_fallback_used") or bundle.workflow.get("legacy_fallback_used")),
        "completed_nodes": [node.node_key for node in bundle.nodes if node.status in {"succeeded", "completed"}],
        "failed_nodes": [node.node_key for node in bundle.nodes if node.status in {"failed", "blocked", "cancelled"}],
        "skipped_nodes": [node.node_key for node in bundle.nodes if node.status == "skipped"],
    }


def _configuration_payload(bundle: _RunBundle) -> dict[str, Any]:
    contract = _frozen_contract(bundle)
    profiles = []
    for profile in contract.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        profiles.append(
            {
                "id": profile.get("id"),
                "provider_id": profile.get("provider_id"),
                "provider": profile.get("provider"),
                "model": profile.get("model"),
                "parameters": profile.get("parameters") or {},
            }
        )
    prompt_manifest = contract.get("prompts") if isinstance(contract.get("prompts"), dict) else {}
    return {
        "workflow_version": _value_or_not_available(bundle.run.workflow_version),
        "skill_version": _value_or_not_available(bundle.run.skill_version),
        "prompt_version": prompt_manifest.get("version", _NOT_AVAILABLE),
        "prompt_manifest": prompt_manifest or _not_available("prompt_manifest_missing"),
        "model_profiles": profiles or _not_available("model_profiles_missing"),
        "max_parallel_agents": _NOT_AVAILABLE,
        "retry_policy": {
            "nodes": [
                {
                    "node_key": node.node_key,
                    "retryable": bool(node.retryable),
                    "max_attempts": node.max_attempts,
                }
                for node in bundle.nodes
            ]
        },
        "analysis_mode": _value_or_not_available(bundle.run.analysis_mode),
        "parameter_set": {
            "version_id": _value_or_not_available(bundle.run.parameter_set_version_id),
            "version": _value_or_not_available(bundle.run.parameter_set_version),
            "hash": _value_or_not_available(bundle.run.parameter_set_hash),
            "lineage": bundle.run.governance_lineage_json or _not_available("parameter_lineage_missing"),
        },
        "evidence_hash": _evidence_hash(bundle),
        "portfolio_snapshot_id": bundle.run.portfolio_snapshot_id,
        "market_snapshot_time": _iso(bundle.run.market_snapshot_at),
    }


def _portfolio_snapshot_payload(bundle: _RunBundle) -> dict[str, Any]:
    artifact = _artifact_json(_latest_artifact(bundle.artifacts, "portfolio_snapshot"))
    if artifact is not None:
        return artifact
    structured = bundle.structured.get("input_snapshot")
    if isinstance(structured, dict):
        return structured
    snapshot = bundle.snapshot
    if snapshot is None:
        return _not_available("portfolio_snapshot_missing")
    return {
        "id": snapshot.id,
        "snapshot_time": _iso(snapshot.snapshot_time),
        "total_assets": snapshot.total_assets,
        "total_market_value": snapshot.total_market_value,
        "broker_available_cash": snapshot.broker_available_cash,
        "corrected_unused_funds": snapshot.corrected_unused_funds,
        "repo_or_standard_bond_value": snapshot.repo_or_standard_bond_value,
        "holdings": _holdings_payload(bundle),
    }


def _holdings_payload(bundle: _RunBundle) -> list[dict[str, Any]]:
    artifact = _artifact_json(_latest_artifact(bundle.artifacts, "portfolio_snapshot"))
    if artifact is not None and isinstance(artifact.get("holdings"), list):
        return [dict(item) for item in artifact["holdings"] if isinstance(item, dict)]
    structured = bundle.structured.get("input_snapshot")
    if isinstance(structured, dict) and isinstance(structured.get("holdings"), list):
        return [dict(item) for item in structured["holdings"] if isinstance(item, dict)]
    return [
        {
            "code": item.code,
            "name": item.name,
            "market": item.market,
            "qty": item.qty,
            "available_qty": item.available_qty,
            "unavailable_qty": item.unavailable_qty,
            "cost": item.cost,
            "screenshot_price": item.screenshot_price,
            "market_value": item.market_value,
            "pnl_ratio": item.pnl_ratio,
            "pnl_amount": item.pnl_amount,
            "weight": item.weight,
        }
        for item in bundle.holdings
    ]


def _market_snapshot_payload(bundle: _RunBundle) -> Any:
    artifact = _artifact_content(_latest_artifact(bundle.artifacts, "market_snapshot"))
    if artifact is not None:
        return artifact
    structured = bundle.structured.get("market_snapshot")
    if isinstance(structured, dict):
        return structured
    frozen = _frozen_input(bundle).get("market")
    return frozen if isinstance(frozen, dict) else _not_available("market_snapshot_missing")


def _final_quote_payload(bundle: _RunBundle) -> Any:
    for key in ("final_market_snapshot", "final_quote_refresh.response"):
        artifact = _artifact_content(_latest_artifact(bundle.artifacts, key))
        if artifact is not None:
            return artifact
    market = _market_snapshot_payload(bundle)
    if isinstance(market, dict) and (market.get("final_quote_refresh_status") or market.get("final_quote_refresh_at")):
        return market
    return _not_available("final_quote_refresh_missing")


def _quality_gate_payload(bundle: _RunBundle) -> Any:
    for source in (bundle.result, bundle.workflow):
        value = source.get("quality_gate") if isinstance(source, dict) else None
        if isinstance(value, dict):
            return value
    artifact = _artifact_content(_latest_artifact(bundle.artifacts, "quality_gate"))
    return artifact if artifact is not None else _not_available("quality_gate_missing")


def _agent_summary(bundle: _RunBundle) -> dict[str, Any]:
    artifact_map = _by_id(bundle.artifacts)
    summary: dict[str, Any] = {}
    for node in bundle.nodes:
        output = _artifact_content(artifact_map.get(node.output_artifact_id))
        output = output if isinstance(output, dict) else {}
        summary[node.node_key] = {
            "status": str(node.status or "").upper(),
            "quality_grade": output.get("quality_grade", _NOT_AVAILABLE),
            "summary": _first_present(output.get("summary"), output.get("rationale_summary"), output.get("market_read"), _NOT_AVAILABLE),
        }
    return summary


def _build_evidence_index(bundle: _RunBundle) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    frozen = bundle.frozen_evidence or {}
    frozen_input = _frozen_input(bundle)
    frozen_refs = [str(item) for item in frozen.get("evidence_refs") or [] if isinstance(item, str) and item]
    aliases: dict[str, set[str]] = {}

    def register(raw: Any) -> None:
        if not isinstance(raw, str) or not raw.strip():
            return
        raw = raw.strip()
        canonical = f"input.{raw}" if raw in frozen_refs and not raw.startswith("input.") else raw
        if raw.startswith("input.") and raw[6:] in frozen_refs:
            canonical = raw
        aliases.setdefault(canonical, set()).add(raw)
        if canonical.startswith("input."):
            aliases[canonical].add(canonical[6:])

    for ref in frozen_refs:
        register(ref)
    for claim in bundle.claims:
        for ref in claim.evidence_refs_json or []:
            register(ref)
    for artifact in bundle.artifacts:
        _collect_evidence_refs(_artifact_content(artifact), register)

    sources, source_lookup, quote_sources = _source_index(bundle, frozen_input)
    entries: list[dict[str, Any]] = []
    legacy_mapping: dict[str, str] = {}
    for number, canonical in enumerate(sorted(aliases), start=1):
        path = canonical
        value = _path_value(frozen_input, path[6:]) if path.startswith("input.") else _NOT_AVAILABLE
        category = _evidence_category(path)
        source_id = _source_for_path(path, source_lookup, quote_sources)
        quality = _evidence_quality(category, frozen_input, bundle)
        evidence_id = f"EV-{number:06d}"
        entry = {
            "evidence_id": evidence_id,
            "path": path,
            "category": category,
            "instrument": _instrument_for_path(path),
            "observed_at": _evidence_time(category, frozen_input, bundle),
            "fetched_at": _evidence_time(category, frozen_input, bundle),
            "source_id": source_id,
            "quality": quality,
            "provider_derived": bool(category == "market" and source_id != _NOT_AVAILABLE),
            "value_summary": _value_summary(value),
        }
        entries.append(entry)
        for alias in aliases[canonical]:
            legacy_mapping[alias] = evidence_id
    return (
        {
            "evidence_hash": _evidence_hash(bundle),
            "evidence": entries,
            "legacy_ref_to_evidence_id": dict(sorted(legacy_mapping.items())),
        },
        {"sources": sources},
        legacy_mapping,
    )


def _collect_evidence_refs(value: Any, register) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in {"evidence_refs", "evidence"} and isinstance(item, list):
                for ref in item:
                    register(ref)
            _collect_evidence_refs(item, register)
    elif isinstance(value, list):
        for item in value:
            _collect_evidence_refs(item, register)


def _source_index(
    bundle: _RunBundle,
    frozen_input: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    market = frozen_input.get("market") if isinstance(frozen_input.get("market"), dict) else _market_snapshot_payload(bundle)
    market = market if isinstance(market, dict) else {}
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    quote_provider: dict[str, tuple[str, str]] = {}

    def add(provider: Any, source_type: str = "quote", *, fallback: Any = False) -> tuple[str, str] | None:
        if provider in {None, ""}:
            return None
        clean_provider = str(provider)
        key = (clean_provider, source_type)
        candidates.setdefault(
            key,
            {
                "provider": clean_provider,
                "endpoint_profile": _NOT_AVAILABLE,
                "source_type": source_type,
                "fetched_at": market.get("captured_at") or _iso(bundle.run.market_snapshot_at),
                "observed_at": market.get("captured_at") or _iso(bundle.run.market_snapshot_at),
                "quality": market.get("quality_grade") or bundle.run.data_quality_grade or _NOT_AVAILABLE,
                "fallback": bool(fallback) if isinstance(fallback, bool) else False,
            },
        )
        return key

    for index, item in enumerate(market.get("source_chain") or []):
        if isinstance(item, dict):
            add(item.get("provider") or item.get("source") or item.get("name"), item.get("source_type") or "quote", fallback=item.get("fallback", index > 0))
        else:
            add(item, "quote", fallback=index > 0)
    quotes = market.get("quotes") if isinstance(market.get("quotes"), dict) else {}
    for code, quote in quotes.items():
        if isinstance(quote, dict):
            key = add(quote.get("provider") or quote.get("source"), quote.get("source_type") or "quote", fallback=quote.get("fallback", False))
            if key is not None:
                quote_provider[str(code)] = key
    lookup: dict[tuple[str, str], str] = {}
    sources: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(candidates), start=1):
        source_id = f"SRC-{index:03d}"
        lookup[key] = source_id
        sources.append({"source_id": source_id, **candidates[key]})
    quote_sources = {code: lookup[key] for code, key in quote_provider.items() if key in lookup}
    return sources, lookup, quote_sources


def _source_for_path(path: str, lookup: dict[tuple[str, str], str], quote_sources: dict[str, str]) -> str:
    match = re.search(r"(?:^|\.)quotes\.([^\.]+)", path)
    if match and match.group(1) in quote_sources:
        return quote_sources[match.group(1)]
    return next(iter(lookup.values()), _NOT_AVAILABLE)


def _path_value(root: Any, path: str) -> Any:
    value = root
    for part in (item for item in path.split(".") if item):
        if isinstance(value, dict):
            if part not in value:
                return _NOT_AVAILABLE
            value = value[part]
        elif isinstance(value, list):
            try:
                value = value[int(part)]
            except (TypeError, ValueError, IndexError):
                return _NOT_AVAILABLE
        else:
            return _NOT_AVAILABLE
    return value


def _evidence_category(path: str) -> str:
    normalized = path[6:] if path.startswith("input.") else path
    head = normalized.split(".", 1)[0]
    return {
        "market": "market",
        "snapshot": "portfolio",
        "portfolio_context": "portfolio",
        "candidate_context": "candidate",
        "recent_history": "history",
        "memory_context": "memory",
        "trigger_context": "trigger",
    }.get(head, "legacy")


def _instrument_for_path(path: str) -> str:
    match = re.search(r"(?<!\d)(\d{6}(?:\.(?:SH|SZ|BJ))?)(?!\d)", path, flags=re.IGNORECASE)
    return match.group(1).upper() if match else _NOT_AVAILABLE


def _evidence_time(category: str, frozen_input: dict[str, Any], bundle: _RunBundle) -> str | None:
    if category == "market":
        market = frozen_input.get("market") if isinstance(frozen_input.get("market"), dict) else {}
        return market.get("captured_at") or _iso(bundle.run.market_snapshot_at)
    if category == "portfolio":
        snapshot = frozen_input.get("snapshot") if isinstance(frozen_input.get("snapshot"), dict) else {}
        return snapshot.get("snapshot_time") or _iso(getattr(bundle.snapshot, "snapshot_time", None))
    return _iso(bundle.run.created_at)


def _evidence_quality(category: str, frozen_input: dict[str, Any], bundle: _RunBundle) -> str:
    if category == "market":
        market = frozen_input.get("market") if isinstance(frozen_input.get("market"), dict) else {}
        return str(market.get("quality_grade") or bundle.run.data_quality_grade or _NOT_AVAILABLE)
    return str(bundle.run.data_quality_grade or _NOT_AVAILABLE)


def _value_summary(value: Any) -> str:
    if value == _NOT_AVAILABLE:
        return _NOT_AVAILABLE
    text = canonical_json(_sanitize_for_export(value))
    return text if len(text) <= 500 else text[:497] + "..."


def _evidence_hash(bundle: _RunBundle) -> str:
    artifact = _latest_artifact(bundle.artifacts, "evidence_snapshot")
    return artifact.sha256 if artifact is not None else _NOT_AVAILABLE


def _claims_payload(bundle: _RunBundle, debate_type: str, mapping: dict[str, str]) -> dict[str, Any]:
    return {
        "claims": [
            {
                "claim_id": claim.claim_id,
                "speaker": claim.speaker,
                "stance": claim.stance,
                "statement": claim.statement,
                "confidence": claim.confidence,
                "status": claim.status,
                "target_claim_ids": list(claim.target_claim_ids_json or []),
                "evidence_refs": list(claim.evidence_refs_json or []),
                "evidence_ids": [mapping[ref] for ref in claim.evidence_refs_json or [] if isinstance(ref, str) and ref in mapping],
                "parent_claim_id": claim.parent_claim_id,
                "created_at": _iso(claim.created_at),
            }
            for claim in bundle.claims
            if claim.debate_type == debate_type
        ]
    }


def _claim_resolution_payload(bundle: _RunBundle) -> Any | None:
    artifact = _artifact_content(_latest_artifact(bundle.artifacts, "claims.resolutions"))
    if artifact is not None:
        return {"resolutions": artifact}
    value = bundle.workflow.get("investment_debate_state")
    if isinstance(value, dict) and isinstance(value.get("claim_resolver"), dict):
        return value["claim_resolver"]
    return None


def _decision_payload(bundle: _RunBundle, name: str) -> Any:
    workflow_keys = {
        "research_manager": ("research_manager_verdict",),
        "trader": ("trader_proposal",),
        "risk_manager": ("risk_revision",),
        "risk_synthesis": ("risk_synthesis",),
        "portfolio_manager": ("portfolio_manager_final",),
        "portfolio_decision_gate": ("decision_gate",),
    }
    node_keys = {
        "research_manager": ("research_manager",),
        "trader": ("trader", "trader_revision"),
        "risk_manager": ("risk_manager",),
        "risk_synthesis": ("risk_synthesis",),
        "portfolio_manager": ("portfolio_manager",),
        "portfolio_decision_gate": ("portfolio_decision_gate",),
    }
    for key in workflow_keys[name]:
        for source in (bundle.workflow, bundle.result):
            value = source.get(key) if isinstance(source, dict) else None
            if value is not None:
                return value
    artifact_map = _by_id(bundle.artifacts)
    for node_key in node_keys[name]:
        node = next((item for item in bundle.nodes if item.node_key == node_key), None)
        if node is None:
            continue
        value = _artifact_content(artifact_map.get(node.output_artifact_id))
        if value is not None:
            return value
    return _not_available(f"{name}_missing")


def _candidate_payload(bundle: _RunBundle) -> dict[str, Any]:
    return {
        "deterministic_candidate_result": bundle.workflow.get("candidate_context", _not_available("candidate_context_missing")),
        "llm_candidate_review": bundle.workflow.get("candidate_review", _not_available("candidate_review_missing")),
        "final_candidate_actions": bundle.result.get("candidate_actions", []),
        "final_candidates": bundle.result.get("candidates", []),
    }


def _final_decision_payload(bundle: _RunBundle) -> Any:
    artifact = _artifact_content(_latest_artifact(bundle.artifacts, "final_decision"))
    if artifact is not None:
        return artifact
    return bundle.result if bundle.result else _not_available("final_decision_missing")


def _failures_payload(bundle: _RunBundle) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    if bundle.run.error_code or bundle.run.error_message:
        failures.append(
            {
                "scope": "run",
                "status": bundle.run.status,
                "error_code": bundle.run.error_code,
                "error_message": bundle.run.error_message,
                "completed_at": _iso(bundle.run.completed_at),
            }
        )
    for stage in bundle.stages:
        if stage.error_code or stage.error_message or stage.status in {"failed", "blocked", "cancelled"}:
            failures.append({"scope": "stage", "phase_key": stage.phase_key, "status": stage.status, "error_code": stage.error_code, "error_message": stage.error_message, "completed_at": _iso(stage.completed_at)})
    for node in bundle.nodes:
        if node.error_code or node.error_message or node.status in {"failed", "blocked", "cancelled"}:
            failures.append({"scope": "node", "node_key": node.node_key, "status": node.status, "error_code": node.error_code, "error_message": node.error_message, "completed_at": _iso(node.completed_at)})
    for attempt in bundle.attempts:
        if attempt.status in {"failed", "cancelled"} or attempt.error_code or attempt.error_message:
            failures.append({"scope": "attempt", **_attempt_payload(attempt)})
    return {"failures": failures}


def _retries_payload(bundle: _RunBundle) -> dict[str, Any]:
    return {
        "retries": [
            _attempt_payload(attempt)
            for attempt in bundle.attempts
            if attempt.attempt_no > 1
            or int(attempt.transport_retry_count or 0) > 0
            or int(attempt.structured_retry_count or 0) > 0
            or attempt.status == "retry_waiting"
        ]
    }


def _summary_markdown(bundle: _RunBundle) -> str:
    final = _final_decision_payload(bundle)
    no_action = isinstance(final, dict) and (
        str(final.get("outcome") or "").upper() == "NO_ACTION"
        or str(final.get("final_rating") or "").lower() in {"no_action", "watch_only"}
    )
    lines = [
        "# Analysis Run Export",
        "",
        f"- Run ID: {bundle.run.id}",
        f"- Trading date: {_trading_date(bundle)}",
        f"- Status: {bundle.run.status}",
        f"- Workflow version: {bundle.run.workflow_version or _NOT_AVAILABLE}",
        f"- Data quality: {bundle.run.data_quality_grade or _NOT_AVAILABLE}",
        f"- NO_ACTION: {'yes' if no_action else 'no'}",
        f"- Blocked: {'yes' if bundle.run.status == 'blocked' else 'no'}",
    ]
    if bundle.run.summary:
        lines.extend(["", "## Summary", "", _safe_text(bundle.run.summary)])
    return "\n".join(lines) + "\n"


def _readme(bundle: _RunBundle, *, mode: str) -> str:
    final = _final_decision_payload(bundle)
    no_action = isinstance(final, dict) and (
        str(final.get("outcome") or "").upper() == "NO_ACTION"
        or str(final.get("final_rating") or "").lower() in {"no_action", "watch_only"}
    )
    return "\n".join(
        [
            "# Analysis Run Evidence Package",
            "",
            f"- Run ID: {bundle.run.id}",
            f"- Analysis date: {_trading_date(bundle)}",
            f"- Export mode: {mode}",
            f"- Workflow version: {bundle.run.workflow_version or _NOT_AVAILABLE}",
            f"- Run status: {bundle.run.status or _NOT_AVAILABLE}",
            f"- Data quality: {bundle.run.data_quality_grade or _NOT_AVAILABLE}",
            f"- NO_ACTION: {'yes' if no_action else 'no'}",
            f"- Blocked: {'yes' if bundle.run.status == 'blocked' else 'no'}",
            "",
            "## Contents",
            "",
            "- run/: run identity, workflow topology, safe configuration and timeline.",
            "- portfolio/ and market/: persisted snapshot evidence used by the run.",
            "- evidence/: evidence and source indexes plus the quality gate.",
            "- agents/, debate/ and decision/: public agent conclusions, claims and deterministic gate results.",
            "- errors/ and reports/: persisted failures, retries and public reports.",
            "",
            "## Modes",
            "",
            "Standard contains the durable public audit record. Debug adds persisted node inputs, redacted prompts, visible raw output, attempts, checkpoints and artifact indexes when available.",
            "",
            "## Security Policy",
            "",
            "Every file receives central redaction again during export. Secrets, credentials and authorization material are removed. Hidden chain-of-thought, provider reasoning and scratchpad fields are omitted. The package is assembled only from persisted historical records; it does not refetch market data, call a model, resume a run or modify run data.",
            "",
            "## Verification",
            "",
            "manifest.json lists each package file with its SHA-256 digest and byte size. manifest.json is intentionally excluded from that list to avoid recursive hashing. verify_export_package() checks every listed file and rejects missing, altered or unlisted files.",
            "",
        ]
    )


def _artifact_metadata(artifact: AnalysisArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "artifact_key": artifact.artifact_key,
        "persisted_sha256": artifact.sha256,
        "content_size": artifact.content_size,
        "redacted": bool(artifact.redacted),
        "content_encoding": artifact.content_encoding,
        "mime_type": artifact.mime_type,
        "stage_id": artifact.stage_id,
        "node_id": artifact.node_id,
        "attempt_id": artifact.attempt_id,
        "created_at": _iso(artifact.created_at),
    }


def _artifact_index_payload(bundle: _RunBundle) -> dict[str, Any]:
    return {"artifacts": [_artifact_metadata(item) for item in bundle.artifacts]}


def _raw_evidence_artifact_index(bundle: _RunBundle) -> dict[str, Any]:
    evidence_types = {ArtifactType.EVIDENCE, ArtifactType.INPUT, ArtifactType.MARKET_SNAPSHOT, ArtifactType.PORTFOLIO_SNAPSHOT}
    return {
        "artifacts": [
            _artifact_metadata(item)
            for item in bundle.artifacts
            if item.artifact_type in evidence_types
        ]
    }


def _prompt_manifest_payload(bundle: _RunBundle) -> Any:
    prompts = _frozen_contract(bundle).get("prompts")
    return prompts if isinstance(prompts, dict) else _not_available("prompt_manifest_missing")


def _checkpoint_payload(bundle: _RunBundle) -> dict[str, Any]:
    return {
        "checkpoints": [
            {"artifact": _artifact_metadata(item), "content": _artifact_content(item)}
            for item in bundle.artifacts
            if item.artifact_type == ArtifactType.CHECKPOINT
        ]
    }


def _raw_errors_payload(bundle: _RunBundle) -> dict[str, Any]:
    return {
        "errors": [
            {"artifact": _artifact_metadata(item), "content": _artifact_content(item)}
            for item in bundle.artifacts
            if item.artifact_type == ArtifactType.ERROR
        ]
    }


def _node_input_payload(node: AnalysisNode, node_artifacts: list[AnalysisArtifact], all_artifacts: list[AnalysisArtifact]) -> Any:
    artifact_map = _by_id(all_artifacts)
    source = artifact_map.get(node.input_artifact_id)
    if source is None:
        source = _latest_node_artifact(node_artifacts, node_id=node.id, artifact_type=ArtifactType.INPUT)
    content = _artifact_content(source)
    return content if content is not None else _not_available("node_input_missing")


def _node_artifact_or_missing(node_artifacts: list[AnalysisArtifact], artifact_type: str) -> Any:
    source = _latest_node_artifact(node_artifacts, node_id=node_artifacts[0].node_id, artifact_type=artifact_type) if node_artifacts else None
    content = _artifact_content(source)
    return content if content is not None else _not_available(f"{artifact_type.lower()}_missing")


def _node_output_payload(node: AnalysisNode, node_artifacts: list[AnalysisArtifact], all_artifacts: list[AnalysisArtifact]) -> Any:
    source = _by_id(all_artifacts).get(node.output_artifact_id)
    if source is None:
        source = _latest_node_artifact(node_artifacts, node_id=node.id, artifact_type=ArtifactType.STRUCTURED_OUTPUT)
    content = _artifact_content(source)
    return content if content is not None else _not_available("structured_output_missing")


def _node_raw_output(attempts: list[AnalysisNodeAttempt], artifacts: list[AnalysisArtifact]) -> str:
    artifact_map = _by_id(artifacts)
    chunks: list[str] = []
    for attempt in attempts:
        artifact = artifact_map.get(attempt.raw_output_artifact_id)
        content = _artifact_content(artifact)
        if content is None:
            continue
        chunks.append(f"Attempt {attempt.attempt_no}\n{_safe_text(content)}")
    return "\n\n".join(chunks) if chunks else "not_available\n"


def _stage_payload(stage: AnalysisStage) -> dict[str, Any]:
    return {
        "id": stage.id,
        "phase_key": stage.phase_key,
        "phase_order": stage.phase_order,
        "display_name": stage.display_name,
        "status": stage.status,
        "criticality": stage.criticality,
        "started_at": _iso(stage.started_at),
        "completed_at": _iso(stage.completed_at),
        "input_hash": stage.input_hash,
        "output_hash": stage.output_hash,
        "quality_grade": stage.quality_grade,
        "error_code": stage.error_code,
        "error_message": stage.error_message,
        "metadata": stage.metadata_json or {},
    }


def _node_payload(node: AnalysisNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "stage_id": node.stage_id,
        "node_key": node.node_key,
        "node_type": node.node_type,
        "agent_role": node.agent_role,
        "status": node.status,
        "criticality": node.criticality,
        "attempt_count": node.attempt_count,
        "max_attempts": node.max_attempts,
        "retryable": bool(node.retryable),
        "resumable": bool(node.resumable),
        "started_at": _iso(node.started_at),
        "completed_at": _iso(node.completed_at),
        "input_artifact_id": node.input_artifact_id,
        "output_artifact_id": node.output_artifact_id,
        "error_code": node.error_code,
        "error_message": node.error_message,
        "metadata": node.metadata_json or {},
    }


def _attempt_payload(attempt: AnalysisNodeAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "node_id": attempt.node_id,
        "stage_id": attempt.stage_id,
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "started_at": _iso(attempt.started_at),
        "completed_at": _iso(attempt.completed_at),
        "latency_ms": attempt.latency_ms,
        "provider": attempt.provider,
        "model": attempt.model,
        "model_profile_id": attempt.model_profile_id,
        "request_id": attempt.request_id,
        "input_hash": attempt.input_hash,
        "output_hash": attempt.output_hash,
        "input_tokens": attempt.input_tokens,
        "output_tokens": attempt.output_tokens,
        "transport_retry_count": attempt.transport_retry_count or 0,
        "structured_retry_count": attempt.structured_retry_count or 0,
        "failure_class": attempt.failure_class,
        "error_type": attempt.error_type,
        "error_code": attempt.error_code,
        "error_message": attempt.error_message,
        "retryable": bool(attempt.retryable),
        "raw_output_artifact_id": attempt.raw_output_artifact_id,
        "structured_output_artifact_id": attempt.structured_output_artifact_id,
        "metadata": attempt.metadata_json or {},
    }


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), None)


def _group_by(items: Iterable[Any], key) -> dict[Any, list[Any]]:
    grouped: dict[Any, list[Any]] = {}
    for item in items:
        grouped.setdefault(key(item), []).append(item)
    return grouped


__all__ = [
    "AnalysisExportError",
    "AnalysisExportSizeLimitExceeded",
    "AnalysisRunExporter",
    "ExportPackage",
    "verify_export_package",
]
