"""Add V3-CORE-1 analysis workflow audit tables and AnalysisRun lifecycle fields.

Revision ID: 20260905_0021
Revises: 20260829_0020
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260905_0021"
down_revision = "20260829_0020"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _index(name: str, table: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    names = {item["name"] for item in inspector.get_indexes(table)}
    if name not in names:
        op.create_index(name, table, columns)


def upgrade() -> None:
    tables = _tables()
    if "analysis_runs" in tables:
        existing = _columns("analysis_runs")
        additions = {
            "status": sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
            "started_at": sa.Column("started_at", sa.DateTime(), nullable=True),
            "completed_at": sa.Column("completed_at", sa.DateTime(), nullable=True),
            "workflow_version": sa.Column("workflow_version", sa.String(length=32), nullable=True),
            "skill_version": sa.Column("skill_version", sa.String(length=64), nullable=True),
            "analysis_mode": sa.Column("analysis_mode", sa.String(length=16), nullable=True),
            "market_snapshot_at": sa.Column("market_snapshot_at", sa.DateTime(), nullable=True),
            "resumable": sa.Column("resumable", sa.Boolean(), nullable=False, server_default=sa.false()),
            "interrupted_at": sa.Column("interrupted_at", sa.DateTime(), nullable=True),
            "last_checkpoint": sa.Column("last_checkpoint", sa.String(length=32), nullable=True),
            "failed_stage": sa.Column("failed_stage", sa.String(length=64), nullable=True),
            "failed_node": sa.Column("failed_node", sa.String(length=64), nullable=True),
            "error_code": sa.Column("error_code", sa.String(length=64), nullable=True),
            "error_message": sa.Column("error_message", sa.Text(), nullable=True),
            "last_artifact_id": sa.Column("last_artifact_id", sa.Integer(), nullable=True),
        }
        for name, column in additions.items():
            if name not in existing:
                op.add_column("analysis_runs", column)
        _index("ix_analysis_runs_status", "analysis_runs", ["status"])
        _index("ix_analysis_runs_analysis_mode", "analysis_runs", ["analysis_mode"])
        _index("ix_analysis_runs_last_checkpoint", "analysis_runs", ["last_checkpoint"])

    if "analysis_stages" not in tables:
        op.create_table(
            "analysis_stages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=False),
            sa.Column("phase_key", sa.String(length=64), nullable=False),
            sa.Column("phase_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("display_name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("criticality", sa.String(length=16), nullable=False, server_default="important"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("input_hash", sa.String(length=64), nullable=True),
            sa.Column("output_hash", sa.String(length=64), nullable=True),
            sa.Column("quality_grade", sa.String(length=8), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("analysis_run_id", "phase_key", name="uq_analysis_stages_run_phase"),
        )
        _index("ix_analysis_stages_analysis_run_id", "analysis_stages", ["analysis_run_id"])
        _index("ix_analysis_stages_phase_key", "analysis_stages", ["phase_key"])
        _index("ix_analysis_stages_status", "analysis_stages", ["status"])

    if "analysis_nodes" not in tables:
        op.create_table(
            "analysis_nodes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=False),
            sa.Column("stage_id", sa.Integer(), nullable=False),
            sa.Column("node_key", sa.String(length=64), nullable=False),
            sa.Column("node_type", sa.String(length=32), nullable=False, server_default="legacy"),
            sa.Column("agent_role", sa.String(length=32), nullable=False, server_default="system"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("criticality", sa.String(length=16), nullable=False, server_default="important"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("input_artifact_id", sa.Integer(), nullable=True),
            sa.Column("output_artifact_id", sa.Integer(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("resumable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["stage_id"], ["analysis_stages.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("analysis_run_id", "node_key", name="uq_analysis_nodes_run_key"),
        )
        _index("ix_analysis_nodes_analysis_run_id", "analysis_nodes", ["analysis_run_id"])
        _index("ix_analysis_nodes_stage_id", "analysis_nodes", ["stage_id"])
        _index("ix_analysis_nodes_node_key", "analysis_nodes", ["node_key"])
        _index("ix_analysis_nodes_status", "analysis_nodes", ["status"])

    if "analysis_node_attempts" not in tables:
        op.create_table(
            "analysis_node_attempts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=False),
            sa.Column("stage_id", sa.Integer(), nullable=False),
            sa.Column("node_id", sa.Integer(), nullable=False),
            sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("provider", sa.String(length=64), nullable=True),
            sa.Column("model", sa.String(length=128), nullable=True),
            sa.Column("model_profile_id", sa.Integer(), nullable=True),
            sa.Column("request_id", sa.String(length=128), nullable=True),
            sa.Column("input_hash", sa.String(length=64), nullable=True),
            sa.Column("output_hash", sa.String(length=64), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("transport_retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("structured_retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_type", sa.String(length=64), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("raw_output_artifact_id", sa.Integer(), nullable=True),
            sa.Column("structured_output_artifact_id", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["stage_id"], ["analysis_stages.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["node_id"], ["analysis_nodes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("node_id", "attempt_no", name="uq_analysis_node_attempts_node_no"),
        )
        _index("ix_analysis_node_attempts_analysis_run_id", "analysis_node_attempts", ["analysis_run_id"])
        _index("ix_analysis_node_attempts_node_id", "analysis_node_attempts", ["node_id"])
        _index("ix_analysis_node_attempts_status", "analysis_node_attempts", ["status"])

    if "analysis_artifacts" not in tables:
        op.create_table(
            "analysis_artifacts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=False),
            sa.Column("stage_id", sa.Integer(), nullable=True),
            sa.Column("node_id", sa.Integer(), nullable=True),
            sa.Column("attempt_id", sa.Integer(), nullable=True),
            sa.Column("artifact_type", sa.String(length=32), nullable=False),
            sa.Column("artifact_key", sa.String(length=128), nullable=False),
            sa.Column("content_json", sa.JSON(), nullable=True),
            sa.Column("content_text", sa.Text(), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("content_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("content_encoding", sa.String(length=32), nullable=False, server_default="utf-8"),
            sa.Column("mime_type", sa.String(length=64), nullable=False, server_default="application/json"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["stage_id"], ["analysis_stages.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["node_id"], ["analysis_nodes.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["attempt_id"], ["analysis_node_attempts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        _index("ix_analysis_artifacts_analysis_run_id", "analysis_artifacts", ["analysis_run_id"])
        _index("ix_analysis_artifacts_artifact_type", "analysis_artifacts", ["artifact_type"])
        _index("ix_analysis_artifacts_sha256", "analysis_artifacts", ["sha256"])
        _index("ix_analysis_artifacts_created_at", "analysis_artifacts", ["created_at"])

    if "analysis_claims" not in tables:
        op.create_table(
            "analysis_claims",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=False),
            sa.Column("stage_id", sa.Integer(), nullable=True),
            sa.Column("node_id", sa.Integer(), nullable=True),
            sa.Column("claim_id", sa.String(length=32), nullable=False),
            sa.Column("debate_type", sa.String(length=32), nullable=False, server_default="investment"),
            sa.Column("speaker", sa.String(length=32), nullable=True),
            sa.Column("stance", sa.String(length=32), nullable=True),
            sa.Column("statement", sa.Text(), nullable=False, server_default=""),
            sa.Column("evidence_refs_json", sa.JSON(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("parent_claim_id", sa.String(length=32), nullable=True),
            sa.Column("target_claim_ids_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["stage_id"], ["analysis_stages.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["node_id"], ["analysis_nodes.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("analysis_run_id", "claim_id", name="uq_analysis_claims_run_claim"),
        )
        _index("ix_analysis_claims_analysis_run_id", "analysis_claims", ["analysis_run_id"])
        _index("ix_analysis_claims_claim_id", "analysis_claims", ["claim_id"])
        _index("ix_analysis_claims_status", "analysis_claims", ["status"])


def downgrade() -> None:
    tables = _tables()
    for table in ("analysis_claims", "analysis_artifacts", "analysis_node_attempts", "analysis_nodes", "analysis_stages"):
        if table in tables:
            op.drop_table(table)
    if "analysis_runs" in _tables():
        existing = _columns("analysis_runs")
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("analysis_runs")}
        for name in ("ix_analysis_runs_last_checkpoint", "ix_analysis_runs_analysis_mode", "ix_analysis_runs_status"):
            if name in indexes:
                op.drop_index(name, table_name="analysis_runs")
        for column_name in (
            "last_artifact_id",
            "error_message",
            "error_code",
            "failed_node",
            "failed_stage",
            "last_checkpoint",
            "interrupted_at",
            "resumable",
            "market_snapshot_at",
            "analysis_mode",
            "skill_version",
            "workflow_version",
            "completed_at",
            "started_at",
            "status",
        ):
            if column_name in existing:
                op.drop_column("analysis_runs", column_name)
