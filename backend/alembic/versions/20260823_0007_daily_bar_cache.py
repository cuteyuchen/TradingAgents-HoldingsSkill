"""Add the local normalized daily-bar cache used by Market Engine."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260823_0007"
down_revision = "20260823_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "daily_bar_cache" in inspector.get_table_names():
        return
    op.create_table(
        "daily_bar_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market", sa.String(16), server_default="CN", nullable=False),
        sa.Column("exchange", sa.String(8), nullable=True),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("prev_close", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("adjustment", sa.String(8), server_default="QFQ", nullable=False),
        sa.Column("provider", sa.String(64), server_default="", nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=True),
        sa.Column("quality_status", sa.String(24), server_default="VALID", nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "market", "code", "trade_date", "adjustment",
            name="uq_daily_bar_cache_market_code_day_adjustment",
        ),
    )
    for column in ("market", "exchange", "code", "trade_date", "adjustment", "available_at", "quality_status", "created_at"):
        op.create_index(f"ix_daily_bar_cache_{column}", "daily_bar_cache", [column])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "daily_bar_cache" in inspector.get_table_names():
        op.drop_table("daily_bar_cache")
