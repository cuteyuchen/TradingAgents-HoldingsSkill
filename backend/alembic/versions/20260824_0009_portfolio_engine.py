"""Add Phase E portfolio ledger, snapshot diff, and risk snapshot facts."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260824_0009"
down_revision = "20260824_0008"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    for column in columns:
        name = f"ix_{table}_{column}"
        if name not in existing:
            op.create_index(name, table, [column])


def upgrade() -> None:
    tables = _tables()
    if "trade_ledger_entries" not in tables:
        op.create_table(
            "trade_ledger_entries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=True),
            sa.Column("trigger_event_id", sa.Integer(), nullable=True),
            sa.Column("entry_type", sa.String(24), nullable=False),
            sa.Column("security_code", sa.String(16), nullable=True),
            sa.Column("security_name", sa.String(128), nullable=True),
            sa.Column("side", sa.String(8), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("gross_amount", sa.Float(), nullable=True),
            sa.Column("fees", sa.Float(), nullable=True),
            sa.Column("taxes", sa.Float(), nullable=True),
            sa.Column("net_amount", sa.Float(), nullable=True),
            sa.Column("currency", sa.String(8), server_default="CNY", nullable=False),
            sa.Column("executed_at", sa.DateTime(), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("available_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(32), server_default="MANUAL", nullable=False),
            sa.Column("source_ref", sa.String(255), nullable=True),
            sa.Column("broker_order_id", sa.String(128), nullable=True),
            sa.Column("idempotency_key", sa.String(255), nullable=True),
            sa.Column("status", sa.String(24), server_default="CONFIRMED", nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["trigger_event_id"], ["trigger_events.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("portfolio_id", "idempotency_key", name="uq_trade_ledger_portfolio_idempotency"),
        )
        _indexes("trade_ledger_entries", (
            "user_id", "portfolio_id", "analysis_run_id", "trigger_event_id", "entry_type", "security_code", "side",
            "executed_at", "trade_date", "available_at", "source", "source_ref", "broker_order_id", "idempotency_key", "status", "created_at",
        ))
    if "trade_ledger_revisions" not in tables:
        op.create_table(
            "trade_ledger_revisions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("ledger_entry_id", sa.Integer(), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("changes_json", sa.JSON(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["ledger_entry_id"], ["trade_ledger_entries.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ledger_entry_id", "revision_no", name="uq_trade_ledger_revision_number"),
        )
        _indexes("trade_ledger_revisions", ("ledger_entry_id", "created_by_user_id", "created_at"))
    if "portfolio_snapshot_diffs" not in tables:
        op.create_table(
            "portfolio_snapshot_diffs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("before_snapshot_id", sa.Integer(), nullable=False),
            sa.Column("after_snapshot_id", sa.Integer(), nullable=False),
            sa.Column("diff_json", sa.JSON(), nullable=False),
            sa.Column("reconciliation_status", sa.String(32), server_default="UNEXPLAINED", nullable=False),
            sa.Column("calculation_version", sa.String(64), server_default="portfolio-diff-v1", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["before_snapshot_id"], ["portfolio_snapshots.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["after_snapshot_id"], ["portfolio_snapshots.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("before_snapshot_id", "after_snapshot_id", name="uq_portfolio_snapshot_diff_pair"),
        )
        _indexes("portfolio_snapshot_diffs", ("user_id", "portfolio_id", "before_snapshot_id", "after_snapshot_id", "reconciliation_status", "created_at"))
    if "portfolio_risk_snapshots" not in tables:
        op.create_table(
            "portfolio_risk_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("calculation_key", sa.String(160), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_snapshot_id", sa.Integer(), nullable=False),
            sa.Column("market_score_snapshot_id", sa.String(64), nullable=True),
            sa.Column("as_of", sa.DateTime(), nullable=False),
            sa.Column("total_assets", sa.Float(), nullable=True),
            sa.Column("market_value", sa.Float(), nullable=True),
            sa.Column("cash_ratio", sa.Float(), nullable=True),
            sa.Column("gross_exposure", sa.Float(), nullable=True),
            sa.Column("top1_weight", sa.Float(), nullable=True),
            sa.Column("top3_weight", sa.Float(), nullable=True),
            sa.Column("top5_weight", sa.Float(), nullable=True),
            sa.Column("hhi", sa.Float(), nullable=True),
            sa.Column("portfolio_vol_20", sa.Float(), nullable=True),
            sa.Column("portfolio_vol_60", sa.Float(), nullable=True),
            sa.Column("weighted_average_correlation", sa.Float(), nullable=True),
            sa.Column("max_pairwise_correlation", sa.Float(), nullable=True),
            sa.Column("unclassified_weight", sa.Float(), nullable=True),
            sa.Column("risk_flags_json", sa.JSON(), nullable=True),
            sa.Column("position_metrics_json", sa.JSON(), nullable=True),
            sa.Column("correlation_summary_json", sa.JSON(), nullable=True),
            sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
            sa.Column("quality_status", sa.String(24), server_default="DEGRADED", nullable=False),
            sa.Column("calculation_version", sa.String(64), server_default="portfolio-risk-v1", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_snapshot_id"], ["portfolio_snapshots.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("calculation_key", name="uq_portfolio_risk_calculation_key"),
        )
        _indexes("portfolio_risk_snapshots", (
            "calculation_key", "user_id", "portfolio_id", "portfolio_snapshot_id", "market_score_snapshot_id", "as_of",
            "quality_status", "created_at",
        ))


def downgrade() -> None:
    tables = _tables()
    for table in ("portfolio_risk_snapshots", "portfolio_snapshot_diffs", "trade_ledger_revisions", "trade_ledger_entries"):
        if table in tables:
            op.drop_table(table)
