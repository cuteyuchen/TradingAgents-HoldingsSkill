"""Add Phase J forward observation campaigns, coverage, and evidence seals."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260827_0017"
down_revision = "20260827_0016"
branch_labels = None
depends_on = None


def _indexes(table: str, names: dict[str, list[str]]) -> None:
    for name, columns in names.items():
        op.create_index(name, table, columns)


def upgrade() -> None:
    op.create_table(
        "observation_campaigns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("decision_contract_version", sa.String(length=24), nullable=False, server_default="2.4.0"),
        sa.Column("evaluation_schema_version", sa.String(length=24), nullable=False, server_default="1.0.0"),
        sa.Column("code_commit", sa.String(length=64), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PLANNED"),
        sa.Column("expected_trading_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_trading_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision_capture_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missed_capture_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_outcome_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_outcome_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_quality_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", name="uq_observation_campaigns_campaign_id"),
    )
    _indexes("observation_campaigns", {
        "ix_observation_campaigns_campaign_id": ["campaign_id"],
        "ix_observation_campaigns_user_id": ["user_id"],
        "ix_observation_campaigns_portfolio_id": ["portfolio_id"],
        "ix_observation_campaigns_start_date": ["start_date"],
        "ix_observation_campaigns_end_date": ["end_date"],
        "ix_observation_campaigns_started_at": ["started_at"],
        "ix_observation_campaigns_ended_at": ["ended_at"],
        "ix_observation_campaigns_status": ["status"],
        "ix_observation_campaigns_created_at": ["created_at"],
    })

    op.create_table(
        "daily_observation_coverages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("market_coverage", sa.JSON(), nullable=True),
        sa.Column("candidate_coverage", sa.JSON(), nullable=True),
        sa.Column("trigger_coverage", sa.JSON(), nullable=True),
        sa.Column("analysis_coverage", sa.JSON(), nullable=True),
        sa.Column("decision_coverage", sa.JSON(), nullable=True),
        sa.Column("episode_coverage", sa.JSON(), nullable=True),
        sa.Column("snapshot_integrity", sa.JSON(), nullable=True),
        sa.Column("data_quality", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PARTIAL"),
        sa.Column("missing_reasons_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["observation_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "trading_date", name="uq_daily_observation_coverage_campaign_day"),
    )
    _indexes("daily_observation_coverages", {
        "ix_daily_observation_coverages_campaign_id": ["campaign_id"],
        "ix_daily_observation_coverages_user_id": ["user_id"],
        "ix_daily_observation_coverages_portfolio_id": ["portfolio_id"],
        "ix_daily_observation_coverages_trading_date": ["trading_date"],
        "ix_daily_observation_coverages_status": ["status"],
        "ix_daily_observation_coverages_created_at": ["created_at"],
    })

    op.create_table(
        "daily_evidence_seals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("seal_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("episode_ids_json", sa.JSON(), nullable=True),
        sa.Column("manifest_hashes_json", sa.JSON(), nullable=True),
        sa.Column("episode_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_hash", sa.String(length=64), nullable=False),
        sa.Column("code_commit", sa.String(length=64), nullable=True),
        sa.Column("decision_contract_version", sa.String(length=24), nullable=False, server_default="2.4.0"),
        sa.Column("evaluation_schema_version", sa.String(length=24), nullable=False, server_default="1.0.0"),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="SEALED"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["observation_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", "trading_date", name="uq_daily_evidence_seal_portfolio_day"),
        sa.UniqueConstraint("seal_id", name="uq_daily_evidence_seal_seal_id"),
    )
    _indexes("daily_evidence_seals", {
        "ix_daily_evidence_seals_seal_id": ["seal_id"],
        "ix_daily_evidence_seals_campaign_id": ["campaign_id"],
        "ix_daily_evidence_seals_user_id": ["user_id"],
        "ix_daily_evidence_seals_portfolio_id": ["portfolio_id"],
        "ix_daily_evidence_seals_trading_date": ["trading_date"],
        "ix_daily_evidence_seals_evidence_hash": ["evidence_hash"],
        "ix_daily_evidence_seals_status": ["status"],
        "ix_daily_evidence_seals_created_at": ["created_at"],
    })


def downgrade() -> None:
    op.drop_table("daily_evidence_seals")
    op.drop_table("daily_observation_coverages")
    op.drop_table("observation_campaigns")
