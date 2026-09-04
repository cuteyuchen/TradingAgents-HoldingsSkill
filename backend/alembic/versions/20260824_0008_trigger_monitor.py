"""Add Phase D trigger plans, trigger events, and analysis context."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260824_0008"
down_revision = "20260823_0007"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    for column in columns:
        name = f"ix_{table}_{column}"
        if name not in existing:
            op.create_index(name, table, [column])


def upgrade() -> None:
    tables = _tables()
    if "analysis_jobs" in tables:
        columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("analysis_jobs")}
        if "context_json" not in columns:
            op.add_column("analysis_jobs", sa.Column("context_json", sa.JSON(), nullable=True))

    if "trigger_plans" not in tables:
        op.create_table(
            "trigger_plans",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_id", sa.Integer(), nullable=True),
            sa.Column("scope", sa.String(16), server_default="USER", nullable=False),
            sa.Column("target_type", sa.String(24), nullable=False),
            sa.Column("target_key", sa.String(128), nullable=False),
            sa.Column("trigger_type", sa.String(32), nullable=False),
            sa.Column("metric", sa.String(64), nullable=False),
            sa.Column("operator", sa.String(16), nullable=False),
            sa.Column("threshold", sa.Float(), nullable=False),
            sa.Column("secondary_threshold", sa.Float(), nullable=True),
            sa.Column("priority", sa.String(4), server_default="P1", nullable=False),
            sa.Column("debounce_cycles", sa.Integer(), server_default="2", nullable=False),
            sa.Column("debounce_seconds", sa.Integer(), server_default="180", nullable=False),
            sa.Column("cooldown_seconds", sa.Integer(), server_default="1800", nullable=False),
            sa.Column("valid_from", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("source_type", sa.String(32), server_default="MANUAL", nullable=False),
            sa.Column("source_id", sa.String(128), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("last_evaluated_at", sa.DateTime(), nullable=True),
            sa.Column("last_triggered_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        _indexes(
            "trigger_plans",
            ("user_id", "portfolio_id", "scope", "target_type", "target_key", "trigger_type", "priority", "expires_at", "enabled", "source_type", "source_id"),
        )
        op.create_index("ix_trigger_plans_target", "trigger_plans", ["target_type", "target_key"])

    if "trigger_events" not in tables:
        op.create_table(
            "trigger_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("trigger_plan_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_id", sa.Integer(), nullable=True),
            sa.Column("trigger_type", sa.String(32), nullable=False),
            sa.Column("target_type", sa.String(24), nullable=False),
            sa.Column("target_key", sa.String(128), nullable=False),
            sa.Column("priority", sa.String(4), server_default="P2", nullable=False),
            sa.Column("status", sa.String(16), server_default="DETECTED", nullable=False),
            sa.Column("detected_at", sa.DateTime(), nullable=False),
            sa.Column("first_detected_at", sa.DateTime(), nullable=False),
            sa.Column("consecutive_hits", sa.Integer(), server_default="1", nullable=False),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("metric", sa.String(64), nullable=True),
            sa.Column("previous_value", sa.Float(), nullable=True),
            sa.Column("current_value", sa.Float(), nullable=True),
            sa.Column("threshold", sa.Float(), nullable=True),
            sa.Column("evidence_json", sa.JSON(), nullable=True),
            sa.Column("market_snapshot_id", sa.String(64), nullable=True),
            sa.Column("market_score_snapshot_id", sa.String(64), nullable=True),
            sa.Column("portfolio_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("analysis_job_id", sa.Integer(), nullable=True),
            sa.Column("analysis_run_id", sa.Integer(), nullable=True),
            sa.Column("resolution", sa.String(32), nullable=True),
            sa.Column("dedupe_key", sa.String(512), nullable=False),
            sa.Column("rule_id", sa.String(128), nullable=True),
            sa.Column("rule_version", sa.String(64), server_default="trigger-engine-v1", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["trigger_plan_id"], ["trigger_plans.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_snapshot_id"], ["portfolio_snapshots.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["analysis_job_id"], ["analysis_jobs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        _indexes(
            "trigger_events",
            ("trigger_plan_id", "user_id", "portfolio_id", "trigger_type", "target_type", "target_key", "priority", "status", "detected_at", "expires_at", "market_snapshot_id", "market_score_snapshot_id", "portfolio_snapshot_id", "analysis_job_id", "analysis_run_id", "resolution", "dedupe_key", "rule_id"),
        )
        op.create_index("ix_trigger_events_dedupe_detected", "trigger_events", ["dedupe_key", "detected_at"])


def downgrade() -> None:
    tables = _tables()
    for table in ("trigger_events", "trigger_plans"):
        if table in tables:
            op.drop_table(table)
    if "analysis_jobs" in tables:
        columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("analysis_jobs")}
        if "context_json" in columns:
            with op.batch_alter_table("analysis_jobs") as batch:
                batch.drop_column("context_json")
