"""Governance service: immutable parameter versions, proposals, and activation."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import utc_now_naive
from ..decision_contract import CONTRACT_VERSION
from ..research.models import BacktestRun, CalibrationReport
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService
from ..system.tables import table_exists
from .models import (
    ParameterChangeProposal,
    ParameterGovernanceEvent,
    ParameterSetVersion,
)
from .registry import (
    canonical_config_hash,
    default_production_config,
    get_spec,
    is_protected,
    normalize_snapshot,
    read_current_value,
    set_path,
    validate_registry_value,
)
from .validation import validate_parameter_snapshot


class GovernanceError(ValueError):
    """Domain error for parameter governance."""


class GovernanceBlockedError(GovernanceError):
    """Governance data is inconsistent and risk-increasing work must fail closed."""


_CACHE_TTL_SECONDS = 15.0
_ACTIVE_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}


def invalidate_parameter_cache() -> None:
    _ACTIVE_CACHE["expires_at"] = 0.0
    _ACTIVE_CACHE["payload"] = None


def _now() -> datetime:
    return utc_now_naive()


def _audit(
    db: Session,
    *,
    event_type: str,
    actor_user_id: int | None = None,
    proposal_id: int | None = None,
    version_id: int | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> ParameterGovernanceEvent:
    row = ParameterGovernanceEvent(
        actor_user_id=actor_user_id,
        event_type=event_type,
        proposal_id=proposal_id,
        parameter_set_version_id=version_id,
        occurred_at=_now(),
        metadata_json=metadata_json,
    )
    db.add(row)
    db.flush()
    return row


def get_active_parameter_set(db: Session) -> ParameterSetVersion | None:
    rows = list(
        db.execute(
            select(ParameterSetVersion)
            .where(ParameterSetVersion.status == "ACTIVE")
            .order_by(ParameterSetVersion.id.asc())
        ).scalars()
    )
    if len(rows) > 1:
        raise GovernanceBlockedError("MULTIPLE_ACTIVE_PARAMETER_SETS")
    return rows[0] if rows else None


def _legacy_context() -> dict[str, Any]:
    snapshot = default_production_config()
    return {
        "version_id": None,
        "version": "LEGACY_PRE_GOVERNANCE",
        "config_hash": canonical_config_hash(snapshot),
        "snapshot": snapshot,
    }


def _context_from_version(version: ParameterSetVersion) -> dict[str, Any]:
    snapshot = normalize_snapshot(version.snapshot_json)
    if canonical_config_hash(snapshot) != version.config_hash:
        raise GovernanceBlockedError("CONFIG_HASH_MISMATCH")
    return {
        "version_id": version.id,
        "version": f"v{version.version}",
        "config_hash": version.config_hash,
        "snapshot": snapshot,
    }


def resolve_production_parameters(db: Session | None = None) -> dict[str, Any]:
    """Return the immutable active snapshot or a legacy pre-governance context."""

    if db is not None:
        try:
            if not table_exists(db, "parameter_set_versions"):
                return _legacy_context()
        except GovernanceBlockedError:
            raise
        except Exception:  # noqa: BLE001
            return _legacy_context()
        try:
            history_count = db.execute(select(func.count()).select_from(ParameterSetVersion)).scalar_one()
            if history_count == 0:
                return _legacy_context()
            active = get_active_parameter_set(db)
            if active is None:
                raise GovernanceBlockedError("NO_ACTIVE_PARAMETER_SET_WITH_HISTORY")
            return _context_from_version(active)
        except GovernanceBlockedError:
            raise
        except Exception:  # noqa: BLE001
            db.rollback()
            raise GovernanceBlockedError("GOVERNANCE_UNAVAILABLE") from None

    if _ACTIVE_CACHE["expires_at"] > time.monotonic() and _ACTIVE_CACHE["payload"] is not None:
        return dict(_ACTIVE_CACHE["payload"])
    from ..database import SessionLocal

    try:
        with SessionLocal() as db_session:
            if not table_exists(db_session, "parameter_set_versions"):
                payload = _legacy_context()
            else:
                history_count = db_session.execute(
                    select(func.count()).select_from(ParameterSetVersion)
                ).scalar_one()
                if history_count == 0:
                    payload = _legacy_context()
                else:
                    active = get_active_parameter_set(db_session)
                    if active is None:
                        raise GovernanceBlockedError("NO_ACTIVE_PARAMETER_SET_WITH_HISTORY")
                    payload = _context_from_version(active)
    except GovernanceBlockedError:
        raise
    except Exception:  # noqa: BLE001
        payload = _legacy_context()
    _ACTIVE_CACHE["expires_at"] = time.monotonic() + _CACHE_TTL_SECONDS
    _ACTIVE_CACHE["payload"] = payload
    return dict(payload)


def lineage_fields(context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return production-row lineage columns from a resolved parameter context."""

    context = context or _legacy_context()
    version_id = context.get("version_id")
    version = str(context.get("version") or "LEGACY_PRE_GOVERNANCE")
    return {
        "parameter_set_version_id": version_id,
        "parameter_set_version": version,
        "parameter_set_hash": context.get("config_hash"),
        "governance_lineage_json": {
            "source": "PARAMETER_SET" if version_id is not None else "LEGACY_PRE_GOVERNANCE",
            "version_id": version_id,
            "version": version,
            "config_hash": context.get("config_hash"),
        },
    }


