"""Add Phase I decision evaluation and paper observation evidence tables."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260827_0015"
down_revision = "20260826_0014"
branch_labels = None
depends_on = None


def _create_indexes(table: str, mapping: dict[str, list[str]]) -> None:
    for name, columns in mapping.items():
        op.create_index(name, table, columns)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "evaluation_runs" not in tables:
        op.create_table(
            "evaluation_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("run_type", sa.String(length=40), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_id", sa.Integer(), nullable=True),
            sa.Column("code_version", sa.String(length=128), nullable=True),
            sa.Column("git_commit", sa.String(length=64), nullable=True),
            sa.Column("decision_contract_version", sa.String(length=24), nullable=False, server_default="2.4.0"),
            sa.Column("evaluation_schema_version", sa.String(length=24), nullable=False, server_default="1.0.0"),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="RUNNING"),
            sa.Column("data_cutoff", sa.DateTime(), nullable=True),
            sa.Column("input_hash", sa.String(length=64), nullable=True),
            sa.Column("result_hash", sa.String(length=64), nullable=True),
            sa.Column("error_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", name="uq_evaluation_runs_run_id"),
        )
        _create_indexes("evaluation_runs", {
            "ix_evaluation_runs_run_id": ["run_id"], "ix_evaluation_runs_run_type": ["run_type"],
            "ix_evaluation_runs_user_id": ["user_id"], "ix_evaluation_runs_portfolio_id": ["portfolio_id"],
            "ix_evaluation_runs_started_at": ["started_at"], "ix_evaluation_runs_status": ["status"],
            "ix_evaluation_runs_data_cutoff": ["data_cutoff"], "ix_evaluation_runs_created_at": ["created_at"],
        })

    if "decision_evaluation_episodes" not in tables:
        op.create_table(
            "decision_evaluation_episodes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("episode_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("symbol", sa.String(length=32), nullable=False, server_default="__PORTFOLIO__"),
            sa.Column("decision_time", sa.DateTime(), nullable=False),
            sa.Column("trading_date", sa.Date(), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Shanghai"),
            sa.Column("decision_run_id", sa.Integer(), nullable=True),
            sa.Column("decision_memory_id", sa.Integer(), nullable=True),
            sa.Column("market_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("portfolio_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("candidate_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("trigger_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("analysis_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("decision_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("candidate_stage", sa.String(length=16), nullable=True),
            sa.Column("decision_type", sa.String(length=32), nullable=False),
            sa.Column("portfolio_gate_result", sa.String(length=32), nullable=True),
            sa.Column("no_action_reason", sa.Text(), nullable=True),
            sa.Column("source_data_cutoff", sa.DateTime(), nullable=False),
            sa.Column("source_mode", sa.String(length=40), nullable=False, server_default="FACT_REPLAY"),
            sa.Column("evidence_status", sa.String(length=40), nullable=False, server_default="READY"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="FROZEN"),
            sa.Column("manifest_hash", sa.String(length=64), nullable=True),
            sa.Column("decision_contract_version", sa.String(length=24), nullable=False, server_default="2.4.0"),
            sa.Column("evaluation_schema_version", sa.String(length=24), nullable=False, server_default="1.0.0"),
            sa.Column("code_version", sa.String(length=128), nullable=True),
            sa.Column("frozen_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["decision_run_id"], ["analysis_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["decision_memory_id"], ["decision_memories.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["portfolio_snapshot_id"], ["portfolio_snapshots.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["candidate_snapshot_id"], ["candidate_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["trigger_snapshot_id"], ["trigger_events.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["analysis_snapshot_id"], ["analysis_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["decision_snapshot_id"], ["decision_memories.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("episode_id", name="uq_decision_evaluation_episode_id"),
            sa.UniqueConstraint("portfolio_id", "decision_run_id", "symbol", name="uq_decision_episode_run_symbol"),
        )
        _create_indexes("decision_evaluation_episodes", {
            "ix_decision_evaluation_episodes_episode_id": ["episode_id"], "ix_decision_evaluation_episodes_user_id": ["user_id"],
            "ix_decision_evaluation_episodes_portfolio_id": ["portfolio_id"], "ix_decision_evaluation_episodes_symbol": ["symbol"],
            "ix_decision_evaluation_episodes_decision_time": ["decision_time"], "ix_decision_evaluation_episodes_trading_date": ["trading_date"],
            "ix_decision_evaluation_episodes_decision_run_id": ["decision_run_id"], "ix_decision_evaluation_episodes_decision_memory_id": ["decision_memory_id"],
            "ix_decision_evaluation_episodes_market_snapshot_id": ["market_snapshot_id"], "ix_decision_evaluation_episodes_portfolio_snapshot_id": ["portfolio_snapshot_id"],
            "ix_decision_evaluation_episodes_candidate_snapshot_id": ["candidate_snapshot_id"], "ix_decision_evaluation_episodes_trigger_snapshot_id": ["trigger_snapshot_id"],
            "ix_decision_evaluation_episodes_analysis_snapshot_id": ["analysis_snapshot_id"], "ix_decision_evaluation_episodes_decision_snapshot_id": ["decision_snapshot_id"],
            "ix_decision_evaluation_episodes_candidate_stage": ["candidate_stage"], "ix_decision_evaluation_episodes_decision_type": ["decision_type"],
            "ix_decision_evaluation_episodes_portfolio_gate_result": ["portfolio_gate_result"], "ix_decision_evaluation_episodes_source_data_cutoff": ["source_data_cutoff"],
            "ix_decision_evaluation_episodes_source_mode": ["source_mode"], "ix_decision_evaluation_episodes_evidence_status": ["evidence_status"],
            "ix_decision_evaluation_episodes_status": ["status"], "ix_decision_evaluation_episodes_manifest_hash": ["manifest_hash"],
            "ix_decision_evaluation_episodes_frozen_at": ["frozen_at"], "ix_decision_evaluation_episodes_created_at": ["created_at"],
        })

    if "decision_evaluation_snapshots" not in tables:
        op.create_table(
            "decision_evaluation_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=False),
            sa.Column("input_type", sa.String(length=32), nullable=False),
            sa.Column("source_id", sa.String(length=128), nullable=True),
            sa.Column("snapshot_id", sa.String(length=128), nullable=True),
            sa.Column("version", sa.String(length=128), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=True),
            sa.Column("available_at", sa.DateTime(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["episode_id"], ["decision_evaluation_episodes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("episode_id", "input_type", "source_id", "snapshot_id", "version", name="uq_decision_evaluation_snapshot_input"),
        )
        _create_indexes("decision_evaluation_snapshots", {
            "ix_decision_evaluation_snapshots_episode_id": ["episode_id"], "ix_decision_evaluation_snapshots_input_type": ["input_type"],
            "ix_decision_evaluation_snapshots_source_id": ["source_id"], "ix_decision_evaluation_snapshots_snapshot_id": ["snapshot_id"],
            "ix_decision_evaluation_snapshots_timestamp": ["timestamp"], "ix_decision_evaluation_snapshots_available_at": ["available_at"],
            "ix_decision_evaluation_snapshots_content_hash": ["content_hash"], "ix_decision_evaluation_snapshots_created_at": ["created_at"],
        })

    if "decision_evaluation_outcomes" not in tables:
        op.create_table(
            "decision_evaluation_outcomes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=False),
            sa.Column("target_type", sa.String(length=32), nullable=False, server_default="SYMBOL"),
            sa.Column("target_key", sa.String(length=32), nullable=False),
            sa.Column("horizon_trading_days", sa.Integer(), nullable=False),
            sa.Column("reference_trade_date", sa.Date(), nullable=True),
            sa.Column("target_trade_date", sa.Date(), nullable=True),
            sa.Column("start_price", sa.Float(), nullable=True),
            sa.Column("end_price", sa.Float(), nullable=True),
            sa.Column("high", sa.Float(), nullable=True),
            sa.Column("low", sa.Float(), nullable=True),
            sa.Column("raw_return", sa.Float(), nullable=True),
            sa.Column("directional_return", sa.Float(), nullable=True),
            sa.Column("benchmark_return", sa.Float(), nullable=True),
            sa.Column("sector_return", sa.Float(), nullable=True),
            sa.Column("mfe", sa.Float(), nullable=True),
            sa.Column("mae", sa.Float(), nullable=True),
            sa.Column("max_drawdown", sa.Float(), nullable=True),
            sa.Column("price_adjustment_method", sa.String(length=32), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("quality_status", sa.String(length=40), nullable=False, server_default="PENDING"),
            sa.Column("observation_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("available_at", sa.DateTime(), nullable=True),
            sa.Column("source_refs_json", sa.JSON(), nullable=True),
            sa.Column("calculation_version", sa.String(length=64), nullable=False, server_default="evaluation-outcome-v1"),
            sa.Column("recalculation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_source_change_at", sa.DateTime(), nullable=True),
            sa.Column("computed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["episode_id"], ["decision_evaluation_episodes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("episode_id", "target_key", "horizon_trading_days", "calculation_version", name="uq_decision_evaluation_outcome_horizon"),
        )
        _create_indexes("decision_evaluation_outcomes", {
            "ix_decision_evaluation_outcomes_episode_id": ["episode_id"], "ix_decision_evaluation_outcomes_target_type": ["target_type"],
            "ix_decision_evaluation_outcomes_target_key": ["target_key"], "ix_decision_evaluation_outcomes_horizon_trading_days": ["horizon_trading_days"],
            "ix_decision_evaluation_outcomes_reference_trade_date": ["reference_trade_date"], "ix_decision_evaluation_outcomes_target_trade_date": ["target_trade_date"],
            "ix_decision_evaluation_outcomes_status": ["status"], "ix_decision_evaluation_outcomes_quality_status": ["quality_status"],
            "ix_decision_evaluation_outcomes_observation_complete": ["observation_complete"], "ix_decision_evaluation_outcomes_available_at": ["available_at"],
            "ix_decision_evaluation_outcomes_calculation_version": ["calculation_version"], "ix_decision_evaluation_outcomes_created_at": ["created_at"],
        })

    if "candidate_evaluations" not in tables:
        op.create_table(
            "candidate_evaluations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("candidate_run_id", sa.Integer(), nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=True),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("stage", sa.String(length=16), nullable=False),
            sa.Column("stage_entered_at", sa.DateTime(), nullable=True),
            sa.Column("stage_exited_at", sa.DateTime(), nullable=True),
            sa.Column("duration_trading_days", sa.Integer(), nullable=True),
            sa.Column("observed_at", sa.DateTime(), nullable=False),
            sa.Column("source_data_cutoff", sa.DateTime(), nullable=False),
            sa.Column("outcome_summary_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=40), nullable=False, server_default="PENDING"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["candidate_run_id"], ["candidate_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["episode_id"], ["decision_evaluation_episodes.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("candidate_run_id", "code", name="uq_candidate_evaluation_run_code"),
        )
        _create_indexes("candidate_evaluations", {
            "ix_candidate_evaluations_user_id": ["user_id"], "ix_candidate_evaluations_portfolio_id": ["portfolio_id"],
            "ix_candidate_evaluations_candidate_run_id": ["candidate_run_id"], "ix_candidate_evaluations_episode_id": ["episode_id"],
            "ix_candidate_evaluations_code": ["code"], "ix_candidate_evaluations_stage": ["stage"],
            "ix_candidate_evaluations_stage_entered_at": ["stage_entered_at"], "ix_candidate_evaluations_observed_at": ["observed_at"],
            "ix_candidate_evaluations_source_data_cutoff": ["source_data_cutoff"], "ix_candidate_evaluations_quality_status": ["quality_status"],
            "ix_candidate_evaluations_created_at": ["created_at"],
        })

    if "trigger_evaluations" not in tables:
        op.create_table(
            "trigger_evaluations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("trigger_event_id", sa.Integer(), nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=True),
            sa.Column("trigger_type", sa.String(length=32), nullable=False),
            sa.Column("priority", sa.String(length=4), nullable=True),
            sa.Column("trigger_status", sa.String(length=24), nullable=False),
            sa.Column("analysis_refreshed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("decision_changed", sa.Boolean(), nullable=True),
            sa.Column("resulting_decision_type", sa.String(length=32), nullable=True),
            sa.Column("movement_return", sa.Float(), nullable=True),
            sa.Column("quality_status", sa.String(length=40), nullable=False, server_default="PENDING"),
            sa.Column("observed_at", sa.DateTime(), nullable=False),
            sa.Column("source_data_cutoff", sa.DateTime(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["trigger_event_id"], ["trigger_events.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["episode_id"], ["decision_evaluation_episodes.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("trigger_event_id", name="uq_trigger_evaluation_event"),
        )
        _create_indexes("trigger_evaluations", {
            "ix_trigger_evaluations_user_id": ["user_id"], "ix_trigger_evaluations_portfolio_id": ["portfolio_id"],
            "ix_trigger_evaluations_trigger_event_id": ["trigger_event_id"], "ix_trigger_evaluations_episode_id": ["episode_id"],
            "ix_trigger_evaluations_trigger_type": ["trigger_type"], "ix_trigger_evaluations_priority": ["priority"],
            "ix_trigger_evaluations_trigger_status": ["trigger_status"], "ix_trigger_evaluations_resulting_decision_type": ["resulting_decision_type"],
            "ix_trigger_evaluations_observed_at": ["observed_at"], "ix_trigger_evaluations_source_data_cutoff": ["source_data_cutoff"],
            "ix_trigger_evaluations_quality_status": ["quality_status"], "ix_trigger_evaluations_created_at": ["created_at"],
        })

    if "paper_observation_runs" not in tables:
        op.create_table(
            "paper_observation_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("observation_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="RUNNING"),
            sa.Column("source_data_cutoff", sa.DateTime(), nullable=True),
            sa.Column("code_version", sa.String(length=128), nullable=True),
            sa.Column("decision_contract_version", sa.String(length=24), nullable=False, server_default="2.4.0"),
            sa.Column("evaluation_schema_version", sa.String(length=24), nullable=False, server_default="1.0.0"),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("missing_reason", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", name="uq_paper_observation_runs_run_id"),
        )
        _create_indexes("paper_observation_runs", {
            "ix_paper_observation_runs_run_id": ["run_id"], "ix_paper_observation_runs_user_id": ["user_id"],
            "ix_paper_observation_runs_portfolio_id": ["portfolio_id"], "ix_paper_observation_runs_observation_date": ["observation_date"],
            "ix_paper_observation_runs_status": ["status"], "ix_paper_observation_runs_source_data_cutoff": ["source_data_cutoff"],
            "ix_paper_observation_runs_created_at": ["created_at"],
        })

    if "paper_observations" not in tables:
        op.create_table(
            "paper_observations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("observation_id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("source_data_cutoff", sa.DateTime(), nullable=True),
            sa.Column("freeze_hash", sa.String(length=64), nullable=True),
            sa.Column("missing_reason", sa.String(length=128), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["paper_observation_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["episode_id"], ["decision_evaluation_episodes.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("observation_id", name="uq_paper_observations_observation_id"),
        )
        _create_indexes("paper_observations", {
            "ix_paper_observations_observation_id": ["observation_id"], "ix_paper_observations_run_id": ["run_id"],
            "ix_paper_observations_episode_id": ["episode_id"], "ix_paper_observations_status": ["status"],
            "ix_paper_observations_captured_at": ["captured_at"], "ix_paper_observations_source_data_cutoff": ["source_data_cutoff"],
            "ix_paper_observations_freeze_hash": ["freeze_hash"], "ix_paper_observations_created_at": ["created_at"],
        })


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table in ("paper_observations", "paper_observation_runs", "trigger_evaluations", "candidate_evaluations", "decision_evaluation_outcomes", "decision_evaluation_snapshots", "decision_evaluation_episodes", "evaluation_runs"):
        if table in tables:
            op.drop_table(table)
