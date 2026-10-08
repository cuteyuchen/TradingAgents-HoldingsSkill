"""Add immutable Alpha Memory, derived Outcomes, and Daily Review."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260826_0012"
down_revision = "20260826_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "decision_memories" not in tables:
        op.create_table(
            "decision_memories",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=False),
            sa.Column("analysis_job_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_risk_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("candidate_run_id", sa.Integer(), nullable=True),
            sa.Column("trigger_event_id", sa.Integer(), nullable=True),
            sa.Column("market_score_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("market_metric_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("market_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("decision_at", sa.DateTime(), nullable=False),
            sa.Column("available_at", sa.DateTime(), nullable=False),
            sa.Column("analysis_mode", sa.String(length=16), nullable=False),
            sa.Column("decision_type", sa.String(length=32), nullable=False),
            sa.Column("final_rating", sa.String(length=32), nullable=True),
            sa.Column("portfolio_action", sa.String(length=32), nullable=True),
            sa.Column("quality_status", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("market_context_json", sa.JSON(), nullable=True),
            sa.Column("portfolio_context_json", sa.JSON(), nullable=True),
            sa.Column("candidate_context_json", sa.JSON(), nullable=True),
            sa.Column("holding_decisions_json", sa.JSON(), nullable=True),
            sa.Column("candidate_decisions_json", sa.JSON(), nullable=True),
            sa.Column("no_action_context_json", sa.JSON(), nullable=True),
            sa.Column("decision_features_json", sa.JSON(), nullable=True),
            sa.Column("source_refs_json", sa.JSON(), nullable=True),
            sa.Column("calculation_version", sa.String(length=64), nullable=False, server_default="decision-memory-v1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["analysis_job_id"], ["analysis_jobs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_snapshot_id"], ["portfolio_snapshots.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["portfolio_risk_snapshot_id"], ["portfolio_risk_snapshots.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["candidate_run_id"], ["candidate_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["trigger_event_id"], ["trigger_events.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("analysis_run_id", name="uq_decision_memories_analysis_run_id"),
        )
        for name, columns in {
            "ix_decision_memories_user_id": ["user_id"],
            "ix_decision_memories_portfolio_id": ["portfolio_id"],
            "ix_decision_memories_decision_at": ["decision_at"],
            "ix_decision_memories_trade_date": ["trade_date"],
            "ix_decision_memories_decision_type": ["decision_type"],
            "ix_decision_memories_analysis_run_id": ["analysis_run_id"],
        }.items():
            op.create_index(name, "decision_memories", columns)

    if "decision_outcomes" not in tables:
        op.create_table(
            "decision_outcomes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("decision_memory_id", sa.Integer(), nullable=False),
            sa.Column("target_type", sa.String(length=32), nullable=False),
            sa.Column("target_key", sa.String(length=64), nullable=False),
            sa.Column("recommended_action", sa.String(length=32), nullable=False),
            sa.Column("horizon_trading_days", sa.Integer(), nullable=False),
            sa.Column("recommended_qty", sa.Float(), nullable=True),
            sa.Column("recommended_weight", sa.Float(), nullable=True),
            sa.Column("target_weight", sa.Float(), nullable=True),
            sa.Column("reference_trade_date", sa.Date(), nullable=True),
            sa.Column("reference_at", sa.DateTime(), nullable=True),
            sa.Column("reference_price", sa.Float(), nullable=True),
            sa.Column("reference_price_basis", sa.String(length=64), nullable=True),
            sa.Column("target_trade_date", sa.Date(), nullable=True),
            sa.Column("end_price", sa.Float(), nullable=True),
            sa.Column("raw_return", sa.Float(), nullable=True),
            sa.Column("benchmark_return", sa.Float(), nullable=True),
            sa.Column("excess_return", sa.Float(), nullable=True),
            sa.Column("mfe", sa.Float(), nullable=True),
            sa.Column("mae", sa.Float(), nullable=True),
            sa.Column("directional_mfe", sa.Float(), nullable=True),
            sa.Column("directional_mae", sa.Float(), nullable=True),
            sa.Column("directional_return", sa.Float(), nullable=True),
            sa.Column("directional_excess_return", sa.Float(), nullable=True),
            sa.Column("actual_execution_price", sa.Float(), nullable=True),
            sa.Column("actual_executed_qty", sa.Float(), nullable=True),
            sa.Column("actual_execution_return", sa.Float(), nullable=True),
            sa.Column("net_execution_return", sa.Float(), nullable=True),
            sa.Column("execution_fees", sa.Float(), nullable=True),
            sa.Column("execution_taxes", sa.Float(), nullable=True),
            sa.Column("execution_alignment", sa.String(length=24), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("source_refs_json", sa.JSON(), nullable=True),
            sa.Column("calculation_version", sa.String(length=64), nullable=False, server_default="decision-outcome-v1"),
            sa.Column("computed_at", sa.DateTime(), nullable=True),
            sa.Column("available_at", sa.DateTime(), nullable=True),
            sa.Column("recalculation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_source_change_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["decision_memory_id"], ["decision_memories.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "decision_memory_id",
                "target_type",
                "target_key",
                "horizon_trading_days",
                "calculation_version",
                name="uq_decision_outcomes_memory_target_horizon_version",
            ),
        )
        for name, columns in {
            "ix_decision_outcomes_decision_memory_id": ["decision_memory_id"],
            "ix_decision_outcomes_target_key": ["target_key"],
            "ix_decision_outcomes_horizon_trading_days": ["horizon_trading_days"],
            "ix_decision_outcomes_status": ["status"],
            "ix_decision_outcomes_available_at": ["available_at"],
        }.items():
            op.create_index(name, "decision_outcomes", columns)

    if "daily_review_runs" not in tables:
        op.create_table(
            "daily_review_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="RUNNING"),
            sa.Column("decision_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("no_action_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("action_decision_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("candidate_action_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actual_execution_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("execution_followed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("execution_partial_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("execution_ignored_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("execution_opposite_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("execution_unresolved_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("outcomes_matured_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("market_summary_json", sa.JSON(), nullable=True),
            sa.Column("decision_summary_json", sa.JSON(), nullable=True),
            sa.Column("execution_summary_json", sa.JSON(), nullable=True),
            sa.Column("outcome_summary_json", sa.JSON(), nullable=True),
            sa.Column("reason_codes_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="DEGRADED"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("review_version", sa.String(length=64), nullable=False, server_default="daily-review-v1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("portfolio_id", "trade_date", "review_version", name="uq_daily_review_portfolio_day_version"),
        )
        for name, columns in {
            "ix_daily_review_runs_user_id": ["user_id"],
            "ix_daily_review_runs_portfolio_id": ["portfolio_id"],
            "ix_daily_review_runs_trade_date": ["trade_date"],
        }.items():
            op.create_index(name, "daily_review_runs", columns)


def downgrade() -> None:
    for table in ("daily_review_runs", "decision_outcomes", "decision_memories"):
        inspector = sa.inspect(op.get_bind())
        if table in inspector.get_table_names():
            op.drop_table(table)
