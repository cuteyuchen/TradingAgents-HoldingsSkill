"""Add research-owned backtest and calibration evidence tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260827_0017"
down_revision = "20260827_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "backtest_runs" not in tables:
        op.create_table(
            "backtest_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_id", sa.Integer(), nullable=True),
            sa.Column("scope", sa.String(length=32), nullable=False),
            sa.Column("replay_mode", sa.String(length=32), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="QUEUED"),
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("current_stage", sa.String(length=32), nullable=False, server_default="DATA_AUDIT"),
            sa.Column("config_version", sa.String(length=128), nullable=False, server_default="candidate-engine-v1"),
            sa.Column("engine_version", sa.String(length=64), nullable=False, server_default="historical-replay-v1"),
            sa.Column("baseline_config_json", sa.JSON(), nullable=True),
            sa.Column("experiment_config_json", sa.JSON(), nullable=True),
            sa.Column("data_manifest_json", sa.JSON(), nullable=True),
            sa.Column("data_hash", sa.String(length=64), nullable=False),
            sa.Column("calculation_key", sa.String(length=255), nullable=False),
            sa.Column("random_seed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("unique_trade_dates", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("quality_status", sa.String(length=32), nullable=False, server_default="MISSING"),
            sa.Column("leakage_status", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
            sa.Column("result_summary_json", sa.JSON(), nullable=True),
            sa.Column("failure_counts_json", sa.JSON(), nullable=True),
            sa.Column("horizons_json", sa.JSON(), nullable=True),
            sa.Column("known_limitations_json", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("calculation_key", name="uq_backtest_runs_calculation_key"),
        )
        for name, columns in {
            "ix_backtest_runs_user_id": ["user_id"],
            "ix_backtest_runs_portfolio_id": ["portfolio_id"],
            "ix_backtest_runs_scope": ["scope"],
            "ix_backtest_runs_replay_mode": ["replay_mode"],
            "ix_backtest_runs_start_date": ["start_date"],
            "ix_backtest_runs_end_date": ["end_date"],
            "ix_backtest_runs_status": ["status"],
            "ix_backtest_runs_current_stage": ["current_stage"],
            "ix_backtest_runs_data_hash": ["data_hash"],
            "ix_backtest_runs_calculation_key": ["calculation_key"],
            "ix_backtest_runs_quality_status": ["quality_status"],
            "ix_backtest_runs_leakage_status": ["leakage_status"],
            "ix_backtest_runs_created_at": ["created_at"],
            "ix_backtest_runs_lease_expires_at": ["lease_expires_at"],
            "ix_backtest_runs_last_heartbeat_at": ["last_heartbeat_at"],
            "ix_backtest_runs_cancel_requested": ["cancel_requested"],
            "ix_backtest_runs_owner_status": ["user_id", "portfolio_id", "status"],
        }.items():
            op.create_index(name, "backtest_runs", columns)

    if "backtest_metric_slices" not in tables:
        op.create_table(
            "backtest_metric_slices",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("slice_key", sa.String(length=255), nullable=False),
            sa.Column("metric_family", sa.String(length=64), nullable=False),
            sa.Column("security_type", sa.String(length=24), nullable=True),
            sa.Column("market_regime", sa.String(length=32), nullable=True),
            sa.Column("stage", sa.String(length=24), nullable=True),
            sa.Column("score_bucket", sa.String(length=32), nullable=True),
            sa.Column("horizon", sa.Integer(), nullable=True),
            sa.Column("parameter_variant", sa.String(length=128), nullable=True),
            sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("trade_date_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("coverage", sa.Float(), nullable=True),
            sa.Column("metrics_json", sa.JSON(), nullable=True),
            sa.Column("confidence_interval_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=32), nullable=False, server_default="INSUFFICIENT"),
            sa.Column("limitations_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["backtest_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "slice_key", name="uq_backtest_metric_slices_run_key"),
        )
        for name, columns in {
            "ix_backtest_metric_slices_run_id": ["run_id"],
            "ix_backtest_metric_slices_slice_key": ["slice_key"],
            "ix_backtest_metric_slices_metric_family": ["metric_family"],
            "ix_backtest_metric_slices_security_type": ["security_type"],
            "ix_backtest_metric_slices_market_regime": ["market_regime"],
            "ix_backtest_metric_slices_stage": ["stage"],
            "ix_backtest_metric_slices_score_bucket": ["score_bucket"],
            "ix_backtest_metric_slices_horizon": ["horizon"],
            "ix_backtest_metric_slices_parameter_variant": ["parameter_variant"],
            "ix_backtest_metric_slices_quality_status": ["quality_status"],
            "ix_backtest_metric_slices_lookup": ["run_id", "metric_family", "horizon"],
        }.items():
            op.create_index(name, "backtest_metric_slices", columns)

    if "calibration_reports" not in tables:
        op.create_table(
            "calibration_reports",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("backtest_run_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="COMPLETED"),
            sa.Column("target_parameter", sa.String(length=128), nullable=False),
            sa.Column("current_value_json", sa.JSON(), nullable=True),
            sa.Column("challenger_value_json", sa.JSON(), nullable=True),
            sa.Column("recommendation", sa.String(length=32), nullable=False),
            sa.Column("train_metrics_json", sa.JSON(), nullable=True),
            sa.Column("validation_metrics_json", sa.JSON(), nullable=True),
            sa.Column("test_metrics_json", sa.JSON(), nullable=True),
            sa.Column("robustness_json", sa.JSON(), nullable=True),
            sa.Column("sample_counts_json", sa.JSON(), nullable=True),
            sa.Column("risk_notes_json", sa.JSON(), nullable=True),
            sa.Column("proposal_json", sa.JSON(), nullable=True),
            sa.Column("report_json", sa.JSON(), nullable=True),
            sa.Column("calibration_version", sa.String(length=64), nullable=False, server_default="calibration-v1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["backtest_run_id"], ["backtest_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("backtest_run_id", "target_parameter", name="uq_calibration_reports_run_parameter"),
        )
        for name, columns in {
            "ix_calibration_reports_backtest_run_id": ["backtest_run_id"],
            "ix_calibration_reports_user_id": ["user_id"],
            "ix_calibration_reports_portfolio_id": ["portfolio_id"],
            "ix_calibration_reports_status": ["status"],
            "ix_calibration_reports_target_parameter": ["target_parameter"],
            "ix_calibration_reports_recommendation": ["recommendation"],
            "ix_calibration_reports_created_at": ["created_at"],
            "ix_calibration_reports_owner_status": ["user_id", "status"],
        }.items():
            op.create_index(name, "calibration_reports", columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("calibration_reports", "backtest_metric_slices", "backtest_runs"):
        if table in inspector.get_table_names():
            op.drop_table(table)
