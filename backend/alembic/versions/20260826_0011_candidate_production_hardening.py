"""Harden Phase F CandidateRun coverage and quote provenance."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260826_0011"
down_revision = "20260825_0010"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "candidate_runs" not in sa.inspect(op.get_bind()).get_table_names():
        return
    existing = _columns("candidate_runs")
    additions = {
        "quote_snapshot_id": sa.Column("quote_snapshot_id", sa.String(length=64), nullable=True),
        "structural_candidate_count": sa.Column("structural_candidate_count", sa.Integer(), server_default="0", nullable=False),
        "quote_ready_count": sa.Column("quote_ready_count", sa.Integer(), server_default="0", nullable=False),
        "bar_ready_count": sa.Column("bar_ready_count", sa.Integer(), server_default="0", nullable=False),
        "quote_coverage": sa.Column("quote_coverage", sa.Float(), server_default="0", nullable=False),
        "bar_coverage": sa.Column("bar_coverage", sa.Float(), server_default="0", nullable=False),
    }
    for name, column in additions.items():
        if name not in existing:
            op.add_column("candidate_runs", column)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("candidate_runs")}
    if "ix_candidate_runs_quote_snapshot_id" not in indexes:
        op.create_index("ix_candidate_runs_quote_snapshot_id", "candidate_runs", ["quote_snapshot_id"])


def downgrade() -> None:
    if "candidate_runs" not in sa.inspect(op.get_bind()).get_table_names():
        return
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("candidate_runs")}
    if "ix_candidate_runs_quote_snapshot_id" in indexes:
        op.drop_index("ix_candidate_runs_quote_snapshot_id", table_name="candidate_runs")
    existing = _columns("candidate_runs")
    for name in (
        "bar_coverage",
        "quote_coverage",
        "bar_ready_count",
        "quote_ready_count",
        "structural_candidate_count",
        "quote_snapshot_id",
    ):
        if name in existing:
            op.drop_column("candidate_runs", name)
