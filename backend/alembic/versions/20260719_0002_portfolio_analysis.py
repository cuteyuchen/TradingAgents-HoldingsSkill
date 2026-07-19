"""Add portfolios, uploads, analysis, schedules, and notifications.

Revision ID: 20260719_0002
Revises: 20260719_0001
Create Date: 2026-07-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260719_0002"
down_revision: str | None = "20260719_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "users" in tables and "timezone" not in _columns("users"):
        op.add_column("users", sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Shanghai"))

    if "portfolios" not in tables:
        op.create_table(
            "portfolios",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="A_SHARE"),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "name", name="uq_portfolio_user_name"),
        )
        op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])
        op.create_index("ix_portfolios_is_default", "portfolios", ["is_default"])

    if "holding_uploads" not in tables:
        op.create_table(
            "holding_uploads",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("storage_path", sa.String(length=1024), nullable=False),
            sa.Column("mime_type", sa.String(length=64), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("parsing_status", sa.String(length=32), nullable=False, server_default="uploaded"),
            sa.Column("vision_model_profile_id", sa.Integer(), sa.ForeignKey("model_profiles.id", ondelete="SET NULL"), nullable=True),
            sa.Column("parsed_json", sa.JSON(), nullable=True),
            sa.Column("validation_errors", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_holding_uploads_user_id", "holding_uploads", ["user_id"])
        op.create_index("ix_holding_uploads_portfolio_id", "holding_uploads", ["portfolio_id"])
        op.create_index("ix_holding_uploads_sha256", "holding_uploads", ["sha256"])
        op.create_index("ix_holding_uploads_parsing_status", "holding_uploads", ["parsing_status"])

    if "portfolio_snapshots" not in tables:
        op.create_table(
            "portfolio_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("upload_id", sa.Integer(), sa.ForeignKey("holding_uploads.id", ondelete="SET NULL"), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="screenshot"),
            sa.Column("snapshot_time", sa.DateTime(), nullable=False),
            sa.Column("total_assets", sa.Float(), nullable=True),
            sa.Column("total_market_value", sa.Float(), nullable=True),
            sa.Column("broker_available_cash", sa.Float(), nullable=True),
            sa.Column("corrected_unused_funds", sa.Float(), nullable=True),
            sa.Column("repo_or_standard_bond_value", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
            sa.Column("raw_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_portfolio_snapshots_user_id", "portfolio_snapshots", ["user_id"])
        op.create_index("ix_portfolio_snapshots_portfolio_id", "portfolio_snapshots", ["portfolio_id"])
        op.create_index("ix_portfolio_snapshots_upload_id", "portfolio_snapshots", ["upload_id"])
        op.create_index("ix_portfolio_snapshots_snapshot_time", "portfolio_snapshots", ["snapshot_time"])
        op.create_index("ix_portfolio_snapshots_status", "portfolio_snapshots", ["status"])

    if "holding_items" not in tables:
        op.create_table(
            "holding_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("code", sa.String(length=16), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=True),
            sa.Column("market", sa.String(length=16), nullable=True),
            sa.Column("qty", sa.Float(), nullable=True),
            sa.Column("available_qty", sa.Float(), nullable=True),
            sa.Column("unavailable_qty", sa.Float(), nullable=True),
            sa.Column("cost", sa.Float(), nullable=True),
            sa.Column("screenshot_price", sa.Float(), nullable=True),
            sa.Column("market_value", sa.Float(), nullable=True),
            sa.Column("pnl_ratio", sa.Float(), nullable=True),
            sa.Column("pnl_amount", sa.Float(), nullable=True),
            sa.Column("weight", sa.Float(), nullable=True),
            sa.Column("extra_json", sa.JSON(), nullable=True),
            sa.UniqueConstraint("snapshot_id", "code", name="uq_holding_item_snapshot_code"),
        )
        op.create_index("ix_holding_items_snapshot_id", "holding_items", ["snapshot_id"])
        op.create_index("ix_holding_items_code", "holding_items", ["code"])

    if "analysis_jobs" not in tables:
        op.create_table(
            "analysis_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("trigger_type", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("checkpoint", sa.String(length=16), nullable=True),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="deep"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("current_stage", sa.String(length=64), nullable=False, server_default="queued"),
            sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_analysis_jobs_idempotency_key"),
        )
        for column in ("user_id", "portfolio_id", "snapshot_id", "trigger_type", "checkpoint", "status", "idempotency_key"):
            op.create_index(f"ix_analysis_jobs_{column}", "analysis_jobs", [column])

    if "analysis_runs" not in tables:
        op.create_table(
            "analysis_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.Integer(), sa.ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("portfolio_snapshot_id", sa.Integer(), sa.ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("model_profile_id", sa.Integer(), sa.ForeignKey("model_profiles.id", ondelete="SET NULL"), nullable=True),
            sa.Column("data_quality_grade", sa.String(length=4), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("final_rating", sa.String(length=32), nullable=True),
            sa.Column("cash_target", sa.String(length=64), nullable=True),
            sa.Column("confidence", sa.String(length=16), nullable=True),
            sa.Column("structured_result_json", sa.JSON(), nullable=True),
            sa.Column("markdown_text", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        for column in ("job_id", "user_id", "portfolio_snapshot_id", "final_rating", "created_at"):
            op.create_index(f"ix_analysis_runs_{column}", "analysis_runs", [column])

    if "schedules" not in tables:
        op.create_table(
            "schedules",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Shanghai"),
            sa.Column("hour", sa.Integer(), nullable=False, server_default="9"),
            sa.Column("minute", sa.Integer(), nullable=False, server_default="35"),
            sa.Column("checkpoint", sa.String(length=16), nullable=False, server_default="09:35"),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="deep"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("stale_snapshot_days", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("max_consecutive_failures", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "portfolio_id", "name", name="uq_schedule_user_portfolio_name"),
        )
        op.create_index("ix_schedules_user_id", "schedules", ["user_id"])
        op.create_index("ix_schedules_portfolio_id", "schedules", ["portfolio_id"])
        op.create_index("ix_schedules_enabled", "schedules", ["enabled"])

    if "notification_channels" not in tables:
        op.create_table(
            "notification_channels",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("type", sa.String(length=16), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("encrypted_webhook", sa.Text(), nullable=False),
            sa.Column("encrypted_secret", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_test_status", sa.String(length=32), nullable=True),
            sa.Column("last_test_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "name", name="uq_notification_user_name"),
        )
        op.create_index("ix_notification_channels_user_id", "notification_channels", ["user_id"])
        op.create_index("ix_notification_channels_type", "notification_channels", ["type"])

    if "notification_deliveries" not in tables:
        op.create_table(
            "notification_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("channel_id", sa.Integer(), sa.ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("response_code", sa.Integer(), nullable=True),
            sa.Column("response_excerpt", sa.Text(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_notification_deliveries_channel_id", "notification_deliveries", ["channel_id"])
        op.create_index("ix_notification_deliveries_analysis_run_id", "notification_deliveries", ["analysis_run_id"])
        op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])


def downgrade() -> None:
    for table in [
        "notification_deliveries",
        "notification_channels",
        "schedules",
        "analysis_runs",
        "analysis_jobs",
        "holding_items",
        "portfolio_snapshots",
        "holding_uploads",
        "portfolios",
    ]:
        if table in _tables():
            op.drop_table(table)
