"""Add provider health, source lineage, and market snapshot metadata.

Revision ID: 20260822_0005
Revises: 20260822_0004
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0005"
down_revision: str | None = "20260822_0004"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "provider_health" not in tables:
        op.create_table(
            "provider_health",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("provider_name", sa.String(length=64), nullable=False),
            sa.Column("data_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=24), server_default="HEALTHY", nullable=False),
            sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
            sa.Column("last_success_at", sa.DateTime(), nullable=True),
            sa.Column("last_failure_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_latency_ms", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_name", "data_type", name="uq_provider_health_provider_data_type"),
        )
        op.create_index("ix_provider_health_provider_name", "provider_health", ["provider_name"])
        op.create_index("ix_provider_health_data_type", "provider_health", ["data_type"])
        op.create_index("ix_provider_health_status", "provider_health", ["status"])

    if "source_lineage" not in tables:
        op.create_table(
            "source_lineage",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("entity_key", sa.String(length=128), nullable=False),
            sa.Column("field_name", sa.String(length=128), nullable=True),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("provider_endpoint", sa.String(length=512), nullable=True),
            sa.Column("operation", sa.String(length=128), nullable=True),
            sa.Column("source_timestamp", sa.DateTime(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=True),
            sa.Column("fallback_level", sa.Integer(), server_default="0", nullable=False),
            sa.Column("quality_status", sa.String(length=24), server_default="VALID", nullable=False),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("entity_type", "entity_key", "field_name", "provider", "fetched_at", "trade_date", "quality_status"):
            op.create_index(f"ix_source_lineage_{column}", "source_lineage", [column])

    if "market_snapshots" not in tables:
        op.create_table(
            "market_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("snapshot_id", sa.String(length=64), nullable=False),
            sa.Column("snapshot_key", sa.String(length=128), nullable=True),
            sa.Column("market", sa.String(length=16), server_default="CN", nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=True),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("fallback_level", sa.Integer(), server_default="0", nullable=False),
            sa.Column("expected_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("received_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("coverage_ratio", sa.Float(), server_default="0", nullable=False),
            sa.Column("quality_status", sa.String(length=24), server_default="MISSING", nullable=False),
            sa.Column("errors_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("snapshot_id", name="uq_market_snapshots_snapshot_id"),
        )
        for column in ("snapshot_id", "snapshot_key", "market", "started_at", "completed_at", "trade_date", "provider", "quality_status", "created_at"):
            op.create_index(f"ix_market_snapshots_{column}", "market_snapshots", [column])


def downgrade() -> None:
    tables = _tables()
    if "market_snapshots" in tables:
        op.drop_table("market_snapshots")
    if "source_lineage" in tables:
        op.drop_table("source_lineage")
    if "provider_health" in tables:
        op.drop_table("provider_health")
