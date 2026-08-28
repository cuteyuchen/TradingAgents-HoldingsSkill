"""Authenticated sanitized diagnostic bundles."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import settings
from .health import operational_health, readiness
from .logging import redact_object, tail_logs
from .release import build_release_metadata
from .startup import collect_startup_recovery_report


def diagnostic_directory() -> Path:
    path = Path(settings.BACKUP_DIR).expanduser().resolve() / "diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _jobs_payload(db: Session) -> dict[str, Any]:
    inspector = inspect(db.get_bind())
    payload: dict[str, Any] = {
        "startup_recovery": collect_startup_recovery_report(db),
        "recent": {},
    }

    def query_rows(table: str, columns: str, order: str, limit: int = 30) -> list[dict[str, Any]]:
        try:
            rows = db.execute(
                text(f"SELECT {columns} FROM {table} ORDER BY {order} DESC LIMIT {limit}")
            ).mappings().all()
            return [dict(row) for row in rows]
        except Exception:  # noqa: BLE001
            return []

    if inspector.has_table("analysis_jobs"):
        payload["recent"]["analysis_jobs"] = query_rows(
            "analysis_jobs",
            "id, portfolio_id, mode, checkpoint, status, current_stage, progress_percent, "
            "error_code, attempt_count, created_at",
            "id",
        )
    if inspector.has_table("backtest_runs"):
        payload["recent"]["backtest_runs"] = query_rows(
            "backtest_runs",
            "id, portfolio_id, scope, replay_mode, status, current_stage, progress_percent, "
            "quality_status, leakage_status, error_code, attempt_count, created_at",
            "id",
        )
    if inspector.has_table("daily_operational_checkpoints"):
        payload["recent"]["checkpoints"] = query_rows(
            "daily_operational_checkpoints",
            "id, portfolio_id, trade_date, checkpoint_name, status, job_id, "
            "attempt_count, lease_expires_at, last_error",
            "id",
        )
    if inspector.has_table("operating_notifications"):
        payload["recent"]["notifications"] = query_rows(
            "operating_notifications",
            "notification_id, portfolio_id, event_type, severity, status, "
            "occurred_at, attempt_count",
            "id",
        )
    return payload


def build_diagnostic_bundle(db: Session | None = None) -> dict[str, Any]:
    owns_session = db is None
    if owns_session:
        from ..database import SessionLocal

        session = SessionLocal()
    else:
        session = db
    bundle_id = "diagnostics_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    directory = diagnostic_directory()
    filename = f"{bundle_id}.zip"
    zip_path = directory / filename
    try:
        release = redact_object(build_release_metadata(session))
        health = redact_object(operational_health(session))
        ready = redact_object(readiness(session, detailed=True))
        jobs = redact_object(_jobs_payload(session))
        logs = redact_text_join(tail_logs(settings.DIAGNOSTIC_LOG_LINES))
        files = {
            "manifest.json": {
                "bundle_id": bundle_id,
                "generated_at": datetime.now(UTC).isoformat(),
                "purpose": "sanitized_operations_diagnostic",
                "contains_db": False,
                "contains_backup": False,
                "redacted": True,
            },
            "release.json": release,
            "health.json": health,
            "readiness.json": ready,
            "jobs.json": jobs,
            "logs.txt": logs,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in files.items():
                content = (
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    if isinstance(payload, (dict, list))
                    else payload
                )
                archive.writestr(name, content.encode("utf-8"))
        data = buffer.getvalue()
        checksum = hashlib.sha256(data).hexdigest()
        zip_path.write_bytes(data)
        (directory / f"{bundle_id}.sha256").write_text(checksum, encoding="ascii")
        return {
            "bundle_id": bundle_id,
            "filename": filename,
            "sha256": checksum,
            "size": len(data),
            "entries": list(files),
            "contains_db": False,
            "contains_backup": False,
        }
    finally:
        if owns_session:
            session.close()


def redact_text_join(lines: list[str]) -> str:
    return "\n".join(redact_object(line) for line in lines)


def diagnostic_bundle_path(bundle_id: str) -> Path | None:
    if not bundle_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", bundle_id):
        return None
    path = diagnostic_directory() / f"{bundle_id}.zip"
    return path if path.is_file() else None


def diagnostic_bundle_metadata(bundle_id: str) -> dict[str, Any]:
    path = diagnostic_bundle_path(bundle_id)
    if path is None:
        raise FileNotFoundError("diagnostic_bundle_not_found")
    sha_path = path.with_suffix(".sha256")
    return {
        "bundle_id": bundle_id,
        "filename": path.name,
        "size": path.stat().st_size,
        "sha256": sha_path.read_text(encoding="ascii").strip() if sha_path.exists() else None,
    }


__all__ = [
    "build_diagnostic_bundle",
    "diagnostic_bundle_metadata",
    "diagnostic_bundle_path",
]
