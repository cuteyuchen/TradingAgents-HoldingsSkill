"""Track stale Review state and same-run refresh metadata."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260826_0014"
down_revision = "20260826_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("daily_review_runs")}
    if "review_stale" not in columns:
        op.add_column("daily_review_runs", sa.Column("review_stale", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "last_refreshed_at" not in columns:
        op.add_column("daily_review_runs", sa.Column("last_refreshed_at", sa.DateTime(), nullable=True))
    if "refresh_count" not in columns:
        op.add_column("daily_review_runs", sa.Column("refresh_count", sa.Integer(), nullable=False, server_default="0"))
    indexes = {index["name"] for index in inspector.get_indexes("daily_review_runs")}
    if "ix_daily_review_runs_review_stale" not in indexes:
        op.create_index("ix_daily_review_runs_review_stale", "daily_review_runs", ["review_stale"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("daily_review_runs")}
    if "ix_daily_review_runs_review_stale" in indexes:
        op.drop_index("ix_daily_review_runs_review_stale", table_name="daily_review_runs")
    columns = {column["name"] for column in inspector.get_columns("daily_review_runs")}
    for name in ("refresh_count", "last_refreshed_at", "review_stale"):
        if name in columns:
            op.drop_column("daily_review_runs", name)