def bootstrap_parameter_set(db: Session) -> ParameterSetVersion:
    """Create ACTIVE v1 exactly once. Existing history without ACTIVE is BLOCKED."""

    count = db.execute(select(func.count()).select_from(ParameterSetVersion)).scalar_one()
    if count:
        active = get_active_parameter_set(db)
        if active is None:
            raise GovernanceBlockedError("NO_ACTIVE_PARAMETER_SET_WITH_HISTORY")
        return active
    snapshot = default_production_config()
    version = ParameterSetVersion(
        version=1,
        status="ACTIVE",
        snapshot_json=snapshot,
        diff_json={"source": "SYSTEM_BOOTSTRAP"},
        config_hash=canonical_config_hash(snapshot),
        runtime_contract_version=CONTRACT_VERSION,
        decision_contract_version=CONTRACT_VERSION,
        activated_at=_now(),
        activation_reason="SYSTEM_BOOTSTRAP",
    )
    db.add(version)
    db.flush()
    _audit(db, event_type="VERSION_CREATED", version_id=version.id, metadata_json={"source": "SYSTEM_BOOTSTRAP"})
    _audit(db, event_type="VERSION_ACTIVATED", version_id=version.id, metadata_json={"reason": "SYSTEM_BOOTSTRAP"})
    db.commit()
    invalidate_parameter_cache()
    return version


def _next_version_number(db: Session) -> int:
    current = db.execute(select(func.max(ParameterSetVersion.version))).scalar_one()
    return int(current or 0) + 1


def _open_proposal(
    db: Session,
    *,
    source_calibration_report_id: int | None,
    target_parameter_key: str,
    base_parameter_set_version_id: int | None,
) -> ParameterChangeProposal | None:
    return db.execute(
        select(ParameterChangeProposal).where(
            ParameterChangeProposal.source_calibration_report_id == source_calibration_report_id,
            ParameterChangeProposal.target_parameter_key == target_parameter_key,
            ParameterChangeProposal.base_parameter_set_version_id == base_parameter_set_version_id,
            ParameterChangeProposal.status.in_(("DRAFT", "PENDING_REVIEW", "APPROVED")),
        )
    ).scalar_one_or_none()


