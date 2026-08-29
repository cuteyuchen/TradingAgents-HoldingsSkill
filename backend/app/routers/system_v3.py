"""Phase K system observability API (authenticated; no production restore)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..system.backup import (
    BackupError,
    RestoreError,
    create_backup,
    list_backups,
    restore_drill,
    verify_backup,
)
from ..system.diagnostics import (
    build_diagnostic_bundle,
    diagnostic_bundle_metadata,
    diagnostic_bundle_path,
)
from ..system.health import operational_health, readiness
from ..system.release import build_release_metadata
from ..system.startup import collect_startup_recovery_report
from ..v2_dependencies import get_current_user
from ..v2_models import User

router = APIRouter(prefix="/api/v3/system", tags=["v3-system"])


class BackupRequest(BaseModel):
    reason: Literal["MANUAL", "SCHEDULED", "PRE_UPGRADE", "PRE_RESTORE_SAFETY"] = "MANUAL"

    model_config = {"extra": "forbid"}


def _system_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (BackupError, RestoreError)):
        message = str(exc)
        if "BACKUP_ALREADY_RUNNING" in message:
            return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/release")
def system_release(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return build_release_metadata(db)


@router.get("/health")
def system_health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return operational_health(db)


@router.get("/readiness")
def system_readiness(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return readiness(db, detailed=True)


@router.get("/recovery")
def system_recovery(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return collect_startup_recovery_report(db)


@router.get("/backups")
def backups(
    limit: int = 200,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return {"backups": list_backups(limit=limit)}


@router.post("/backups", status_code=status.HTTP_201_CREATED)
def create_system_backup(
    payload: BackupRequest,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return create_backup(reason=payload.reason)
    except (BackupError, RestoreError) as exc:
        raise _system_error(exc) from exc


@router.post("/backups/{backup_id}/verify")
def verify_system_backup(
    backup_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return verify_backup(backup_id)
    except (BackupError, RestoreError) as exc:
        raise _system_error(exc) from exc


@router.post("/backups/{backup_id}/restore-drill")
def restore_drill_endpoint(
    backup_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return restore_drill(backup_id)
    except (BackupError, RestoreError) as exc:
        raise _system_error(exc) from exc


@router.post("/diagnostics", status_code=status.HTTP_201_CREATED)
def create_diagnostics(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return build_diagnostic_bundle()
    except Exception as exc:  # noqa: BLE001
        raise _system_error(exc) from exc


@router.get("/diagnostics/{bundle_id}/download")
def download_diagnostics(
    bundle_id: str,
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    try:
        metadata = diagnostic_bundle_metadata(bundle_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnostic bundle not found.") from exc
    path = diagnostic_bundle_path(bundle_id)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnostic bundle not found.")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=metadata["filename"],
        headers={"X-Diagnostic-SHA256": metadata["sha256"] or ""},
    )


__all__ = ["router"]
