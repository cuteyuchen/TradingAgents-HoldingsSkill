"""Single source of truth for release metadata and schema compatibility."""

from __future__ import annotations

import platform
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..config import settings
from ..governance.service import GovernanceBlockedError, resolve_production_parameters
from ..services.skill_runtime import runtime_metadata

STARTED_AT = datetime.now(UTC)

_REVISION_RE = re.compile(r'^revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DOWN_REVISION_RE = re.compile(r"^down_revision\s*=\s*(?:None|['\"]([^'\"]+)['\"])", re.MULTILINE)


def alembic_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "alembic"


def alembic_versions_directory() -> Path:
    return alembic_directory() / "versions"


def known_migration_revisions() -> list[str]:
    """Return migration revisions ordered by their numeric prefix."""

    directory = alembic_versions_directory()
    revisions: list[str] = []
    if not directory.is_dir():
        return revisions
    for path in sorted(directory.glob("*.py")):
        match = _REVISION_RE.search(path.read_text(encoding="utf-8"))
        if match:
            revisions.append(match.group(1))
    return revisions


def code_head_revision() -> str | None:
    revisions = known_migration_revisions()
    return revisions[-1] if revisions else None


def alembic_db_revision(db: Session) -> str | None:
    try:
        if not inspect(db.get_bind()).has_table("alembic_version"):
            return None
        row = db.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
        return str(row) if row else None
    except Exception:  # noqa: BLE001
        return None


def schema_state(
    db_revision: str | None = None,
    code_head: str | None = None,
    revisions: list[str] | None = None,
) -> dict[str, Any]:
    """Return CURRENT / BEHIND / AHEAD / UNKNOWN / BROKEN schema status."""

    code_head = code_head or code_head_revision()
    revisions = revisions if revisions is not None else known_migration_revisions()
    if not code_head:
        return {
            "state": "UNKNOWN",
            "db_revision": db_revision,
            "code_head_revision": code_head,
            "reason": "MIGRATION_HEAD_UNAVAILABLE",
            "blocked": False,
        }
    if db_revision in (None, ""):
        return {
            "state": "UNKNOWN",
            "db_revision": db_revision,
            "code_head_revision": code_head,
            "reason": "ALEMBIC_VERSION_MISSING",
            "blocked": False,
        }
    if db_revision == code_head:
        return {
            "state": "CURRENT",
            "db_revision": db_revision,
            "code_head_revision": code_head,
            "reason": None,
            "blocked": False,
        }
    if db_revision in revisions:
        return {
            "state": "BEHIND",
            "db_revision": db_revision,
            "code_head_revision": code_head,
            "reason": "DB_SCHEMA_BEHIND",
            "blocked": False,
        }
    if db_revision > code_head:
        return {
            "state": "AHEAD",
            "db_revision": db_revision,
            "code_head_revision": code_head,
            "reason": "BLOCKED_SCHEMA_AHEAD",
            "blocked": True,
        }
    return {
        "state": "BROKEN",
        "db_revision": db_revision,
        "code_head_revision": code_head,
        "reason": "DB_REVISION_UNKNOWN",
        "blocked": True,
    }


def active_parameter_summary(db: Session | None = None) -> dict[str, Any]:
    owns_session = db is None
    if owns_session:
        from ..database import SessionLocal

        session = SessionLocal()
    else:
        session = db
    try:
        context = resolve_production_parameters(session)
        return {
            "version_id": context.get("version_id"),
            "version": context.get("version"),
            "config_hash": context.get("config_hash"),
            "status": "OK",
        }
    except GovernanceBlockedError as exc:
        return {
            "version_id": None,
            "version": None,
            "config_hash": None,
            "status": "BLOCKED",
            "reason": str(exc),
        }
    except Exception:  # noqa: BLE001
        return {"version_id": None, "version": None, "config_hash": None, "status": "UNKNOWN"}
    finally:
        if owns_session:
            session.close()


def _runtime_contract_versions() -> dict[str, str]:
    try:
        skill = runtime_metadata()
        contract_version = str(skill.get("decision_contract_version") or "2.4.0")
        return {
            "runtime_contract_version": contract_version,
            "decision_contract_version": contract_version,
        }
    except Exception:  # noqa: BLE001
        return {"runtime_contract_version": "2.4.0", "decision_contract_version": "2.4.0"}


def build_release_metadata(db: Session | None = None) -> dict[str, Any]:
    """Build deterministic release metadata without hiding missing values."""

    owns_session = db is None
    if owns_session:
        from ..database import SessionLocal

        session = SessionLocal()
    else:
        session = db
    try:
        db_revision = alembic_db_revision(session)
        state = schema_state(db_revision=db_revision)
        contracts = _runtime_contract_versions()
        active = active_parameter_summary(session)
        uptime_seconds = max(0, (datetime.now(UTC) - STARTED_AT).total_seconds())
        return {
            "app_version": settings.APP_VERSION or "UNKNOWN",
            "git_sha": settings.APP_GIT_SHA or "UNKNOWN",
            "git_ref": settings.APP_GIT_REF or None,
            "build_time": settings.APP_BUILD_TIME or None,
            "alembic_db_revision": db_revision,
            "alembic_code_head_revision": state["code_head_revision"],
            "schema_state": state["state"],
            "schema_reason": state["reason"],
            "schema_blocked": state["blocked"],
            "runtime_contract_version": contracts["runtime_contract_version"],
            "decision_contract_version": contracts["decision_contract_version"],
            "active_parameter_set_version": active.get("version"),
            "active_parameter_set_hash": active.get("config_hash"),
            "governance_status": active.get("status"),
            "python_version": platform.python_version(),
            "environment": settings.APP_ENV,
            "database_backend": "sqlite",
            "database_identity": Path(settings.DB_PATH).name,
            "started_at": STARTED_AT.isoformat(),
            "uptime_seconds": uptime_seconds,
        }
    finally:
        if owns_session:
            session.close()


__all__ = [
    "STARTED_AT",
    "alembic_db_revision",
    "build_release_metadata",
    "code_head_revision",
    "known_migration_revisions",
    "schema_state",
]