def _proposal_evidence(calibration_report: CalibrationReport) -> dict[str, Any]:
    return {
        "recommendation": calibration_report.recommendation,
        "sample_counts": calibration_report.sample_counts_json,
        "validation": calibration_report.validation_metrics_json,
        "test": calibration_report.test_metrics_json,
        "global_final_test": (calibration_report.report_json or {}).get("global_final_test")
        if isinstance(calibration_report.report_json, dict)
        else None,
        "robustness": calibration_report.robustness_json,
        "known_limitations": calibration_report.risk_notes_json,
        "replay_mode": (calibration_report.report_json or {}).get("replay_mode")
        if isinstance(calibration_report.report_json, dict)
        else None,
    }


def create_proposal_from_calibration(
    db: Session,
    *,
    calibration_report: CalibrationReport,
    proposed_value: Any,
    user_id: int,
    reason: str | None = None,
) -> ParameterChangeProposal:
    if calibration_report.recommendation != "CONSIDER_CHANGE":
        raise GovernanceError("calibration_not_consider_change")
    if calibration_report.status != "COMPLETED":
        raise GovernanceError("calibration_not_completed")
    key = calibration_report.target_parameter
    spec = get_spec(key)
    if not spec.calibration_supported:
        raise GovernanceError("parameter_not_calibratable")
    if spec.protected:
        raise GovernanceError("protected_parameter_requires_manual_exception")
    active = get_active_parameter_set(db)
    if active is None:
        raise GovernanceError("no_active_parameter_set")
    current_value = read_current_value(active.snapshot_json, key)
    proposed = validate_registry_value(key, proposed_value)
    if calibration_report.challenger_value_json != proposed:
        raise GovernanceError("CALIBRATION_VALUE_MISMATCH")
    if calibration_report.current_value_json != current_value:
        raise GovernanceError("CALIBRATION_VALUE_MISMATCH")
    backtest = db.get(BacktestRun, calibration_report.backtest_run_id)
    if backtest is None:
        raise GovernanceError("calibration_backtest_not_found")
    if (
        backtest.parameter_set_version_id != active.id
        or backtest.parameter_set_hash != active.config_hash
    ):
        raise GovernanceError("CALIBRATION_BASE_VERSION_CHANGED")
    existing = _open_proposal(
        db,
        source_calibration_report_id=calibration_report.id,
        target_parameter_key=key,
        base_parameter_set_version_id=active.id,
    )
    if existing is not None:
        return existing
    row = ParameterChangeProposal(
        user_id=user_id,
        source_type="CALIBRATION_REPORT",
        source_calibration_report_id=calibration_report.id,
        base_parameter_set_version_id=active.id,
        target_parameter_key=key,
        current_value_json=current_value,
        proposed_value_json=proposed,
        proposal_type="STANDARD",
        status="DRAFT",
        evidence_summary_json=_proposal_evidence(calibration_report),
        risk_summary_json={"source": "calibration_report", "human_review_required": True},
        reason=reason,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        event_type="PROPOSAL_CREATED",
        actor_user_id=user_id,
        proposal_id=row.id,
        metadata_json={"target_parameter": key, "source_type": "CALIBRATION_REPORT"},
    )
    return row


