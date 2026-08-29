"""Add durable checkpoint and operating-notification ownership claims."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260826_0015"
down_revision = "20260826_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "daily_operational_checkpoints" not in tables:
        op.create_table(
            "daily_operational_checkpoints",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("checkpoint_name", sa.String(length=32), nullable=False),
            sa.Column("workflow_version", sa.String(length=64), nullable=False, server_default="daily-operations-v1"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="CLAIMED"),
            sa.Column("job_id", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("claimed_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["job_id"], ["analysis_jobs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "portfolio_id", "trade_date", "checkpoint_name", "workflow_version",
                name="uq_daily_operational_checkpoint_owner",
            ),
        )
        for name, columns in {
            "ix_daily_operational_checkpoints_user_id": ["user_id"],
            "ix_daily_operational_checkpoints_portfolio_id": ["portfolio_id"],
            "ix_daily_operational_checkpoints_trade_date": ["trade_date"],
            "ix_daily_operational_checkpoints_checkpoint_name": ["checkpoint_name"],
            "ix_daily_operational_checkpoints_workflow_version": ["workflow_version"],
            "ix_daily_operational_checkpoints_status": ["status"],
            "ix_daily_operational_checkpoints_job_id": ["job_id"],
            "ix_daily_operational_checkpoints_claimed_at": ["claimed_at"],
        }.items():
            op.create_index(name, "daily_operational_checkpoints", columns)

    if "operating_notifications" not in tables:
        op.create_table(
            "operating_notifications",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("notification_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("dedupe_key", sa.String(length=255), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("severity", sa.String(length=24), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="DISPATCHING"),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("notification_id", name="uq_operating_notification_id"),
            sa.UniqueConstraint("user_id", "portfolio_id", "dedupe_key", name="uq_operating_notification_dedupe"),
        )
        for name, columns in {
            "ix_operating_notifications_notification_id": ["notification_id"],
            "ix_operating_notifications_user_id": ["user_id"],
            "ix_operating_notifications_portfolio_id": ["portfolio_id"],
            "ix_operating_notifications_trade_date": ["trade_date"],
            "ix_operating_notifications_dedupe_key": ["dedupe_key"],
            "ix_operating_notifications_event_type": ["event_type"],
            "ix_operating_notifications_severity": ["severity"],
            "ix_operating_notifications_status": ["status"],
            "ix_operating_notifications_occurred_at": ["occurred_at"],
            "ix_operating_notifications_read": ["read"],
        }.items():
            op.create_index(name, "operating_notifications", columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "operating_notifications" in tables:
        op.drop_table("operating_notifications")
    if "daily_operational_checkpoints" in tables:
        op.drop_table("daily_operational_checkpoints")
