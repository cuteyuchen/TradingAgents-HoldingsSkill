"""Immutable governance records for production parameter versions, proposals, and audit."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from ..clock import utc_now
from ..database import Base


def utcnow() -> datetime:
    return utc_now()


class ParameterSetVersion(Base):
    """One immutable, complete production parameter snapshot."""

    __tablename__ = "parameter_set_versions"
    __table_args__ = (
        UniqueConstraint("version", name="uq_parameter_set_versions_version"),
        Index("ix_parameter_set_versions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("parameter_set_versions.id", ondelete="SET NULL"), index=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    source_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("parameter_change_proposals.id", ondelete="SET NULL"), index=True
    )
    snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    diff_json: Mapped[dict | None] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    runtime_contract_version: Mapped[str] = mapped_column(String(32), default="2.4.0")
    decision_contract_version: Mapped[str] = mapped_column(String(32), default="2.4.0")
    validation_json: Mapped[dict | None] = mapped_column(JSON)
    validation_status: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime)
    activation_reason: Mapped[str | None] = mapped_column(Text)
    rollback_from_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("parameter_set_versions.id", ondelete="SET NULL"), index=True
    )
    rollback_reason: Mapped[str | None] = mapped_column(Text)


class ParameterChangeProposal(Base):
    """Human-reviewed proposal. Approval never activates a version by itself."""

    __tablename__ = "parameter_change_proposals"
    __table_args__ = (
        Index("ix_parameter_change_proposals_created_at", "created_at"),
        Index("ix_parameter_change_proposals_source_report", "source_calibration_report_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="MANUAL")
    source_calibration_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("calibration_reports.id", ondelete="SET NULL"), index=True
    )
    base_parameter_set_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("parameter_set_versions.id", ondelete="SET NULL"), index=True
    )
    target_parameter_key: Mapped[str] = mapped_column(String(160), index=True)
    current_value_json: Mapped[object | None] = mapped_column(JSON)
    proposed_value_json: Mapped[object | None] = mapped_column(JSON)
    proposed_snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    proposal_type: Mapped[str] = mapped_column(String(32), default="STANDARD")
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    evidence_summary_json: Mapped[dict | None] = mapped_column(JSON)
    risk_summary_json: Mapped[dict | None] = mapped_column(JSON)
    validation_summary_json: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    risk_acknowledged: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text)
    approved_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("parameter_set_versions.id", ondelete="SET NULL"), index=True
    )


class ParameterGovernanceEvent(Base):
    """Append-only audit trail. Events can never be updated or deleted."""

    __tablename__ = "parameter_governance_events"
    __table_args__ = (
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("parameter_change_proposals.id", ondelete="SET NULL"), index=True
    )
    parameter_set_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("parameter_set_versions.id", ondelete="SET NULL"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


def _immutable_update(_mapper, _connection, target) -> None:
    if getattr(target, "_allow_governance_update", False) is not True:
        raise RuntimeError("governance_record_is_immutable")


def _immutable_delete(_mapper, _connection, target) -> None:
    raise RuntimeError("governance_record_is_immutable")


def _clear_update_flag(_mapper, _connection, target) -> None:
    if hasattr(target, "_allow_governance_update"):
        setattr(target, "_allow_governance_update", False)


for model in (ParameterSetVersion, ParameterChangeProposal, ParameterGovernanceEvent):
    event.listen(model, "before_update", _immutable_update)
    event.listen(model, "before_delete", _immutable_delete)
    event.listen(model, "after_update", _clear_update_flag)


__all__ = [
    "ParameterChangeProposal",
    "ParameterGovernanceEvent",
    "ParameterSetVersion",
]
