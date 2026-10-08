"""Add the lightweight Phase H daily orchestration state."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260826_0013"
down_revision = "20260826_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "daily_operational_runs" in inspector.get_table_names():
        return
    op.create_table(
        "daily_operational_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="RUNNING"),
        sa.Column("checkpoint_state_json", sa.JSON(), nullable=True),
        sa.Column("maintenance_result_json", sa.JSON(), nullable=True),
        sa.Column("notification_state_json", sa.JSON(), nullable=True),
        sa.Column("review_state_json", sa.JSON(), nullable=True),
        sa.Column("workflow_version", sa.String(length=64), nullable=False, server_default="daily-operations-v1"),
        sa.Column("last_tick_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "portfolio_id",
            "trade_date",
            "workflow_version",
            name="uq_daily_operational_portfolio_day_version",
        ),
    )
    for name, columns in {
        "ix_daily_operational_runs_user_id": ["user_id"],
        "ix_daily_operational_runs_portfolio_id": ["portfolio_id"],
        "ix_daily_operational_runs_trade_date": ["trade_date"],
        "ix_daily_operational_runs_status": ["status"],
        "ix_daily_operational_runs_last_tick_at": ["last_tick_at"],
    }.items():
        op.create_index(name, "daily_operational_runs", columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "daily_operational_runs" in inspector.get_table_names():
        op.drop_table("daily_operational_runs")
