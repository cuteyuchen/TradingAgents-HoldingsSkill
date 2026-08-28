"""Phase J parameter governance API: research proposes, humans approve and activate."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..governance.models import (
    ParameterChangeProposal,
    ParameterSetVersion,
)
from ..governance.registry import REGISTRY, read_current_value
from ..governance.service import (
    GovernanceBlockedError,
    GovernanceError,
    activate_parameter_set_version,
    approve_proposal,
    create_manual_proposal,
    create_proposal_from_calibration,
    create_rollback_proposal,
    governance_health,
    list_governance_events,
    list_parameter_set_versions,
    list_proposals,
    reject_proposal,
    serialize_governance_event,
    serialize_parameter_set_version,
    serialize_proposal,
    submit_proposal,
    validate_parameter_set_version,
)
from ..research.models import CalibrationReport
from ..v2_dependencies import get_current_user
from ..v2_models import User

router = APIRouter(prefix="/api/v3/governance", tags=["v3-governance"])


class FromCalibrationRequest(BaseModel):
    calibration_report_id: int = Field(ge=1)
    proposed_value: Any
    reason: str | None = Field(default=None, max_length=4000)


class ManualProposalRequest(BaseModel):
    target_parameter_key: str = Field(min_length=1, max_length=160)
    proposed_value: Any
    reason: str = Field(min_length=1, max_length=4000)
    proposal_type: str = "MANUAL_EXCEPTION"
    risk_acknowledged: bool = False
    risk_summary: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class ReviewRequest(BaseModel):
    review_comment: str | None = Field(default=None, max_length=4000)


class ActivateRequest(BaseModel):
    emergency_override: bool = False
    reason: str | None = Field(default=None, max_length=4000)
    expected_active_version_id: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid")


class RollbackRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)


def _error(exc: ValueError) -> HTTPException:
    message = str(exc)
    conflict_codes = {
        "STALE_BASE_VERSION",
        "ACTIVE_VERSION_CHANGED",
        "BLOCKED_TRADING_SESSION",
        "version_not_approved",
        "validation_blocked",
        "validation_warning_requires_acknowledgement",
        "emergency_reason_required",
        "MULTIPLE_ACTIVE_PARAMETER_SETS",
        "NO_ACTIVE_PARAMETER_SET_WITH_HISTORY",
    }
    if isinstance(exc, GovernanceBlockedError) or any(code in message for code in conflict_codes):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _version(db: Session, version_id: int) -> ParameterSetVersion:
    row = db.get(ParameterSetVersion, version_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parameter set version not found.")
    return row


def _proposal(db: Session, proposal_id: int) -> ParameterChangeProposal:
    row = db.get(ParameterChangeProposal, proposal_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parameter proposal not found.")
    return row


@router.get("/parameters")
def parameters(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    active = db.execute(
        select(ParameterSetVersion)
        .where(ParameterSetVersion.status == "ACTIVE")
        .order_by(ParameterSetVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    values: dict[str, Any] = {}
    for key, spec in REGISTRY.items():
        try:
            current = read_current_value(active.snapshot_json, key) if active is not None else None
        except Exception:  # noqa: BLE001
            current = None
        values[key] = {
            **{
                field_name: getattr(spec, field_name)
                for field_name in (
                    "display_name",
                    "domain",
                    "classification",
                    "value_type",
                    "min_value",
                    "max_value",
                    "calibration_supported",
                    "requires_calibration_report",
                    "protected",
                    "restart_required",
                    "runtime_contract_relevant",
                    "description",
                )
            },
            "current_value": current,
        }
    return {
        "registry": values,
        "active_version_id": active.id if active else None,
        "active_version": active.version if active else None,
    }


@router.get("/parameter-sets")
def parameter_sets(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return {"versions": [serialize_parameter_set_version(row) for row in list_parameter_set_versions(db, limit=limit)]}


@router.get("/parameter-sets/active")
def active_parameter_set(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    from ..governance.service import get_active_parameter_set

    try:
        row = get_active_parameter_set(db)
    except GovernanceBlockedError as exc:
        raise _error(exc) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="NO_ACTIVE_PARAMETER_SET")
    return serialize_parameter_set_version(row)


@router.get("/parameter-sets/{version_id}")
def parameter_set_detail(
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return serialize_parameter_set_version(_version(db, version_id))


@router.get("/proposals")
def proposals(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return {"proposals": [serialize_proposal(row) for row in list_proposals(db, limit=limit)]}


@router.get("/proposals/{proposal_id}")
def proposal_detail(
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return serialize_proposal(_proposal(db, proposal_id))


@router.post("/proposals/from-calibration")
def proposal_from_calibration(
    payload: FromCalibrationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    report = db.get(CalibrationReport, payload.calibration_report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calibration report not found.")
    try:
        row = create_proposal_from_calibration(
            db,
            calibration_report=report,
            proposed_value=payload.proposed_value,
            user_id=current_user.id,
            reason=payload.reason,
        )
        db.commit()
        return serialize_proposal(row)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.post("/proposals/manual")
def manual_proposal(
    payload: ManualProposalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        row = create_manual_proposal(
            db,
            target_parameter_key=payload.target_parameter_key,
            proposed_value=payload.proposed_value,
            user_id=current_user.id,
            reason=payload.reason,
            proposal_type=payload.proposal_type,
            risk_acknowledged=payload.risk_acknowledged,
            risk_summary=payload.risk_summary,
        )
        db.commit()
        return serialize_proposal(row)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.post("/proposals/{proposal_id}/submit")
def submit(
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _proposal(db, proposal_id)
    try:
        submit_proposal(db, proposal=row, user_id=current_user.id)
        db.commit()
        return serialize_proposal(row)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.post("/proposals/{proposal_id}/approve")
def approve(
    proposal_id: int,
    payload: ReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _proposal(db, proposal_id)
    try:
        version = approve_proposal(
            db,
            proposal=row,
            reviewer_user_id=current_user.id,
            review_comment=payload.review_comment,
        )
        db.commit()
        return {"proposal": serialize_proposal(row), "version": serialize_parameter_set_version(version)}
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.post("/proposals/{proposal_id}/reject")
def reject(
    proposal_id: int,
    payload: ReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _proposal(db, proposal_id)
    try:
        reject_proposal(
            db,
            proposal=row,
            reviewer_user_id=current_user.id,
            review_comment=payload.review_comment,
        )
        db.commit()
        return serialize_proposal(row)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.post("/parameter-sets/{version_id}/validate")
def validate_version(
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _version(db, version_id)
    try:
        validate_parameter_set_version(db, version=row, actor_user_id=current_user.id)
        db.commit()
        return serialize_parameter_set_version(row)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.post("/parameter-sets/{version_id}/activate")
def activate(
    version_id: int,
    payload: ActivateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = _version(db, version_id)
    try:
        activate_parameter_set_version(
            db,
            version=row,
            actor_user_id=current_user.id,
            emergency_override=payload.emergency_override,
            reason=payload.reason,
            expected_active_version_id=payload.expected_active_version_id,
        )
        db.commit()
        return serialize_parameter_set_version(row)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.post("/parameter-sets/{version_id}/rollback-proposal")
def rollback_proposal(
    version_id: int,
    payload: RollbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _version(db, version_id)
    try:
        row = create_rollback_proposal(
            db,
            target_version_id=version_id,
            user_id=current_user.id,
            reason=payload.reason,
        )
        db.commit()
        return serialize_proposal(row)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.get("/events")
def events(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return {"events": [serialize_governance_event(row) for row in list_governance_events(db, limit=limit)]}


@router.get("/health")
def health(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return governance_health(db)


__all__ = ["router"]
