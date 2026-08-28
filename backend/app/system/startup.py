"""Startup preflight, pre-upgrade guard, and daily system maintenance."""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import settings
from .backup import (
    BackupError,
    create_backup,
    ensure_pre_upgrade_backup,
    run_scheduled_system_maintenance,
)
from .logging import configure_logging, tail_logs

logger = logging.getLogger(__name__)

_PREFLIGHT: dict[str, Any] = {"completed": False, "checks": {}, "blocked": False}


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def run_startup_preflight(db: Session | None = None) -> dict[str, Any]:
    from .health import database_status, disk_status, governance_status, schema_status

    owns_session = db is None
    if owns_session:
        from ..database import SessionLocal

        session = SessionLocal()
    else:
        session = db
    try:
        database = database_status(session)
        storage = disk_status()
        schema = schema_status(session)
        governance = governance_status(session)
        checks = {
            "database": database,
            "storage": storage,
            "schema": schema,
            "governance": governance,
        }
        blocked = any(
            str(checks[key].get("status") or "").upper() == "BLOCKED"
            for key in checks
        )
        result = {
            "completed": True,
            "checks": checks,
            "blocked": blocked,
        }
        _PREFLIGHT.clear()
        _PREFLIGHT.update(result)
        return result
    finally:
        if owns_session:
            session.close()


def startup_preflight_completed() -> bool:
    return bool(_PREFLIGHT.get("completed"))


def startup_preflight_state() -> dict[str, Any]:
    return dict(_PREFLIGHT)


def collect_startup_recovery_report(db: Session) -> dict[str, Any]:
    """Read stale durable-claim counts without mutating anything."""

    cutoff = datetime.now(UTC).replace(tzinfo=None)
    inspector = inspect(db.get_bind())
    counts: dict[str, int] = {}
    errors: list[str] = []
    try:
        if inspector.has_table("daily_operational_checkpoints"):
            counts["stale_checkpoints"] = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM daily_operational_checkpoints "
                        "WHERE status IN ('CLAIMED','RUNNING') AND lease_expires_at IS NOT NULL "
                        "AND lease_expires_at < :cutoff"
                    ),
                    {"cutoff": cutoff},
                ).scalar_one()
            )
        if inspector.has_table("backtest_runs"):
            counts["stale_backtests"] = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM backtest_runs "
                        "WHERE status = 'RUNNING' AND lease_expires_at IS NOT NULL "
                        "AND lease_expires_at < :cutoff"
                    ),
                    {"cutoff": cutoff},
                ).scalar_one()
            )
        if inspector.has_table("operating_notifications"):
            counts["stale_notifications"] = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM operating_notifications "
                        "WHERE status = 'DISPATCHING' AND lease_expires_at IS NOT NULL "
                        "AND lease_expires_at < :cutoff"
                    ),
                    {"cutoff": cutoff},
                ).scalar_one()
            )
        if inspector.has_table("daily_review_runs"):
            counts["stale_reviews"] = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM daily_review_runs "
                        "WHERE status = 'RUNNING' AND lease_expires_at IS NOT NULL "
                        "AND lease_expires_at < :cutoff"
                    ),
                    {"cutoff": cutoff},
                ).scalar_one()
            )
        if inspector.has_table("analysis_jobs"):
            counts["stale_analysis_jobs"] = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM analysis_jobs "
                        "WHERE status IN ('running','retrying') "
                        "AND (started_at IS NULL OR started_at <= :cutoff)"
                    ),
                    {"cutoff": cutoff},
                ).scalar_one()
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"startup_recovery_collect_failed:{exc}")
    return {"counts": counts, "errors": errors}


def schedule_system_maintenance(scheduler: Any) -> bool:
    if not (settings.BACKUP_SCHEDULE_ENABLED or settings.SYSTEM_MAINTENANCE_QUICK_CHECK_ENABLED):
        return False
    try:
        hour_text, minute_text = settings.BACKUP_SCHEDULE_TIME.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, TypeError):
        logger.error("invalid BACKUP_SCHEDULE_TIME %s", settings.BACKUP_SCHEDULE_TIME)
        return False
    scheduler.add_job(
        run_scheduled_system_maintenance,
        "cron",
        hour=hour,
        minute=minute,
        timezone="Asia/Shanghai",
        id="system-daily-maintenance",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    logger.info("System daily maintenance scheduled at %s Asia/Shanghai", settings.BACKUP_SCHEDULE_TIME)
    return True


def run_pre_upgrade_guard() -> dict[str, Any]:
    try:
        backup = ensure_pre_upgrade_backup()
        return {"backup_required": backup is not None, "backup": backup}
    except BackupError as exc:
        raise RuntimeError(f"PRE_UPGRADE_BACKUP_FAILED:{exc}") from exc


def _backend_directory() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    """Docker entrypoint: verified backup guard, Alembic upgrade, preflight."""

    configure_logging()
    try:
        run_pre_upgrade_guard()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=str(_backend_directory()),
            check=True,
        )
    except subprocess.CalledProcessError:
        print("ALEMBIC_UPGRADE_FAILED: restore the latest verified backup and inspect the failure.", file=sys.stderr)
        return 1
    from ..database import SessionLocal

    try:
        with SessionLocal() as db:
            preflight = run_startup_preflight(db)
    except Exception as exc:  # noqa: BLE001
        print(f"STARTUP_PREFLIGHT_FAILED: {exc}", file=sys.stderr)
        return 1
    if preflight["blocked"]:
        print("STARTUP_PREFLIGHT_BLOCKED", file=sys.stderr)
        return 1
    print("STARTUP_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "collect_startup_recovery_report",
    "main",
    "run_pre_upgrade_guard",
    "run_startup_preflight",
    "schedule_system_maintenance",
    "startup_preflight_completed",
    "startup_preflight_state",
]
