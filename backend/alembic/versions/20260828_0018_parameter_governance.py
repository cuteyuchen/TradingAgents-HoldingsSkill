"""Add parameter governance tables and production lineage columns."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260828_0018"
down_revision = "20260827_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "parameter_set_versions" not in tables:
        op.create_table(
            "parameter_set_versions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"),
            sa.Column("parent_version_id", sa.Integer(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
            sa.Column("source_proposal_id", sa.Integer(), nullable=True),
            sa.Column("snapshot_json", sa.JSON(), nullable=True),
            sa.Column("diff_json", sa.JSON(), nullable=True),
            sa.Column("config_hash", sa.String(length=64), nullable=False),
            sa.Column("runtime_contract_version", sa.String(length=32), nullable=False, server_default="2.4.0"),
            sa.Column("decision_contract_version", sa.String(length=32), nullable=False, server_default="2.4.0"),
            sa.Column("validation_json", sa.JSON(), nullable=True),
            sa.Column("validation_status", sa.String(length=16), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("activated_at", sa.DateTime(), nullable=True),
            sa.Column("deactivated_at", sa.DateTime(), nullable=True),
            sa.Column("activation_reason", sa.Text(), nullable=True),
            sa.Column("rollback_from_version_id", sa.Integer(), nullable=True),
            sa.Column("rollback_reason", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["parent_version_id"], ["parameter_set_versions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["source_proposal_id"],
                ["parameter_change_proposals.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["rollback_from_version_id"], ["parameter_set_versions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("version", name="uq_parameter_set_versions_version"),
        )
        for name, columns in {
            "ix_parameter_set_versions_status": ["status"],
            "ix_parameter_set_versions_created_at": ["created_at"],
            "ix_parameter_set_versions_version": ["version"],
            "ix_parameter_set_versions_activated_at": ["activated_at"],
        }.items():
            op.create_index(name, "parameter_set_versions", columns)

    if "parameter_change_proposals" not in tables:
        op.create_table(
            "parameter_change_proposals",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("source_type", sa.String(length=32), nullable=False, server_default="MANUAL"),
            sa.Column("source_calibration_report_id", sa.Integer(), nullable=True),
            sa.Column("base_parameter_set_version_id", sa.Integer(), nullable=True),
            sa.Column("target_parameter_key", sa.String(length=160), nullable=False),
            sa.Column("current_value_json", sa.JSON(), nullable=True),
            sa.Column("proposed_value_json", sa.JSON(), nullable=True),
            sa.Column("proposed_snapshot_json", sa.JSON(), nullable=True),
            sa.Column("proposal_type", sa.String(length=32), nullable=False, server_default="STANDARD"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"),
            sa.Column("evidence_summary_json", sa.JSON(), nullable=True),
            sa.Column("risk_summary_json", sa.JSON(), nullable=True),
            sa.Column("validation_summary_json", sa.JSON(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("risk_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
            sa.Column("review_comment", sa.Text(), nullable=True),
            sa.Column("approved_version_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_calibration_report_id"], ["calibration_reports.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["base_parameter_set_version_id"], ["parameter_set_versions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["approved_version_id"], ["parameter_set_versions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, columns in {
            "ix_parameter_change_proposals_status": ["status"],
            "ix_parameter_change_proposals_created_at": ["created_at"],
            "ix_parameter_change_proposals_source_report": ["source_calibration_report_id"],
            "ix_parameter_change_proposals_user_id": ["user_id"],
            "ix_parameter_change_proposals_base_version": ["base_parameter_set_version_id"],
        }.items():
            op.create_index(name, "parameter_change_proposals", columns)

    if "parameter_governance_events" not in tables:
        op.create_table(
            "parameter_governance_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("actor_user_id", sa.Integer(), nullable=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("proposal_id", sa.Integer(), nullable=True),
            sa.Column("parameter_set_version_id", sa.Integer(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["proposal_id"], ["parameter_change_proposals.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["parameter_set_version_id"], ["parameter_set_versions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, columns in {
            "ix_parameter_governance_events_occurred_at": ["occurred_at"],
            "ix_parameter_governance_events_type": ["event_type"],
            "ix_parameter_governance_events_actor": ["actor_user_id"],
        }.items():
            op.create_index(name, "parameter_governance_events", columns)

    lineage_columns = {
        "parameter_set_version_id": sa.Column("parameter_set_version_id", sa.Integer(), nullable=True),
        "parameter_set_version": sa.Column("parameter_set_version", sa.String(length=64), nullable=True),
        "parameter_set_hash": sa.Column("parameter_set_hash", sa.String(length=64), nullable=True),
        "governance_lineage_json": sa.Column("governance_lineage_json", sa.JSON(), nullable=True),
    }
    for table_name in (
        "backtest_runs",
        "market_score_snapshots",
        "candidate_runs",
        "analysis_runs",
        "decision_memories",
    ):
        if table_name not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column in lineage_columns.items():
            if column_name not in existing:
                op.add_column(table_name, column)
        for column_name in ("parameter_set_version_id", "parameter_set_version", "parameter_set_hash"):
            op.create_index(f"ix_{table_name}_{column_name}", table_name, [column_name])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table_name in (
        "decision_memories",
        "analysis_runs",
        "candidate_runs",
        "market_score_snapshots",
        "backtest_runs",
    ):
        if table_name not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name in ("parameter_set_version_id", "parameter_set_version", "parameter_set_hash"):
            if column_name in existing:
                op.drop_index(f"ix_{table_name}_{column_name}", table_name=table_name)
        for column_name in ("governance_lineage_json", "parameter_set_hash", "parameter_set_version", "parameter_set_version_id"):
            if column_name in existing:
                op.drop_column(table_name, column_name)

    for table_name in ("parameter_governance_events", "parameter_change_proposals", "parameter_set_versions"):
        if table_name in tables:
            op.drop_table(table_name)