def create_manual_proposal(
    db: Session,
    *,
    target_parameter_key: str,
    proposed_value: Any,
    user_id: int,
    reason: str,
    proposal_type: str = "MANUAL_EXCEPTION",
    risk_acknowledged: bool = False,
    risk_summary: dict[str, Any] | None = None,
) -> ParameterChangeProposal:
    get_spec(target_parameter_key)
    if proposal_type != "MANUAL_EXCEPTION":
        raise GovernanceError("manual_proposal_requires_manual_exception")
    if not risk_acknowledged:
        raise GovernanceError("manual_proposal_requires_risk_acknowledgement")
    active = get_active_parameter_set(db)
    if active is None:
        raise GovernanceError("no_active_parameter_set")
    current_value = read_current_value(active.snapshot_json, target_parameter_key)
    proposed = validate_registry_value(target_parameter_key, proposed_value)
    existing = _open_proposal(
        db,
        source_calibration_report_id=None,
        target_parameter_key=target_parameter_key,
        base_parameter_set_version_id=active.id,
    )
    if existing is not None:
        return existing
    row = ParameterChangeProposal(
        user_id=user_id,
        source_type="MANUAL",
        base_parameter_set_version_id=active.id,
        target_parameter_key=target_parameter_key,
        current_value_json=current_value,
        proposed_value_json=proposed,
        proposal_type=proposal_type,
        status="DRAFT",
        evidence_summary_json={"source": "manual"},
        risk_summary_json={
            **(risk_summary or {}),
            "risk_acknowledged": risk_acknowledged,
        },
        reason=reason,
        risk_acknowledged=risk_acknowledged,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        event_type="PROPOSAL_CREATED",
        actor_user_id=user_id,
        proposal_id=row.id,
        metadata_json={"target_parameter": target_parameter_key, "source_type": "MANUAL"},
    )
    return row


