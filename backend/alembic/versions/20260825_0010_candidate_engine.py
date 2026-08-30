"""Add Phase F deterministic Candidate Engine persistence."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260825_0010"
down_revision = "20260824_0009"
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
    if "candidate_runs" not in tables:
        op.create_table(
            "candidate_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("calculation_key", sa.String(length=255), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("as_of", sa.DateTime(), nullable=False),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("portfolio_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("market_score_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("market_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=24), server_default="COMPLETED", nullable=False),
            sa.Column("mode", sa.String(length=16), server_default="standard", nullable=False),
            sa.Column("universe_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("eligible_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("prefilter_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("watchlist_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("ready_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("action_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("quality_status", sa.String(length=24), server_default="MISSING", nullable=False),
            sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
            sa.Column("calculation_version", sa.String(length=64), server_default="candidate-engine-v1", nullable=False),
            sa.Column("stock_score_version", sa.String(length=64), server_default="stock-opportunity-v1", nullable=False),
            sa.Column("etf_score_version", sa.String(length=64), server_default="etf-opportunity-v1", nullable=False),
            sa.Column("entry_score_version", sa.String(length=64), server_default="entry-score-v1", nullable=False),
            sa.Column("portfolio_fit_version", sa.String(length=64), server_default="portfolio-fit-v1", nullable=False),
            sa.Column("decision_edge_version", sa.String(length=64), server_default="decision-edge-v1", nullable=False),
            sa.Column("exclusion_counts_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_snapshot_id"], ["portfolio_snapshots.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("calculation_key", name="uq_candidate_runs_calculation_key"),
        )
        _indexes(
            "candidate_runs",
            (
                "user_id",
                "portfolio_id",
                "calculation_key",
                "trade_date",
                "as_of",
                "captured_at",
                "portfolio_snapshot_id",
                "market_score_snapshot_id",
                "market_snapshot_id",
                "status",
                "mode",
                "quality_status",
                "created_at",
            ),
        )
    if "candidate_scores" not in tables:
        op.create_table(
            "candidate_scores",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("candidate_run_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=16), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("security_type", sa.String(length=24), nullable=False),
            sa.Column("etf_category", sa.String(length=32), nullable=True),
            sa.Column("stage", sa.String(length=16), nullable=False),
            sa.Column("rank", sa.Integer(), server_default="0", nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("opportunity_score", sa.Float(), nullable=True),
            sa.Column("entry_score", sa.Float(), nullable=True),
            sa.Column("portfolio_fit_score", sa.Float(), nullable=True),
            sa.Column("action_score", sa.Float(), nullable=True),
            sa.Column("edge_vs_no_action", sa.Float(), nullable=True),
            sa.Column("edge_vs_current_holdings", sa.Float(), nullable=True),
            sa.Column("decision_edge", sa.Float(), nullable=True),
            sa.Column("risk_reward_ratio", sa.Float(), nullable=True),
            sa.Column("data_coverage", sa.Float(), server_default="0", nullable=False),
            sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
            sa.Column("quality_status", sa.String(length=24), server_default="INSUFFICIENT", nullable=False),
            sa.Column("probe_weight", sa.Float(), nullable=True),
            sa.Column("funding_mode", sa.String(length=32), nullable=True),
            sa.Column("components_json", sa.JSON(), nullable=True),
            sa.Column("portfolio_fit_json", sa.JSON(), nullable=True),
            sa.Column("entry_json", sa.JSON(), nullable=True),
            sa.Column("comparison_json", sa.JSON(), nullable=True),
            sa.Column("reason_codes_json", sa.JSON(), nullable=True),
            sa.Column("risk_flags_json", sa.JSON(), nullable=True),
            sa.Column("positive_drivers_json", sa.JSON(), nullable=True),
            sa.Column("negative_drivers_json", sa.JSON(), nullable=True),
            sa.Column("blocking_reasons_json", sa.JSON(), nullable=True),
            sa.Column("lineage_json", sa.JSON(), nullable=True),
            sa.Column("lifecycle", sa.String(length=16), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["candidate_run_id"], ["candidate_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("candidate_run_id", "code", name="uq_candidate_scores_run_code"),
        )
        _indexes(
            "candidate_scores",
            (
                "candidate_run_id",
                "code",
                "security_type",
                "etf_category",
                "stage",
                "rank",
                "quality_status",
                "funding_mode",
                "created_at",
            ),
        )


def downgrade() -> None:
    tables = _tables()
    if "candidate_scores" in tables:
        op.drop_table("candidate_scores")
    if "candidate_runs" in tables:
        op.drop_table("candidate_runs")
