"""Verified SQLite online backups with durable filesystem manifests."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import settings
from ..governance.service import GovernanceBlockedError, resolve_production_parameters
from .release import alembic_db_revision, code_head_revision, known_migration_revisions, schema_state

logger = logging.getLogger(__name__)

BACKUP_TYPES = frozenset({"MANUAL", "SCHEDULED", "PRE_UPGRADE", "PRE_RESTORE_SAFETY"})
REQUIRED_TABLES = (
    "users",
    "portfolios",
    "portfolio_snapshots",
    "analysis_jobs",
    "candidate_runs",
    "market_score_snapshots",
    "parameter_set_versions",
    "decision_memories",
    "daily_operational_runs",
)
_BACKUP_LOCK = threading.Lock()


class BackupError(RuntimeError):
    pass


class RestoreError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def backup_directory() -> Path:
    path = Path(settings.BACKUP_DIR).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        # Windows best effort; the documented deploy guidance keeps the backup
        # volume private at the Docker/OS level.
        pass
    return path


def _backend_directory() -> Path:
    return Path(__file__).resolve().parents[2]


def _quick_check(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        return {"ok": False, "result": "missing_or_empty"}
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            result = str(row[0]) if row else "error"
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "result": str(exc)}
    return {"ok": result == "ok", "result": result}


def _read_revision(path: Path) -> str | None:
    try:
        conn = sqlite3.connect(str(path))
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "alembic_version" not in tables:
                return None
            row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
            return str(row[0]) if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _required_tables_present(path: Path) -> dict[str, Any]:
    try:
        conn = sqlite3.connect(str(path))
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error:
        return {"missing": list(REQUIRED_TABLES), "ok": False}
    missing = [name for name in REQUIRED_TABLES if name not in tables]
    return {"missing": missing, "ok": not missing}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _active_parameter_values() -> dict[str, Any]:
    from ..database import SessionLocal

    try:
        with SessionLocal() as db:
            context = resolve_production_parameters(db)
            return {
                "active_parameter_set_version": context.get("version"),
                "active_parameter_set_hash": context.get("config_hash"),
            }
    except GovernanceBlockedError as exc:
        return {
            "active_parameter_set_version": None,
            "active_parameter_set_hash": None,
            "governance_status": "BLOCKED",
            "governance_reason": str(exc),
        }
    except Exception:  # noqa: BLE001
        return {
            "active_parameter_set_version": None,
            "active_parameter_set_hash": None,
            "governance_status": "UNKNOWN",
        }


def _manifest_path(backup_id: str) -> Path:
    return backup_directory() / f"{backup_id}.json"


def _database_path(backup_id: str) -> Path:
    return backup_directory() / f"{backup_id}.sqlite"


def _safe_backup_id(value: str) -> str:
    if not value or not all(char.isalnum() or char in {"_", "-"} for char in value):
        raise BackupError("invalid_backup_id")
    return value


def create_backup(
    *,
    reason: str = "MANUAL",
    backup_id: str | None = None,
) -> dict[str, Any]:
    """Create one verified SQLite online backup and a durable JSON manifest."""

    reason = str(reason or "MANUAL").upper()
    if reason not in BACKUP_TYPES:
        raise BackupError(f"unsupported_backup_type:{reason}")
    if not _BACKUP_LOCK.acquire(blocking=False):
        raise BackupError("BACKUP_ALREADY_RUNNING")
    started = _utc_now()
    source = Path(settings.DB_PATH).expanduser().resolve()
    directory = backup_directory()
    try:
        if not source.is_file() or source.stat().st_size <= 0:
            raise BackupError("source_database_missing")
        source_quick = _quick_check(source)
        if not source_quick["ok"]:
            raise BackupError(f"BLOCKED_DB_INTEGRITY:{source_quick['result']}")
        candidate_id = backup_id or (
            "backup_"
            + started.strftime("%Y%m%d_%H%M%S")
            + "_"
            + secrets.token_hex(4)
        )
        candidate_id = _safe_backup_id(candidate_id)
        partial_db = directory / f"{candidate_id}.sqlite.partial"
        final_db = _database_path(candidate_id)
        partial_manifest = directory / f"{candidate_id}.json.partial"
        final_manifest = _manifest_path(candidate_id)
        if final_db.exists() or final_manifest.exists():
            raise BackupError("backup_id_already_exists")
        try:
            src_conn = sqlite3.connect(str(source))
            dest_conn = sqlite3.connect(str(partial_db))
            try:
                src_conn.backup(dest_conn)
            finally:
                dest_conn.close()
                src_conn.close()
            partial_quick = _quick_check(partial_db)
            if not partial_quick["ok"]:
                raise BackupError(f"backup_quick_check_failed:{partial_quick['result']}")
            partial_revision = _read_revision(partial_db)
            source_fingerprint = _sha256(source)
            backup_sha = _sha256(partial_db)
            manifest = {
                "manifest_version": "1.0",
                "backup_id": candidate_id,
                "filename": final_db.name,
                "type": reason,
                "reason": reason,
                "status": "VERIFIED",
                "created_at": started.isoformat(),
                "verified_at": _utc_now().isoformat(),
                "completed_at": _utc_now().isoformat(),
                "source_db_revision": partial_revision,
                "code_head_revision": code_head_revision(),
                "app_version": settings.APP_VERSION,
                "git_sha": settings.APP_GIT_SHA or "UNKNOWN",
                "runtime_contract_version": "2.4.0",
                "decision_contract_version": "2.4.0",
                "source_db_size": source.stat().st_size,
                "backup_size": partial_db.stat().st_size,
                "sha256": backup_sha,
                "quick_check_result": partial_quick["result"],
                "source_db_fingerprint": source_fingerprint,
                "governance": _active_parameter_values(),
            }
            with partial_manifest.open("w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(partial_db, final_db)
            try:
                os.chmod(final_db, 0o600)
            except OSError:
                pass
            final_quick = _quick_check(final_db)
            if not final_quick["ok"]:
                raise BackupError(f"final_backup_integrity_failed:{final_quick['result']}")
            if _sha256(final_db) != backup_sha:
                raise BackupError("final_checksum_mismatch")
            os.replace(partial_manifest, final_manifest)
            try:
                os.chmod(final_manifest, 0o600)
            except OSError:
                pass
            logger.info(
                "backup_success id=%s type=%s bytes=%s sha256=%s",
                candidate_id,
                reason,
                manifest["backup_size"],
                backup_sha[:12],
            )
            return manifest
        except Exception:
            for path in (partial_db, partial_manifest, final_db, final_manifest):
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
            raise
    finally:
        _BACKUP_LOCK.release()


def load_manifest(backup_id: str) -> dict[str, Any]:
    backup_id = _safe_backup_id(backup_id)
    path = _manifest_path(backup_id)
    if not path.is_file():
        raise BackupError("backup_not_found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("backup_manifest_corrupt") from exc
    if not isinstance(payload, dict) or payload.get("backup_id") != backup_id:
        raise BackupError("backup_manifest_mismatch")
    return payload


def list_backups(limit: int = 200) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for path in backup_directory().glob("*.json"):
        if path.name.endswith(".partial"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("backup_id")
            and str(payload.get("status") or "").upper() == "VERIFIED"
        ):
            manifests.append(payload)
    manifests.sort(key=lambda item: str(item.get("completed_at") or item.get("created_at") or ""), reverse=True)
    return manifests[: max(1, min(int(limit), 1000))]


def verify_backup(backup_id: str) -> dict[str, Any]:
    manifest = load_manifest(backup_id)
    db_path = backup_directory() / manifest["filename"]
    if not db_path.is_file():
        raise BackupError("backup_file_missing")
    checksum = _sha256(db_path)
    checksum_matches = checksum == str(manifest.get("sha256") or "")
    quick = _quick_check(db_path)
    tables = _required_tables_present(db_path)
    revision = _read_revision(db_path)
    verified = (
        str(manifest.get("status") or "").upper() == "VERIFIED"
        and checksum_matches
        and quick["ok"]
        and revision is not None
    )
    return {
        "backup_id": backup_id,
        "verified": verified,
        "checksum_matches": checksum_matches,
        "sha256": checksum,
        "quick_check": quick,
        "required_tables": tables,
        "alembic_revision": revision,
        "manifest": manifest,
    }


def validate_backup_for_restore(
    backup_id: str,
    *,
    code_head: str | None = None,
) -> dict[str, Any]:
    verified = verify_backup(backup_id)
    if not verified["verified"]:
        raise RestoreError("backup_not_verified")
    backup_revision = verified["alembic_revision"]
    head = code_head or code_head_revision()
    revisions = known_migration_revisions()
    note: str | None = None
    if backup_revision is None:
        raise RestoreError("BACKUP_REVISION_UNKNOWN")
    if head and backup_revision == head:
        compatible = True
    elif backup_revision in revisions:
        compatible = True
        note = "backup_revision_behind_code_head_upgrade_required"
    elif head and backup_revision > head:
        raise RestoreError("BACKUP_REVISION_AHEAD")
    else:
        raise RestoreError("BACKUP_REVISION_UNKNOWN")
    verified["restore_compatible"] = compatible
    verified["restore_note"] = note
    return verified


def restore_drill(backup_id: str, *, cleanup: bool = True) -> dict[str, Any]:
    """Restore a verified backup into a temporary database and inspect it."""

    verified = validate_backup_for_restore(backup_id)
    directory = backup_directory() / "drills"
    directory.mkdir(parents=True, exist_ok=True)
    temp_path = directory / f"restore-drill-{backup_id}-{_utc_now().strftime('%H%M%S%f')}.sqlite"
    source_path = backup_directory() / verified["manifest"]["filename"]
    head = code_head_revision()
    upgrade_error: str | None = None
    try:
        src_conn = sqlite3.connect(str(source_path))
        dest_conn = sqlite3.connect(str(temp_path))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            src_conn.close()
        revision = _read_revision(temp_path)
        if revision != head:
            env = os.environ.copy()
            env["ADVISOR_DB_PATH"] = str(temp_path)
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=str(_backend_directory()),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                upgrade_error = (result.stderr or result.stdout or "alembic_upgrade_failed")[-500:]
            revision = _read_revision(temp_path)
        quick = _quick_check(temp_path)
        tables = _required_tables_present(temp_path)
        smoke: dict[str, Any] = {}
        governance: dict[str, Any] = {
            "status": "UNKNOWN",
            "reasons": [],
            "active_count": 0,
            "active": None,
        }
        from sqlalchemy import create_engine, func, select, table as sql_table
        from sqlalchemy.orm import Session as SqlSession

        engine = create_engine(f"sqlite:///{temp_path}")
        try:
            with SqlSession(engine) as session:
                from ..governance.models import ParameterSetVersion
                from ..governance.service import bootstrap_parameter_set, governance_health

                bootstrap_parameter_set(session)
                health = governance_health(session)
                active_count = session.execute(
                    select(func.count())
                    .select_from(ParameterSetVersion)
                    .where(ParameterSetVersion.status == "ACTIVE")
                ).scalar_one()
                governance = {
                    "status": health["status"],
                    "reasons": health.get("reasons") or [],
                    "active_count": int(active_count),
                    "active": health.get("active"),
                }
                for table, label in (
                    ("portfolio_snapshots", "portfolio_snapshot_count"),
                    ("market_score_snapshots", "market_score_snapshot_count"),
                    ("candidate_runs", "candidate_run_count"),
                ):
                    try:
                        smoke[label] = int(
                            session.execute(select(func.count()).select_from(
                                sql_table(table)
                            )).scalar_one()
                        )
                    except Exception:  # noqa: BLE001
                        smoke[label] = None
        except Exception as exc:  # noqa: BLE001
            governance = {
                "status": "ERROR",
                "reasons": [str(exc)],
                "active_count": 0,
                "active": None,
            }
        finally:
            engine.dispose()
        passed = (
            upgrade_error is None
            and quick["ok"]
            and tables["ok"]
            and revision == head
            and governance["status"] not in {"BLOCKED", "ERROR"}
            and governance["active_count"] == 1
        )
        result = {
            "backup_id": backup_id,
            "status": "PASS" if passed else "FAILED",
            "quick_check": quick,
            "required_tables": tables,
            "alembic_revision": revision,
            "alembic_upgrade": (
                "not_required"
                if upgrade_error is None and revision == verified["alembic_revision"]
                else "ok" if upgrade_error is None else f"FAILED:{upgrade_error}"
            ),
            "governance": governance,
            "smoke": smoke,
            "production_db_untouched": True,
        }
        logger.info("restore_drill id=%s status=%s", backup_id, result["status"])
        return result
    finally:
        if cleanup:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("restore drill temp cleanup failed: %s", temp_path)


def backup_freshness() -> dict[str, Any]:
    backups = list_backups()
    if not backups:
        return {
            "status": "BLOCKED",
            "reason": "NO_VERIFIED_BACKUP",
            "last_success_at": None,
            "age_hours": None,
            "backup_count": 0,
            "latest": None,
        }
    latest: dict[str, Any] | None = None
    for candidate in backups[:5]:
        try:
            check = verify_backup(str(candidate["backup_id"]))
        except BackupError:
            continue
        if check["verified"]:
            latest = candidate
            break
    if latest is None:
        return {
            "status": "BLOCKED",
            "reason": "NO_VERIFIED_BACKUP",
            "last_success_at": None,
            "age_hours": None,
            "backup_count": len(backups),
            "latest": None,
        }
    completed = latest.get("completed_at") or latest.get("created_at")
    try:
        age_hours = max(0.0, (_utc_now() - datetime.fromisoformat(completed)).total_seconds() / 3600)
    except (TypeError, ValueError):
        age_hours = None
    if age_hours is None:
        status = "UNKNOWN"
    elif age_hours > settings.BACKUP_BLOCKED_HOURS:
        status = "BLOCKED"
    elif age_hours > settings.BACKUP_DEGRADED_HOURS:
        status = "DEGRADED"
    else:
        status = "OK"
    return {
        "status": status,
        "last_success_at": completed,
        "age_hours": age_hours,
        "backup_count": len(backups),
        "latest": latest,
    }


def retention_cleanup() -> dict[str, Any]:
    """Remove expired scheduled backups while never deleting the last backup."""

    backups = list_backups(limit=1000)
    removed: list[str] = []
    limits = {
        "DAILY": settings.BACKUP_RETENTION_DAILY,
        "WEEKLY": settings.BACKUP_RETENTION_WEEKLY,
        "PRE_UPGRADE": settings.BACKUP_RETENTION_PRE_UPGRADE,
    }
    if not backups:
        return {"removed": removed, "remaining": 0}
    candidates = list(backups)

    def _delete(item: dict[str, Any]) -> None:
        remaining = [row for row in candidates if str(row["backup_id"]) not in removed]
        if len(remaining) <= 1:
            return
        backup_id = str(item["backup_id"])
        try:
            _database_path(backup_id).unlink(missing_ok=True)
            _manifest_path(backup_id).unlink(missing_ok=True)
            removed.append(backup_id)
        except OSError as exc:
            logger.warning("backup retention cleanup failed id=%s: %s", backup_id, exc)

    def _date_key(item: dict[str, Any]) -> Any:
        value = item.get("completed_at") or item.get("created_at") or ""
        try:
            return datetime.fromisoformat(value).date()
        except (TypeError, ValueError):
            return None

    for backup_type, limit in limits.items():
        if limit <= 0:
            continue
        grouped = [item for item in candidates if str(item.get("type") or "").upper() == backup_type]
        if len(grouped) <= limit:
            continue
        # Never remove the single newest verified backup.
        keep_until = max(1, min(limit, len(grouped) - 1))
        for item in grouped[keep_until:]:
            _delete(item)

    scheduled = [item for item in candidates if str(item.get("type") or "").upper() == "SCHEDULED"]
    if scheduled and settings.BACKUP_RETENTION_DAILY > 0:
        by_date: dict[Any, list[dict[str, Any]]] = {}
        for item in scheduled:
            by_date.setdefault(_date_key(item), []).append(item)
        for items in by_date.values():
            items.sort(key=lambda row: str(row.get("completed_at") or row.get("created_at") or ""), reverse=True)
            for item in items[1:]:
                _delete(item)
        candidates = [row for row in backups if str(row["backup_id"]) not in removed]
        dates = sorted(
            {_date_key(row) for row in candidates if str(row.get("type") or "").upper() == "SCHEDULED"},
            reverse=True,
        )
        keep_dates = set(dates[: max(1, min(settings.BACKUP_RETENTION_DAILY, len(dates) - 1))]) if dates else set()
        for item in candidates:
            if (
                str(item.get("type") or "").upper() == "SCHEDULED"
                and _date_key(item) not in keep_dates
            ):
                _delete(item)

    scheduled = [row for row in backups if str(row.get("type") or "").upper() == "SCHEDULED" and str(row["backup_id"]) not in removed]
    if scheduled and settings.BACKUP_RETENTION_WEEKLY > 0:
        by_week: dict[Any, list[dict[str, Any]]] = {}
        for item in scheduled:
            day = _date_key(item)
            by_week.setdefault(day.isocalendar()[:2] if day else None, []).append(item)
        for items in by_week.values():
            items.sort(key=lambda row: str(row.get("completed_at") or row.get("created_at") or ""), reverse=True)
            for item in items[1:]:
                _delete(item)
        candidates = [row for row in backups if str(row["backup_id"]) not in removed]
        week_values: list[Any] = []
        for row in candidates:
            if str(row.get("type") or "").upper() != "SCHEDULED":
                continue
            day = _date_key(row)
            if day is not None:
                week_values.append(day.isocalendar()[:2])
        weeks = sorted(set(week_values), reverse=True)
        keep_weeks = set(weeks[: max(1, min(settings.BACKUP_RETENTION_WEEKLY, len(weeks) - 1))]) if weeks else set()
        for item in candidates:
            day = _date_key(item)
            if (
                str(item.get("type") or "").upper() == "SCHEDULED"
                and day is not None
                and day.isocalendar()[:2] not in keep_weeks
            ):
                _delete(item)
    return {"removed": removed, "remaining": len(list_backups())}


def requires_pre_upgrade_backup(db: Any = None) -> bool:
    source = Path(settings.DB_PATH).expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        return False
    if db is not None:
        db_revision = alembic_db_revision(db)
    else:
        db_revision = _read_revision(source)
    state = schema_state(db_revision=db_revision)
    return state["state"] != "CURRENT"


def ensure_pre_upgrade_backup(db: Any = None) -> dict[str, Any] | None:
    if not requires_pre_upgrade_backup(db):
        return None
    return create_backup(reason="PRE_UPGRADE")


def run_scheduled_system_maintenance() -> dict[str, Any]:
    """Daily maintenance hook used by the single embedded scheduler."""

    from ..database import SessionLocal

    result: dict[str, Any] = {"backup": None, "retention": None, "quick_check": None}
    with SessionLocal() as db:
        if settings.BACKUP_SCHEDULE_ENABLED:
            try:
                result["backup"] = create_backup(reason="SCHEDULED")
            except BackupError as exc:
                logger.error("scheduled backup failed: %s", exc)
                result["backup"] = {"status": "FAILED", "reason": str(exc)}
        if settings.SYSTEM_MAINTENANCE_QUICK_CHECK_ENABLED:
            from .health import run_quick_check

            result["quick_check"] = run_quick_check(db)
    result["retention"] = retention_cleanup()
    return result


def offline_restore(
    *,
    backup_id: str,
    target: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Offline-only production restore boundary. Never exposed over HTTP."""

    if not confirmed:
        raise RestoreError("restore_requires_confirmation")
    target_path = Path(target).expanduser().resolve()
    if not target_path.parent.exists():
        raise RestoreError("restore_target_directory_missing")
    validated = validate_backup_for_restore(backup_id)
    source_path = backup_directory() / validated["manifest"]["filename"]
    if target_path.exists():
        create_backup(reason="PRE_RESTORE_SAFETY")
    partial = target_path.with_suffix(target_path.suffix + ".partial")
    shutil.copyfile(source_path, partial)
    quick = _quick_check(partial)
    if not quick["ok"]:
        partial.unlink(missing_ok=True)
        raise RestoreError(f"restore_partial_integrity_failed:{quick['result']}")
    os.replace(partial, target_path)
    return {
        "status": "RESTORED",
        "backup_id": backup_id,
        "target": str(target_path),
        "alembic_revision": validated["alembic_revision"],
        "operator_startup_preflight_required": True,
    }


__all__ = [
    "BackupError",
    "RestoreError",
    "backup_directory",
    "backup_freshness",
    "create_backup",
    "ensure_pre_upgrade_backup",
    "list_backups",
    "load_manifest",
    "offline_restore",
    "requires_pre_upgrade_backup",
    "restore_drill",
    "retention_cleanup",
    "run_scheduled_system_maintenance",
    "validate_backup_for_restore",
    "verify_backup",
]