def create_rollback_proposal(
    db: Session,
    *,
    target_version_id: int,
    user_id: int,
    reason: str,
    current_active: ParameterSetVersion | None = None,
) -> ParameterChangeProposal:
    target = db.get(ParameterSetVersion, target_version_id)
    if target is None:
        raise GovernanceError("target_version_not_found")
    active = current_active or get_active_parameter_set(db)
    if active is None:
        raise GovernanceError("no_active_parameter_set")
    if target.id == active.id:
        raise GovernanceError("cannot_rollback_to_active_version")
    existing = db.execute(
        select(ParameterChangeProposal).where(
            ParameterChangeProposal.source_type == "ROLLBACK",
            ParameterChangeProposal.target_parameter_key == "__FULL_ROLLBACK__",
            ParameterChangeProposal.base_parameter_set_version_id == active.id,
            ParameterChangeProposal.status.in_(("DRAFT", "PENDING_REVIEW", "APPROVED")),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = ParameterChangeProposal(
        user_id=user_id,
        source_type="ROLLBACK",
        base_parameter_set_version_id=active.id,
        target_parameter_key="__FULL_ROLLBACK__",
        current_value_json={"active_version": active.version, "active_version_id": active.id},
        proposed_value_json={"rollback_target_version": target.version, "rollback_target_version_id": target.id},
        proposed_snapshot_json=target.snapshot_json,
        proposal_type="ROLLBACK",
        status="DRAFT",
        evidence_summary_json={"source": "rollback"},
        risk_summary_json={"target_version": target.version, "target_version_id": target.id},
        reason=reason,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        event_type="ROLLBACK_PROPOSED",
        actor_user_id=user_id,
        proposal_id=row.id,
        metadata_json={"target_version": target.version, "active_version": active.version},
    )
    return row


def submit_proposal(db: Session, *, proposal: ParameterChangeProposal, user_id: int) -> ParameterChangeProposal:
    if proposal.status != "DRAFT":
        raise GovernanceError("proposal_not_draft")
    proposal._allow_governance_update = True
    proposal.status = "PENDING_REVIEW"
    proposal.submitted_at = _now()
    _audit(db, event_type="PROPOSAL_SUBMITTED", actor_user_id=user_id, proposal_id=proposal.id)
    db.flush()
    return proposal


def reject_proposal(
    db: Session,
    *,
    proposal: ParameterChangeProposal,
    reviewer_user_id: int,
    review_comment: str | None = None,
) -> ParameterChangeProposal:
    if proposal.status != "PENDING_REVIEW":
        raise GovernanceError("proposal_not_pending_review")
    proposal._allow_governance_update = True
    proposal.status = "REJECTED"
    proposal.reviewed_at = _now()
    proposal.reviewed_by_user_id = reviewer_user_id
    proposal.review_comment = review_comment
    _audit(db, event_type="PROPOSAL_REJECTED", actor_user_id=reviewer_user_id, proposal_id=proposal.id)
    db.flush()
    return proposal


def approve_proposal(
    db: Session,
    *,
    proposal: ParameterChangeProposal,
    reviewer_user_id: int,
    review_comment: str | None = None,
) -> ParameterSetVersion:
    if proposal.status != "PENDING_REVIEW":
        raise GovernanceError("proposal_not_pending_review")
    active = get_active_parameter_set(db)
    if active is None or proposal.base_parameter_set_version_id != active.id:
        raise GovernanceError("STALE_BASE_VERSION")
    if proposal.approved_version_id is not None:
        version = db.get(ParameterSetVersion, proposal.approved_version_id)
        if version is not None:
            return version
    base = db.get(ParameterSetVersion, proposal.base_parameter_set_version_id)
    if base is None:
        raise GovernanceError("base_version_not_found")
    if proposal.source_type == "ROLLBACK":
        snapshot = normalize_snapshot(proposal.proposed_snapshot_json or base.snapshot_json)
        diff = {
            "__FULL_ROLLBACK__": {
                "from": proposal.current_value_json,
                "to": proposal.proposed_value_json,
            }
        }
    else:
        snapshot = set_path(base.snapshot_json, proposal.target_parameter_key, proposal.proposed_value_json)
        validate_registry_value(proposal.target_parameter_key, proposal.proposed_value_json)
        diff = {
            proposal.target_parameter_key: {
                "from": proposal.current_value_json,
                "to": proposal.proposed_value_json,
            }
        }
    version = ParameterSetVersion(
        version=_next_version_number(db),
        status="APPROVED",
        parent_version_id=base.id,
        created_by_user_id=proposal.user_id,
        approved_by_user_id=reviewer_user_id,
        source_proposal_id=proposal.id,
        snapshot_json=snapshot,
        diff_json=diff,
        config_hash=canonical_config_hash(snapshot),
        runtime_contract_version=CONTRACT_VERSION,
        decision_contract_version=CONTRACT_VERSION,
        approved_at=_now(),
        rollback_from_version_id=active.id if proposal.source_type == "ROLLBACK" else None,
        rollback_reason=proposal.reason if proposal.source_type == "ROLLBACK" else None,
    )
    db.add(version)
    db.flush()
    proposal._allow_governance_update = True
    proposal.status = "APPROVED"
    proposal.approved_version_id = version.id
    proposal.reviewed_at = _now()
    proposal.reviewed_by_user_id = reviewer_user_id
    proposal.review_comment = review_comment
    _audit(db, event_type="PROPOSAL_APPROVED", actor_user_id=reviewer_user_id, proposal_id=proposal.id)
    _audit(db, event_type="VERSION_CREATED", actor_user_id=reviewer_user_id, version_id=version.id, proposal_id=proposal.id)
    db.flush()
    return version


def validate_parameter_set_version(
    db: Session,
    *,
    version: ParameterSetVersion,
    actor_user_id: int | None = None,
) -> ParameterSetVersion:
    result = validate_parameter_snapshot(version.snapshot_json)
    version._allow_governance_update = True
    version.validation_json = result
    version.validation_status = result["status"]
    _audit(
        db,
        event_type="VALIDATION_RUN",
        actor_user_id=actor_user_id,
        version_id=version.id,
        metadata_json={"status": result["status"], "blocked_count": result["blocked_count"]},
    )
    db.flush()
    return version


def _trading_session_blocked(db: Session) -> bool:
    try:
        return bool(TradingCalendarService(db).is_market_session())
    except Exception:  # noqa: BLE001
        # Missing or broken calendar data must not open a window for unguarded
        # activation during an actual trading session.
        return True


def activate_parameter_set_version(
    db: Session,
    *,
    version: ParameterSetVersion,
    actor_user_id: int,
    emergency_override: bool = False,
    reason: str | None = None,
    expected_active_version_id: int | None = None,
) -> ParameterSetVersion:
    if version.status == "ACTIVE":
        return version
    if version.status != "APPROVED":
        raise GovernanceError("version_not_approved")
    if version.validation_status != "PASS":
        version = validate_parameter_set_version(db, version=version, actor_user_id=actor_user_id)
    if version.validation_status == "BLOCKED":
        raise GovernanceError("validation_blocked")
    if version.validation_status == "WARNING" and not emergency_override:
        raise GovernanceError("validation_warning_requires_acknowledgement")
    active = get_active_parameter_set(db)
    if version.source_proposal_id is not None:
        proposal = db.get(ParameterChangeProposal, version.source_proposal_id)
        if proposal is not None and active is not None and proposal.base_parameter_set_version_id != active.id:
            raise GovernanceError("STALE_BASE_VERSION")
    if expected_active_version_id is not None and active is not None and active.id != expected_active_version_id:
        raise GovernanceError("ACTIVE_VERSION_CHANGED")
    if _trading_session_blocked(db) and not emergency_override:
        raise GovernanceError("BLOCKED_TRADING_SESSION")
    if emergency_override and not reason:
        raise GovernanceError("emergency_reason_required")
    now = _now()
    if active is not None:
        active._allow_governance_update = True
        active.status = "SUPERSEDED"
        active.deactivated_at = now
        _audit(
            db,
            event_type="VERSION_SUPERSEDED",
            actor_user_id=actor_user_id,
            version_id=active.id,
            metadata_json={"superseded_by_version_id": version.id},
        )
    version._allow_governance_update = True
    version.status = "ACTIVE"
    version.activated_at = now
    version.activation_reason = reason or "MANUAL_ACTIVATION"
    _audit(
        db,
        event_type="VERSION_ACTIVATED",
        actor_user_id=actor_user_id,
        version_id=version.id,
        metadata_json={"emergency_override": emergency_override, "reason": version.activation_reason},
    )
    if version.source_proposal_id is not None:
        proposal = db.get(ParameterChangeProposal, version.source_proposal_id)
        if proposal is not None:
            proposal._allow_governance_update = True
            proposal.status = "ACTIVATED"
    db.commit()
    invalidate_parameter_cache()
    db.refresh(version)
    return version


def governance_health(db: Session) -> dict[str, Any]:
    count = db.execute(select(func.count()).select_from(ParameterSetVersion)).scalar_one()
    if count == 0:
        return {"status": "DEGRADED", "reasons": ["NO_ACTIVE_PARAMETER_SET"], "active": None}
    active_rows = list(
        db.execute(select(ParameterSetVersion).where(ParameterSetVersion.status == "ACTIVE")).scalars()
    )
    if len(active_rows) > 1:
        return {
            "status": "BLOCKED",
            "reasons": ["MULTIPLE_ACTIVE_PARAMETER_SETS"],
            "active": None,
        }
    if not active_rows:
        return {"status": "BLOCKED", "reasons": ["NO_ACTIVE_PARAMETER_SET"], "active": None}
    active = active_rows[0]
    try:
        snapshot = normalize_snapshot(active.snapshot_json)
        if canonical_config_hash(snapshot) != active.config_hash:
            return {
                "status": "BLOCKED",
                "reasons": ["CONFIG_HASH_MISMATCH"],
                "active": serialize_parameter_set_version(active),
            }
    except Exception:  # noqa: BLE001
        return {
            "status": "BLOCKED",
            "reasons": ["CONFIG_HASH_MISMATCH"],
            "active": serialize_parameter_set_version(active),
        }
    reasons: list[str] = []
    if snapshot != default_production_config():
        reasons.append("PARAMETER_DEFAULT_DRIFT")
    return {
        "status": "DEGRADED" if reasons else "OK",
        "reasons": reasons,
        "active": serialize_parameter_set_version(active),
    }


def list_parameter_set_versions(db: Session, *, limit: int = 100) -> list[ParameterSetVersion]:
    return list(
        db.execute(
            select(ParameterSetVersion)
            .order_by(ParameterSetVersion.version.desc(), ParameterSetVersion.id.desc())
            .limit(max(1, min(int(limit), 500)))
        ).scalars()
    )


def list_proposals(db: Session, *, user_id: int | None = None, limit: int = 100) -> list[ParameterChangeProposal]:
    statement = select(ParameterChangeProposal)
    if user_id is not None:
        statement = statement.where(ParameterChangeProposal.user_id == user_id)
    return list(
        db.execute(
            statement.order_by(ParameterChangeProposal.created_at.desc(), ParameterChangeProposal.id.desc()).limit(
                max(1, min(int(limit), 500))
            )
        ).scalars()
    )


def list_governance_events(db: Session, *, limit: int = 100) -> list[ParameterGovernanceEvent]:
    return list(
        db.execute(
            select(ParameterGovernanceEvent)
            .order_by(ParameterGovernanceEvent.occurred_at.desc(), ParameterGovernanceEvent.id.desc())
            .limit(max(1, min(int(limit), 500)))
        ).scalars()
    )


def serialize_parameter_set_version(row: ParameterSetVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "version": row.version,
        "status": row.status,
        "parent_version_id": row.parent_version_id,
        "created_by_user_id": row.created_by_user_id,
        "approved_by_user_id": row.approved_by_user_id,
        "source_proposal_id": row.source_proposal_id,
        "snapshot": row.snapshot_json,
        "diff": row.diff_json,
        "config_hash": row.config_hash,
        "runtime_contract_version": row.runtime_contract_version,
        "decision_contract_version": row.decision_contract_version,
        "validation": row.validation_json,
        "validation_status": row.validation_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "deactivated_at": row.deactivated_at.isoformat() if row.deactivated_at else None,
        "activation_reason": row.activation_reason,
        "rollback_from_version_id": row.rollback_from_version_id,
        "rollback_reason": row.rollback_reason,
    }


def serialize_proposal(row: ParameterChangeProposal) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "source_type": row.source_type,
        "source_calibration_report_id": row.source_calibration_report_id,
        "base_parameter_set_version_id": row.base_parameter_set_version_id,
        "target_parameter": row.target_parameter_key,
        "current_value": row.current_value_json,
        "proposed_value": row.proposed_value_json,
        "proposed_snapshot": row.proposed_snapshot_json,
        "proposal_type": row.proposal_type,
        "status": row.status,
        "evidence": row.evidence_summary_json,
        "risk_summary": row.risk_summary_json,
        "validation_summary": row.validation_summary_json,
        "reason": row.reason,
        "risk_acknowledged": row.risk_acknowledged,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "reviewed_by_user_id": row.reviewed_by_user_id,
        "review_comment": row.review_comment,
        "approved_version_id": row.approved_version_id,
    }


def serialize_governance_event(row: ParameterGovernanceEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "actor_user_id": row.actor_user_id,
        "event_type": row.event_type,
        "proposal_id": row.proposal_id,
        "parameter_set_version_id": row.parameter_set_version_id,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "metadata": row.metadata_json,
    }


__all__ = [
    "GovernanceBlockedError",
    "GovernanceError",
    "activate_parameter_set_version",
    "approve_proposal",
    "bootstrap_parameter_set",
    "create_manual_proposal",
    "create_proposal_from_calibration",
    "create_rollback_proposal",
    "get_active_parameter_set",
    "governance_health",
    "invalidate_parameter_cache",
    "lineage_fields",
    "list_governance_events",
    "list_parameter_set_versions",
    "list_proposals",
    "reject_proposal",
    "resolve_production_parameters",
    "serialize_governance_event",
    "serialize_parameter_set_version",
    "serialize_proposal",
    "submit_proposal",
    "validate_parameter_set_version",
]
