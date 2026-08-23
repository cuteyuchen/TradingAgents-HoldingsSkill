"""Add deterministic Phase C market-engine snapshots.

Revision ID: 20260823_0006
Revises: 20260822_0005
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260823_0006"
down_revision = "20260822_0005"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    tables = _tables()
    if "market_metric_snapshots" not in tables:
        op.create_table(
            "market_metric_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("snapshot_id", sa.String(64), nullable=False),
            sa.Column("market_snapshot_id", sa.String(64), nullable=True),
            sa.Column("market", sa.String(16), server_default="CN", nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("universe_rule_version", sa.String(64), server_default="market-universe-v1", nullable=False),
            sa.Column("calculation_version", sa.String(64), server_default="market-engine-v1", nullable=False),
            sa.Column("score_config_version", sa.String(64), server_default="market-score-config-v1", nullable=False),
            sa.Column("universe_total", sa.Integer(), server_default="0", nullable=False),
            sa.Column("included_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("excluded_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("coverage", sa.Float(), server_default="0", nullable=False),
            sa.Column("median_return", sa.Float(), nullable=True),
            sa.Column("advance_ratio", sa.Float(), nullable=True),
            sa.Column("top5_concentration", sa.Float(), nullable=True),
            sa.Column("total_amount", sa.Float(), nullable=True),
            sa.Column("quality_status", sa.String(24), server_default="VALID", nullable=False),
            sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
            sa.Column("metrics_json", sa.JSON(), nullable=True),
            sa.Column("breadth_metrics_json", sa.JSON(), nullable=True),
            sa.Column("trend_metrics_json", sa.JSON(), nullable=True),
            sa.Column("liquidity_metrics_json", sa.JSON(), nullable=True),
            sa.Column("profitability_metrics_json", sa.JSON(), nullable=True),
            sa.Column("diffusion_metrics_json", sa.JSON(), nullable=True),
            sa.Column("crowding_metrics_json", sa.JSON(), nullable=True),
            sa.Column("tail_risk_metrics_json", sa.JSON(), nullable=True),
            sa.Column("exclusion_counts_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("snapshot_id", name="uq_market_metric_snapshots_snapshot_id"),
            sa.UniqueConstraint("market", "trade_date", "captured_at", name="uq_market_metric_snapshots_capture"),
        )
        _indexes("market_metric_snapshots", ("snapshot_id", "market_snapshot_id", "market", "trade_date", "captured_at", "quality_status", "created_at"))

    if "market_score_snapshots" not in tables:
        op.create_table(
            "market_score_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("snapshot_id", sa.String(64), nullable=False),
            sa.Column("metric_snapshot_id", sa.String(64), nullable=True),
            sa.Column("market", sa.String(16), server_default="CN", nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("raw_score", sa.Float(), nullable=True),
            sa.Column("display_score", sa.Float(), nullable=True),
            sa.Column("regime", sa.String(32), nullable=True),
            sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
            sa.Column("quality_status", sa.String(24), server_default="VALID", nullable=False),
            sa.Column("is_frozen", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("freeze_reason", sa.String(128), nullable=True),
            sa.Column("previous_display_score", sa.Float(), nullable=True),
            sa.Column("available_component_weight", sa.Float(), server_default="0", nullable=False),
            sa.Column("breadth_score", sa.Float(), nullable=True),
            sa.Column("trend_score", sa.Float(), nullable=True),
            sa.Column("liquidity_score", sa.Float(), nullable=True),
            sa.Column("profitability_score", sa.Float(), nullable=True),
            sa.Column("diffusion_score", sa.Float(), nullable=True),
            sa.Column("crowding_score", sa.Float(), nullable=True),
            sa.Column("tail_risk_score", sa.Float(), nullable=True),
            sa.Column("positive_drivers_json", sa.JSON(), nullable=True),
            sa.Column("negative_drivers_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("calculation_version", sa.String(64), server_default="market-engine-v1", nullable=False),
            sa.Column("score_config_version", sa.String(64), server_default="market-score-config-v1", nullable=False),
            sa.Column("universe_rule_version", sa.String(64), server_default="market-universe-v1", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("snapshot_id", name="uq_market_score_snapshots_snapshot_id"),
            sa.UniqueConstraint("market", "trade_date", "captured_at", name="uq_market_score_snapshots_capture"),
        )
        _indexes("market_score_snapshots", ("snapshot_id", "metric_snapshot_id", "market", "trade_date", "captured_at", "regime", "quality_status", "is_frozen", "created_at"))

    if "all_a_median_index_daily" not in tables:
        op.create_table(
            "all_a_median_index_daily",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(16), server_default="CN", nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("median_return", sa.Float(), nullable=False),
            sa.Column("index_value", sa.Float(), nullable=False),
            sa.Column("eligible_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("quality_status", sa.String(24), server_default="VALID", nullable=False),
            sa.Column("calculation_version", sa.String(64), server_default="market-engine-v1", nullable=False),
            sa.Column("available_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("market", "trade_date", "calculation_version", name="uq_all_a_median_index_daily_day_version"),
        )
        _indexes("all_a_median_index_daily", ("market", "trade_date", "quality_status", "created_at"))


def downgrade() -> None:
    tables = _tables()
    for table in ("all_a_median_index_daily", "market_score_snapshots", "market_metric_snapshots"):
        if table in tables:
            op.drop_table(table)
