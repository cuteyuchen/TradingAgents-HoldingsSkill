"""Liveness, readiness, and operational health without LLM or network work."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from .backup import backup_freshness
from .release import alembic_db_revision, schema_state
from .tables import table_exists

_QUICK_CHECK_TTL_SECONDS = 6 * 3600
_QUICK_CHECK_CACHE: dict[str, Any] = {
    "checked_at": None,
    "source_path": None,
    "source_size": None,
    "source_mtime": None,
    "result": None,
}


class RuntimeNotReadyError(RuntimeError):
    """Raised when readiness hard blockers forbid new risk-increasing work."""


def liveness() -> dict[str, Any]:
    return {"status": "ok", "time": datetime.now(UTC).isoformat()}


def run_quick_check(db: Session) -> dict[str, Any]:
    """Run one heavy PRAGMA quick_check and cache it by DB file identity."""

    try:
        quick = db.execute(text("PRAGMA quick_check")).scalar()
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "result": f"error:{exc}"}
    else:
        result = {"ok": str(quick or "").lower() == "ok", "result": str(quick or "error")}
    path = Path(settings.DB_PATH).expanduser().resolve()
    try:
        stat = path.stat()
        size, mtime = stat.st_size, stat.st_mtime
    except OSError:
        size = mtime = None
    _QUICK_CHECK_CACHE.update(
        {
            "checked_at": datetime.now(UTC),
            "source_path": str(path),
            "source_size": size,
            "source_mtime": mtime,
            "result": result,
        }
    )
    return result


def _cached_quick_check() -> dict[str, Any] | None:
    path = Path(settings.DB_PATH).expanduser().resolve()
    try:
        stat = path.stat()
    except OSError:
        return None
    cache = _QUICK_CHECK_CACHE
    checked_at = cache.get("checked_at")
    if (
        checked_at is None
        or cache.get("source_path") != str(path)
        or cache.get("source_size") != stat.st_size
        or cache.get("source_mtime") != stat.st_mtime
        or (datetime.now(UTC) - checked_at).total_seconds() > _QUICK_CHECK_TTL_SECONDS
    ):
        return None
    return dict(cache["result"]) if cache.get("result") is not None else None


def disk_status() -> dict[str, Any]:
    directory = Path(settings.BACKUP_DIR).expanduser().resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(directory)
        free_ratio = usage.free / usage.total if usage.total else 0.0
    except OSError as exc:
        return {
            "status": "BLOCKED",
            "reason": f"disk_unavailable:{exc}",
            "free_ratio": None,
            "free_bytes": None,
            "total_bytes": None,
        }
    if free_ratio <= settings.DISK_BLOCKED_RATIO:
        status = "BLOCKED"
    elif free_ratio <= settings.DISK_DEGRADED_RATIO:
        status = "DEGRADED"
    else:
        status = "OK"
    return {
        "status": status,
        "free_ratio": round(free_ratio, 6),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
    }


def database_status(db: Session, *, check_writable: bool = True) -> dict[str, Any]:
    path = Path(settings.DB_PATH).expanduser().resolve()
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": f"db_unavailable:{exc}", "quick_check": None}
    quick = _cached_quick_check()
    quick_ok = bool(quick is not None and quick["ok"])
    writable = None
    if check_writable and path.is_file():
        try:
            with sqlite3.connect(str(path), timeout=5) as probe_conn:
                probe_conn.execute("CREATE TABLE advisor_write_probe_tmp(id INTEGER)")
                probe_conn.execute("DROP TABLE advisor_write_probe_tmp")
            writable = True
        except Exception:  # noqa: BLE001
            writable = False
    required = (
        "users",
        "portfolios",
        "analysis_jobs",
        "parameter_set_versions",
        "market_score_snapshots",
        "candidate_runs",
        "decision_memories",
        "daily_operational_runs",
    )
    present = {name for name in required if table_exists(db, name)}
    missing = sorted(set(required) - present)
    status = "OK"
    reason = None
    if quick is not None and not quick_ok:
        status, reason = "BLOCKED", "DB_QUICK_CHECK_FAILED"
    elif missing:
        status, reason = "BLOCKED", f"MISSING_TABLES:{','.join(missing)}"
    elif writable is False:
        status, reason = "BLOCKED", "DB_NOT_WRITABLE"
    size = wal_size = shm_size = None
    try:
        size = path.stat().st_size if path.is_file() else None
        wal = Path(str(path) + "-wal")
        shm = Path(str(path) + "-shm")
        wal_size = wal.stat().st_size if wal.is_file() else None
        shm_size = shm.stat().st_size if shm.is_file() else None
    except OSError:
        pass
    return {
        "status": status,
        "reason": reason,
        "quick_check": str(quick["result"] if quick is not None else "not_recently_checked"),
        "quick_check_source": "cache" if quick is not None else "deferred",
        "writable": writable,
        "required_tables_present": not missing,
        "missing_tables": missing,
        "db_size": size,
        "wal_size": wal_size,
        "shm_size": shm_size,
    }


def schema_status(db: Session) -> dict[str, Any]:
    state = schema_state(db_revision=alembic_db_revision(db))
    state_map = {
        "CURRENT": "OK",
        "BEHIND": "BLOCKED",
        "AHEAD": "BLOCKED",
        "BROKEN": "BLOCKED",
        "UNKNOWN": "UNKNOWN",
    }
    return {
        "status": state_map.get(state["state"], "UNKNOWN"),
        "state": state["state"],
        "reason": state["reason"],
        "db_revision": state["db_revision"],
        "code_head_revision": state["code_head_revision"],
    }


def governance_status(db: Session) -> dict[str, Any]:
    from ..governance.service import governance_health

    try:
        if not table_exists(db, "parameter_set_versions"):
            return {
                "status": "DEGRADED",
                "reasons": ["LEGACY_PRE_GOVERNANCE"],
                "active_version": None,
                "active_version_id": None,
            }
        health = governance_health(db)
        return {
            "status": health["status"],
            "reasons": health.get("reasons") or [],
            "active_version": (health.get("active") or {}).get("version"),
            "active_version_id": (health.get("active") or {}).get("id"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reasons": [f"GOVERNANCE_UNAVAILABLE:{exc}"]}


def scheduler_status() -> dict[str, Any]:
    from ..services.scheduler import scheduler_running

    if not settings.SCHEDULER_ENABLED:
        return {"status": "OK", "reason": "scheduler_disabled_by_config"}
    if scheduler_running():
        return {"status": "OK", "reason": None}
    return {"status": "BLOCKED", "reason": "scheduler_not_running"}


def monitor_status() -> dict[str, Any]:
    from ..services.realtime_monitor import get_realtime_monitor

    try:
        monitor = get_realtime_monitor()
        state = monitor.status()
        running = bool(state.get("status") == "running")
    except Exception:  # noqa: BLE001
        return {"status": "UNKNOWN", "reason": "monitor_unavailable"}
    if not settings.REALTIME_MONITOR_ENABLED:
        return {"status": "OK", "reason": "monitor_disabled_by_config"}
    return {
        "status": "OK" if running else "DEGRADED",
        "reason": None if running else state.get("last_error") or "monitor_not_running",
        "last_tick_at": state.get("last_tick_at"),
        "last_success_at": state.get("last_success_at"),
    }


def worker_recovery_status(db: Session) -> dict[str, Any]:
    from .startup import collect_startup_recovery_report

    report = collect_startup_recovery_report(db)
    stale_total = sum(report["counts"].values())
    if report["errors"]:
        return {"status": "BLOCKED", "reasons": report["errors"], "counts": report["counts"]}
    return {
        "status": "DEGRADED" if stale_total else "OK",
        "reasons": ["STALE_DURABLE_JOBS"] if stale_total else [],
        "counts": report["counts"],
    }


def _shadow_db_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _shadow_db_datetime_text(value: Any) -> str | None:
    parsed = _shadow_db_datetime(value)
    return parsed.isoformat() if parsed is not None else (str(value) if value else None)


def shadow_status(db: Session) -> dict[str, Any]:
    """Return aggregate shadow health without exposing portfolio details."""
    required = (
        "shadow_accounts",
        "shadow_order_intents",
        "live_decision_outcomes",
        "shadow_daily_snapshots",
    )
    missing = sorted(name for name in required if not table_exists(db, name))
    empty: dict[str, Any] = {
        "schema_installed": not missing,
        "active_shadow_accounts": 0,
        "active_generation_ids": [],
        "pending_intents": 0,
        "blocked_intents": 0,
        "expired_pending_intents": 0,
        "failed_evaluations": 0,
        "oldest_pending_created_at": None,
        "oldest_pending_age_seconds": None,
        "last_daily_snapshot": None,
        "last_validation_at": None,
        "maintenance_authority": "existing_scheduler",
    }
    if missing:
        return {
            "status": "DEGRADED",
            "reason": "shadow_schema_not_installed",
            "missing_tables": missing,
            **empty,
        }

    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        active_accounts = int(db.execute(text(
            "SELECT COUNT(*) FROM shadow_accounts WHERE status = 'ACTIVE'"
        )).scalar_one() or 0)
        active_generations = [
            int(value)
            for value in db.execute(text(
                "SELECT shadow_generation FROM shadow_accounts "
                "WHERE status = 'ACTIVE' ORDER BY id"
            )).scalars().all()
            if value is not None
        ]
        pending = int(db.execute(text(
            "SELECT COUNT(*) FROM shadow_order_intents "
            "WHERE status IN ('PENDING', 'PARTIAL')"
        )).scalar_one() or 0)
        blocked = int(db.execute(text(
            "SELECT COUNT(*) FROM shadow_order_intents WHERE status = 'BLOCKED'"
        )).scalar_one() or 0)
        expired_pending = int(db.execute(text(
            "SELECT COUNT(*) FROM shadow_order_intents "
            "WHERE status IN ('PENDING', 'PARTIAL') AND expires_at < :now"
        ), {"now": now}).scalar_one() or 0)
        failed_evaluations = int(db.execute(text(
            "SELECT COUNT(*) FROM live_decision_outcomes "
            "WHERE status IN ('FAILED', 'ERROR')"
        )).scalar_one() or 0)
        oldest_pending = db.execute(text(
            "SELECT MIN(created_at) FROM shadow_order_intents "
            "WHERE status IN ('PENDING', 'PARTIAL')"
        )).scalar_one_or_none()
        last_snapshot = db.execute(text(
            "SELECT trade_date, created_at FROM shadow_daily_snapshots "
            "ORDER BY trade_date DESC, created_at DESC LIMIT 1"
        )).mappings().first()
        last_validation = db.execute(text(
            "SELECT MAX(computed_at) FROM live_decision_outcomes "
            "WHERE computed_at IS NOT NULL"
        )).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        return {
            "status": "DEGRADED",
            "reason": "shadow_health_unavailable",
            "missing_tables": [],
            **empty,
        }

    oldest_dt = _shadow_db_datetime(oldest_pending)
    oldest_age = max(0.0, (now - oldest_dt).total_seconds()) if oldest_dt else None
    reasons: list[str] = []
    if expired_pending:
        reasons.append("EXPIRED_PENDING_INTENTS")
    if failed_evaluations:
        reasons.append("FAILED_OUTCOME_EVALUATIONS")
    return {
        "status": "DEGRADED" if reasons else "OK",
        "reason": ";".join(reasons) if reasons else None,
        "missing_tables": [],
        "schema_installed": True,
        "active_shadow_accounts": active_accounts,
        "active_generation_ids": active_generations,
        "pending_intents": pending,
        "blocked_intents": blocked,
        "expired_pending_intents": expired_pending,
        "failed_evaluations": failed_evaluations,
        "oldest_pending_created_at": _shadow_db_datetime_text(oldest_pending),
        "oldest_pending_age_seconds": round(oldest_age, 3) if oldest_age is not None else None,
        "last_daily_snapshot": {
            "trade_date": str(last_snapshot["trade_date"]) if last_snapshot else None,
            "created_at": _shadow_db_datetime_text(last_snapshot["created_at"]) if last_snapshot else None,
        } if last_snapshot else None,
        "last_validation_at": _shadow_db_datetime_text(last_validation),
        "maintenance_authority": "existing_scheduler",
    }


def backup_status() -> dict[str, Any]:
    freshness = backup_freshness()
    return {
        "status": freshness["status"],
        "last_success_at": freshness["last_success_at"],
        "age_hours": freshness["age_hours"],
        "backup_count": freshness["backup_count"],
    }


def require_runtime_ready_for_risk_work(db: Session | None = None) -> dict[str, Any]:
    """Fail closed before creating new risk-increasing analysis or candidate work."""

    owns_session = db is None
    if owns_session:
        from ..database import SessionLocal

        session = SessionLocal()
    else:
        session = db
    try:
        checks = {
            "schema": schema_status(session),
            "governance": governance_status(session),
            "database": database_status(session, check_writable=False),
            "storage": disk_status(),
        }
        blocked = {
            key: value
            for key, value in checks.items()
            if str(value.get("status") or "").upper() == "BLOCKED"
        }
        if blocked:
            reasons = ", ".join(
                f"{key}={value.get('reason') or value.get('reasons') or value.get('state') or value.get('status')}"
                for key, value in sorted(blocked.items())
            )
            raise RuntimeNotReadyError(f"RUNTIME_NOT_READY:{reasons}")
        return checks
    finally:
        if owns_session:
            session.close()


def readiness(db: Session | None = None, *, detailed: bool = False) -> dict[str, Any]:
    owns_session = db is None
    if owns_session:
        from ..database import SessionLocal

        session = SessionLocal()
    else:
        session = db
    try:
        from .startup import startup_preflight_completed, startup_preflight_state

        checks = {
            "database": database_status(session),
            "storage": disk_status(),
            "schema": schema_status(session),
            "governance": governance_status(session),
            "scheduler": scheduler_status(),
            "worker_recovery": worker_recovery_status(session),
            "backup": backup_status(),
            "preflight": {
                "status": "OK" if startup_preflight_completed() else "UNKNOWN",
                "reason": None if startup_preflight_completed() else "startup_preflight_not_completed",
                "blocked": startup_preflight_state().get("blocked", False),
            },
        }
        blocked = any(
            str(checks[key].get("status") or "").upper() == "BLOCKED"
            for key in ("database", "storage", "schema", "governance", "scheduler", "preflight")
            if key in checks
        )
        warnings = [
            key
            for key, value in checks.items()
            if str(value.get("status") or "").upper() == "DEGRADED"
        ]
        status = "BLOCKED" if blocked else "READY_WITH_WARNINGS" if warnings else "READY"
        payload: dict[str, Any] = {
            "status": status,
            "ready": not blocked,
            "checks": checks if detailed else {
                key: {"status": value["status"]} for key, value in checks.items()
            },
        }
        return payload
    finally:
        if owns_session:
            session.close()


def operational_health(db: Session | None = None) -> dict[str, Any]:
    owns_session = db is None
    if owns_session:
        from ..database import SessionLocal

        session = SessionLocal()
    else:
        session = db
    try:
        components: dict[str, Any] = {
            "release": {
                "status": "OK",
                "detail": {
                    "schema": schema_status(session)["state"],
                    "governance": governance_status(session)["status"],
                },
            },
            "database": database_status(session),
            "storage": disk_status(),
            "backup": backup_status(),
            "governance": governance_status(session),
            "scheduler": scheduler_status(),
            "realtime_monitor": monitor_status(),
            "worker_recovery": worker_recovery_status(session),
            "shadow": shadow_status(session),
        }
        severity = {"OK": 0, "UNKNOWN": 1, "DEGRADED": 2, "BLOCKED": 3}
        overall = max(
            (severity.get(str(value.get("status") or "UNKNOWN").upper(), 1) for value in components.values()),
            default=0,
        )
        status = {0: "OK", 1: "UNKNOWN", 2: "DEGRADED", 3: "BLOCKED"}[overall]
        return {
            "status": status,
            "components": components,
            "as_of": datetime.now(UTC).isoformat(),
        }
    finally:
        if owns_session:
            session.close()


__all__ = [
    "backup_status",
    "database_status",
    "disk_status",
    "governance_status",
    "liveness",
    "monitor_status",
    "operational_health",
    "readiness",
    "scheduler_status",
    "schema_status",
    "shadow_status",
    "worker_recovery_status",
]
