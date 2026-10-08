"""Add Phase N live decision validation and paper-only shadow facts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0020"
down_revision = "20260828_0019"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index(name: str, table: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    names = {item["name"] for item in inspector.get_indexes(table)}
    if name not in names:
        op.create_index(name, table, columns)


def upgrade() -> None:
    tables = _tables()

    if "live_decision_observations" not in tables:
        op.create_table(
            "live_decision_observations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("decision_kind", sa.String(length=16), nullable=False, server_default="CHECKPOINT"),
            sa.Column("decision_checkpoint", sa.String(length=16), nullable=True),
            sa.Column("trigger_type", sa.String(length=32), nullable=True),
            sa.Column("trigger_event_id", sa.Integer(), nullable=True),
            sa.Column("trigger_priority", sa.String(length=4), nullable=True),
            sa.Column("trigger_reason", sa.Text(), nullable=True),
            sa.Column("source_analysis_job_id", sa.Integer(), nullable=True),
            sa.Column("source_analysis_run_id", sa.Integer(), nullable=True),
            sa.Column("decision_memory_id", sa.Integer(), nullable=True),
            sa.Column("candidate_run_id", sa.Integer(), nullable=True),
            sa.Column("portfolio_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("market_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("market_score_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("market_metric_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("parameter_set_version_id", sa.Integer(), nullable=True),
            sa.Column("parameter_set_version", sa.String(length=64), nullable=True),
            sa.Column("parameter_set_hash", sa.String(length=64), nullable=True),
            sa.Column("runtime_contract_version", sa.String(length=32), nullable=False, server_default="2.4.0"),
            sa.Column("decision_contract_version", sa.String(length=32), nullable=False, server_default="2.4.0"),
            sa.Column("runtime_prompt_version", sa.String(length=64), nullable=True),
            sa.Column("runtime_prompt_sha256", sa.String(length=64), nullable=True),
            sa.Column("skill_version", sa.String(length=64), nullable=True),
            sa.Column("skill_sha256", sa.String(length=64), nullable=True),
            sa.Column("market_engine_version", sa.String(length=64), nullable=True),
            sa.Column("candidate_engine_version", sa.String(length=64), nullable=True),
            sa.Column("model_provider", sa.String(length=64), nullable=True),
            sa.Column("model_name", sa.String(length=128), nullable=True),
            sa.Column("final_action", sa.String(length=24), nullable=False),
            sa.Column("raw_final_action", sa.String(length=32), nullable=True),
            sa.Column("final_reason_codes_json", sa.JSON(), nullable=True),
            sa.Column("selected_actions_json", sa.JSON(), nullable=True),
            sa.Column("selected_candidate_ids_json", sa.JSON(), nullable=True),
            sa.Column("market_regime", sa.String(length=32), nullable=True),
            sa.Column("market_score", sa.Float(), nullable=True),
            sa.Column("market_quality", sa.String(length=24), nullable=True),
            sa.Column("portfolio_quality", sa.String(length=24), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("data_coverage", sa.Float(), nullable=True),
            sa.Column("decision_started_at", sa.DateTime(), nullable=True),
            sa.Column("decision_finalized_at", sa.DateTime(), nullable=False),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("deterministic_core_hash", sa.String(length=64), nullable=True),
            sa.Column("observation_hash", sa.String(length=64), nullable=False),
            sa.Column("calculation_key", sa.String(length=255), nullable=False),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("live_evidence_eligibility", sa.String(length=32), nullable=False, server_default="DIAGNOSTIC_ONLY"),
            sa.Column("calculation_version", sa.String(length=64), nullable=False, server_default="live-decision-observation-v1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["trigger_event_id"], ["trigger_events.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["source_analysis_job_id"], ["analysis_jobs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["source_analysis_run_id"], ["analysis_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["decision_memory_id"], ["decision_memories.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["candidate_run_id"], ["candidate_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["portfolio_snapshot_id"], ["portfolio_snapshots.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("calculation_key", name="uq_live_decision_observation_calculation_key"),
            sa.CheckConstraint(
                "decision_kind IN ('CHECKPOINT', 'TRIGGER', 'MANUAL')",
                name="ck_live_decision_observation_kind",
            ),
        )
        for name, columns in {
            "ix_live_decision_observation_owner_date": ["user_id", "portfolio_id", "trade_date"],
            "ix_live_decision_observation_finalized": ["decision_finalized_at"],
            "ix_live_decision_observation_final_action": ["final_action"],
            "ix_live_decision_observation_calculation_key": ["calculation_key"],
        }.items():
            _index(name, "live_decision_observations", columns)

    tables = _tables()
    if "live_quote_observations" not in tables:
        op.create_table(
            "live_quote_observations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("quote_key", sa.String(length=255), nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("exchange", sa.String(length=8), nullable=True),
            sa.Column("code", sa.String(length=16), nullable=False),
            sa.Column("security_type", sa.String(length=24), nullable=True),
            sa.Column("trade_date", sa.Date(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("captured_at_precision", sa.String(length=16), nullable=False, server_default="EXACT"),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("prev_close", sa.Float(), nullable=True),
            sa.Column("bid", sa.Float(), nullable=True),
            sa.Column("ask", sa.Float(), nullable=True),
            sa.Column("volume", sa.Float(), nullable=True),
            sa.Column("amount", sa.Float(), nullable=True),
            sa.Column("limit_up", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("limit_down", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("suspended", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("instrument_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("provider", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("source_ref", sa.String(length=255), nullable=True),
            sa.Column("price_basis", sa.String(length=32), nullable=False, server_default="RAW_QUOTE"),
            sa.Column("source_snapshot_id", sa.String(length=64), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("quote_key", name="uq_live_quote_observation_quote_key"),
        )
        for name, columns in {
            "ix_live_quote_observation_code_time": ["code", "captured_at"],
            "ix_live_quote_observation_trade_date": ["trade_date"],
            "ix_live_quote_observation_quality": ["quality_status"],
        }.items():
            _index(name, "live_quote_observations", columns)

    tables = _tables()
    if "shadow_accounts" not in tables:
        op.create_table(
            "shadow_accounts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("source_portfolio_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
            sa.Column("mode", sa.String(length=32), nullable=False, server_default="FOLLOW_FINAL_ACTIONS"),
            sa.Column("base_currency", sa.String(length=8), nullable=False, server_default="CNY"),
            sa.Column("paper_only", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("initialized_from_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("initialized_at", sa.DateTime(), nullable=False),
            sa.Column("starting_cash", sa.Float(), nullable=False, server_default="0"),
            sa.Column("current_cash", sa.Float(), nullable=False, server_default="0"),
            sa.Column("reserved_cash", sa.Float(), nullable=False, server_default="0"),
            sa.Column("shadow_generation", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("execution_contract_version", sa.String(length=64), nullable=False, server_default="shadow-execution-v1"),
            sa.Column("expires_policy", sa.String(length=64), nullable=False, server_default="NEXT_TRADING_DAY_CLOSE"),
            sa.Column("config_json", sa.JSON(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("paused_at", sa.DateTime(), nullable=True),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["initialized_from_snapshot_id"], ["portfolio_snapshots.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        _index("ix_shadow_accounts_owner_status", "shadow_accounts", ["user_id", "source_portfolio_id", "status"])
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_accounts_active_owner "
            "ON shadow_accounts (user_id, source_portfolio_id) WHERE status = 'ACTIVE'"
        )

    tables = _tables()
    if "shadow_positions" not in tables:
        op.create_table(
            "shadow_positions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("shadow_account_id", sa.Integer(), nullable=False),
            sa.Column("shadow_generation", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=16), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("security_type", sa.String(length=24), nullable=True),
            sa.Column("etf_category", sa.String(length=64), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=False, server_default="0"),
            sa.Column("sellable_quantity", sa.Float(), nullable=False, server_default="0"),
            sa.Column("average_cost", sa.Float(), nullable=False, server_default="0"),
            sa.Column("current_mark", sa.Float(), nullable=True),
            sa.Column("market_value", sa.Float(), nullable=True),
            sa.Column("unrealized_pnl", sa.Float(), nullable=True),
            sa.Column("last_mark_at", sa.DateTime(), nullable=True),
            sa.Column("acquired_decision_ids_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["shadow_account_id"], ["shadow_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("shadow_account_id", "shadow_generation", "code", name="uq_shadow_position_generation_code"),
        )
        _index("ix_shadow_positions_account_generation", "shadow_positions", ["shadow_account_id", "shadow_generation"])
        _index("ix_shadow_positions_code", "shadow_positions", ["code"])

    tables = _tables()
    if "shadow_order_intents" not in tables:
        op.create_table(
            "shadow_order_intents",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("shadow_account_id", sa.Integer(), nullable=False),
            sa.Column("shadow_generation", sa.Integer(), nullable=False),
            sa.Column("decision_observation_id", sa.Integer(), nullable=False),
            sa.Column("action_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("code", sa.String(length=16), nullable=False),
            sa.Column("security_type", sa.String(length=24), nullable=True),
            sa.Column("side", sa.String(length=8), nullable=False),
            sa.Column("target_qty", sa.Float(), nullable=True),
            sa.Column("target_notional", sa.Float(), nullable=True),
            sa.Column("target_weight", sa.Float(), nullable=True),
            sa.Column("decision_reference_price", sa.Float(), nullable=True),
            sa.Column("decision_reference_basis", sa.String(length=32), nullable=True),
            sa.Column("decision_finalized_at", sa.DateTime(), nullable=False),
            sa.Column("earliest_executable_at", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
            sa.Column("reason_codes_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.ForeignKeyConstraint(["shadow_account_id"], ["shadow_accounts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["decision_observation_id"], ["live_decision_observations.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("idempotency_key", name="uq_shadow_order_intent_idempotency_key"),
            sa.CheckConstraint(
                "status IN ('PENDING', 'FILLED', 'PARTIAL', 'BLOCKED', 'EXPIRED', 'CANCELLED', 'SUPERSEDED')",
                name="ck_shadow_order_intent_status",
            ),
        )
        _index("ix_shadow_order_intent_pending_code", "shadow_order_intents", ["status", "code"])
        _index("ix_shadow_order_intent_account_generation", "shadow_order_intents", ["shadow_account_id", "shadow_generation"])

    tables = _tables()
    if "shadow_fills" not in tables:
        op.create_table(
            "shadow_fills",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("order_intent_id", sa.Integer(), nullable=False),
            sa.Column("shadow_account_id", sa.Integer(), nullable=False),
            sa.Column("shadow_generation", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=16), nullable=False),
            sa.Column("side", sa.String(length=8), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("price", sa.Float(), nullable=False),
            sa.Column("gross_amount", sa.Float(), nullable=False),
            sa.Column("commission", sa.Float(), nullable=False, server_default="0"),
            sa.Column("tax", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total_cost", sa.Float(), nullable=False),
            sa.Column("price_basis", sa.String(length=32), nullable=False, server_default="RAW_QUOTE"),
            sa.Column("quote_observation_id", sa.Integer(), nullable=False),
            sa.Column("quote_source_ref", sa.String(length=255), nullable=True),
            sa.Column("quote_captured_at", sa.DateTime(), nullable=False),
            sa.Column("fill_at", sa.DateTime(), nullable=False),
            sa.Column("fill_quality", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("execution_key", sa.String(length=255), nullable=False),
            sa.Column("slippage_not_modeled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("execution_delay_seconds", sa.Float(), nullable=True),
            sa.Column("execution_delay_price_drift", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["order_intent_id"], ["shadow_order_intents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["shadow_account_id"], ["shadow_accounts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["quote_observation_id"], ["live_quote_observations.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("execution_key", name="uq_shadow_fill_execution_key"),
        )
        _index("ix_shadow_fill_account_generation", "shadow_fills", ["shadow_account_id", "shadow_generation"])
        _index("ix_shadow_fill_at", "shadow_fills", ["fill_at"])

    tables = _tables()
    if "shadow_ledger_entries" not in tables:
        op.create_table(
            "shadow_ledger_entries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("entry_key", sa.String(length=255), nullable=False),
            sa.Column("shadow_account_id", sa.Integer(), nullable=False),
            sa.Column("shadow_generation", sa.Integer(), nullable=False),
            sa.Column("entry_type", sa.String(length=32), nullable=False),
            sa.Column("code", sa.String(length=16), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("gross_amount", sa.Float(), nullable=True),
            sa.Column("commission", sa.Float(), nullable=True),
            sa.Column("tax", sa.Float(), nullable=True),
            sa.Column("cash_delta", sa.Float(), nullable=True),
            sa.Column("sellable_at", sa.DateTime(), nullable=True),
            sa.Column("decision_observation_id", sa.Integer(), nullable=True),
            sa.Column("order_intent_id", sa.Integer(), nullable=True),
            sa.Column("fill_id", sa.Integer(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["shadow_account_id"], ["shadow_accounts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["decision_observation_id"], ["live_decision_observations.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["order_intent_id"], ["shadow_order_intents.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["fill_id"], ["shadow_fills.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("entry_key", name="uq_shadow_ledger_entry_key"),
        )
        _index("ix_shadow_ledger_account_generation", "shadow_ledger_entries", ["shadow_account_id", "shadow_generation"])
        _index("ix_shadow_ledger_occurred_at", "shadow_ledger_entries", ["occurred_at"])

    tables = _tables()
    if "shadow_daily_snapshots" not in tables:
        op.create_table(
            "shadow_daily_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("shadow_account_id", sa.Integer(), nullable=False),
            sa.Column("shadow_generation", sa.Integer(), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("cash", sa.Float(), nullable=False, server_default="0"),
            sa.Column("market_value", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total_equity", sa.Float(), nullable=False, server_default="0"),
            sa.Column("daily_return", sa.Float(), nullable=True),
            sa.Column("cumulative_return", sa.Float(), nullable=True),
            sa.Column("drawdown", sa.Float(), nullable=True),
            sa.Column("turnover", sa.Float(), nullable=False, server_default="0"),
            sa.Column("position_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("action_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("no_action_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("benchmark_return", sa.Float(), nullable=True),
            sa.Column("excess_return", sa.Float(), nullable=True),
            sa.Column("market_regime", sa.String(length=32), nullable=True),
            sa.Column("price_basis", sa.String(length=32), nullable=True),
            sa.Column("price_basis_compatible", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("source_refs_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["shadow_account_id"], ["shadow_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("shadow_account_id", "shadow_generation", "trade_date", name="uq_shadow_daily_snapshot_day"),
        )
        _index("ix_shadow_daily_snapshot_trade_date", "shadow_daily_snapshots", ["trade_date"])

    tables = _tables()
    if "live_decision_outcomes" not in tables:
        op.create_table(
            "live_decision_outcomes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("decision_observation_id", sa.Integer(), nullable=False),
            sa.Column("shadow_account_id", sa.Integer(), nullable=True),
            sa.Column("shadow_generation", sa.Integer(), nullable=True),
            sa.Column("target_type", sa.String(length=32), nullable=False),
            sa.Column("target_key", sa.String(length=64), nullable=False),
            sa.Column("recommended_action", sa.String(length=24), nullable=False),
            sa.Column("horizon_trading_days", sa.Integer(), nullable=False),
            sa.Column("reference_trade_date", sa.Date(), nullable=True),
            sa.Column("reference_at", sa.DateTime(), nullable=True),
            sa.Column("reference_price", sa.Float(), nullable=True),
            sa.Column("reference_price_basis", sa.String(length=32), nullable=True),
            sa.Column("target_trade_date", sa.Date(), nullable=True),
            sa.Column("target_price", sa.Float(), nullable=True),
            sa.Column("forward_return", sa.Float(), nullable=True),
            sa.Column("benchmark_return", sa.Float(), nullable=True),
            sa.Column("excess_return", sa.Float(), nullable=True),
            sa.Column("mfe", sa.Float(), nullable=True),
            sa.Column("mae", sa.Float(), nullable=True),
            sa.Column("drawdown", sa.Float(), nullable=True),
            sa.Column("direction", sa.String(length=16), nullable=True),
            sa.Column("execution_eligible", sa.Boolean(), nullable=True),
            sa.Column("shadow_filled", sa.Boolean(), nullable=True),
            sa.Column("fill_delay_seconds", sa.Float(), nullable=True),
            sa.Column("fill_drift", sa.Float(), nullable=True),
            sa.Column("realized_pnl", sa.Float(), nullable=True),
            sa.Column("unrealized_pnl", sa.Float(), nullable=True),
            sa.Column("candidate_opportunity_cost", sa.Float(), nullable=True),
            sa.Column("drawdown_avoided", sa.Float(), nullable=True),
            sa.Column("risk_off_correct", sa.Boolean(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("live_evidence_eligibility", sa.String(length=32), nullable=False, server_default="INSUFFICIENT_SAMPLE"),
            sa.Column("next_due_date", sa.Date(), nullable=True),
            sa.Column("source_refs_json", sa.JSON(), nullable=True),
            sa.Column("calculation_version", sa.String(length=64), nullable=False, server_default="live-shadow-outcome-v1"),
            sa.Column("computed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["decision_observation_id"], ["live_decision_observations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["shadow_account_id"], ["shadow_accounts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "decision_observation_id", "target_type", "target_key", "horizon_trading_days", "calculation_version",
                name="uq_live_decision_outcome_target_horizon",
            ),
        )
        _index("ix_live_decision_outcome_due", "live_decision_outcomes", ["next_due_date", "status"])

    if "decision_actual_alignments" not in _tables():
        op.create_table(
            "decision_actual_alignments",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("decision_observation_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=16), nullable=False),
            sa.Column("side", sa.String(length=8), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("actual_trade_ledger_id", sa.Integer(), nullable=True),
            sa.Column("window_start", sa.DateTime(), nullable=False),
            sa.Column("window_end", sa.DateTime(), nullable=False),
            sa.Column("matched_at", sa.DateTime(), nullable=True),
            sa.Column("time_delta_seconds", sa.Float(), nullable=True),
            sa.Column("quantity_ratio", sa.Float(), nullable=True),
            sa.Column("source_refs_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["decision_observation_id"], ["live_decision_observations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actual_trade_ledger_id"], ["trade_ledger_entries.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("decision_observation_id", "code", "side", name="uq_decision_actual_alignment_target"),
        )
        _index("ix_decision_actual_alignment_status", "decision_actual_alignments", ["status"])


def downgrade() -> None:
    for table in (
        "decision_actual_alignments",
        "live_decision_outcomes",
        "shadow_daily_snapshots",
        "shadow_ledger_entries",
        "shadow_fills",
        "shadow_order_intents",
        "shadow_positions",
        "shadow_accounts",
        "live_quote_observations",
        "live_decision_observations",
    ):
        if table in _tables():
            op.drop_table(table)
