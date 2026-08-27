"""Add restart-safe leases for Phase H operational claims."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260827_0016"
# Keep the hotfix independently installable from the Phase H head. Later
# feature branches may add their own migration chain after this revision.
down_revision = "20260826_0014"
branch_labels = ("phase_h1",)
depends_on = None


def _add_column_if_missing(table: str, column: str, definition: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    if table in inspector.get_table_names():
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column not in columns:
            op.add_column(table, definition)


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    if table in inspector.get_table_names():
        indexes = {item["name"] for item in inspector.get_indexes(table)}
        if name not in indexes:
            op.create_index(name, table, columns)


def upgrade() -> None:
    _add_column_if_missing(
        "daily_operational_checkpoints",
        "lease_expires_at",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )
    _add_column_if_missing(
        "daily_review_runs",
        "lease_expires_at",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )
    _add_column_if_missing(
        "daily_review_runs",
        "attempt_count",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
    )
    _add_column_if_missing(
        "operating_notifications",
        "lease_expires_at",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )
    _create_index_if_missing(
        "ix_daily_operational_checkpoints_lease_expires_at",
        "daily_operational_checkpoints",
        ["lease_expires_at"],
    )
    _create_index_if_missing(
        "ix_daily_review_runs_lease_expires_at",
        "daily_review_runs",
        ["lease_expires_at"],
    )
    _create_index_if_missing(
        "ix_operating_notifications_lease_expires_at",
        "operating_notifications",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table in (
        ("ix_operating_notifications_lease_expires_at", "operating_notifications"),
        ("ix_daily_review_runs_lease_expires_at", "daily_review_runs"),
        ("ix_daily_operational_checkpoints_lease_expires_at", "daily_operational_checkpoints"),
    ):
        if table in inspector.get_table_names() and name in {item["name"] for item in inspector.get_indexes(table)}:
            op.drop_index(name, table_name=table)
    for table, column in (
        ("operating_notifications", "lease_expires_at"),
        ("daily_review_runs", "attempt_count"),
        ("daily_review_runs", "lease_expires_at"),
        ("daily_operational_checkpoints", "lease_expires_at"),
    ):
        inspector = sa.inspect(op.get_bind())
        if table in inspector.get_table_names() and column in {item["name"] for item in inspector.get_columns(table)}:
            op.drop_column(table, column)
