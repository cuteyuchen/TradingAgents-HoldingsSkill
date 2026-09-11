"""Add failure_class to analysis_node_attempts for V3-CORE-2 retry classification.

Revision ID: 20260905_0022
Revises: 20260905_0021
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260905_0022"
down_revision = "20260905_0021"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "analysis_node_attempts" not in _tables():
        return
    if "failure_class" not in _columns("analysis_node_attempts"):
        op.add_column("analysis_node_attempts", sa.Column("failure_class", sa.String(length=32), nullable=True))


def downgrade() -> None:
    if "analysis_node_attempts" not in _tables():
        return
    if "failure_class" in _columns("analysis_node_attempts"):
        op.drop_column("analysis_node_attempts", "failure_class")
