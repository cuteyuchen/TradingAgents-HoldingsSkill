"""Add point-in-time historical data foundation tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260828_0019"
down_revision = "20260828_0018"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade() -> None:
    tables = _tables()

    if "security_lifecycle_events" not in tables:
        op.create_table(
            "security_lifecycle_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("exchange", sa.String(length=8), nullable=True),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("security_type", sa.String(length=24), nullable=True),
            sa.Column("security_name", sa.String(length=128), nullable=True),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("effective_at", sa.DateTime(), nullable=True),
            sa.Column("source_available_at", sa.DateTime(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_ref", sa.String(length=160), nullable=True),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "market", "code", "effective_date", "event_type", "source", "source_ref",
                name="uq_security_lifecycle_source_ref",
            ),
        )
        for name, columns in {
            "ix_security_lifecycle_code_date": ["code", "effective_date"],
            "ix_security_lifecycle_event": ["event_type"],
            "ix_security_lifecycle_available": ["source_available_at"],
        }.items():
            op.create_index(name, "security_lifecycle_events", columns)

    if "security_trading_status_daily" not in tables:
        op.create_table(
            "security_trading_status_daily",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("exchange", sa.String(length=8), nullable=True),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("effective_at", sa.DateTime(), nullable=True),
            sa.Column("source_available_at", sa.DateTime(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_ref", sa.String(length=160), nullable=True),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "market", "code", "trade_date", "source", "source_ref",
                name="uq_security_trading_status_source_ref",
            ),
        )
        for name, columns in {
            "ix_security_trading_status_code_date": ["code", "trade_date"],
            "ix_security_trading_status_status": ["status"],
            "ix_security_trading_status_available": ["source_available_at"],
        }.items():
            op.create_index(name, "security_trading_status_daily", columns)

    if "security_classification_daily" not in tables:
        op.create_table(
            "security_classification_daily",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("exchange", sa.String(length=8), nullable=True),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("classification", sa.String(length=24), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("is_name_derived", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source_available_at", sa.DateTime(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_ref", sa.String(length=160), nullable=True),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "market", "code", "trade_date", "source", "source_ref",
                name="uq_security_classification_source_ref",
            ),
        )
        for name, columns in {
            "ix_security_classification_code_date": ["code", "trade_date"],
            "ix_security_classification_class": ["classification"],
            "ix_security_classification_available": ["source_available_at"],
        }.items():
            op.create_index(name, "security_classification_daily", columns)

    if "security_valuation_daily" not in tables:
        op.create_table(
            "security_valuation_daily",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("exchange", sa.String(length=8), nullable=True),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("pe_ttm", sa.Float(), nullable=True),
            sa.Column("pb", sa.Float(), nullable=True),
            sa.Column("ps_ttm", sa.Float(), nullable=True),
            sa.Column("dividend_yield", sa.Float(), nullable=True),
            sa.Column("market_cap", sa.Float(), nullable=True),
            sa.Column("float_market_cap", sa.Float(), nullable=True),
            sa.Column("valuation_effective_at", sa.DateTime(), nullable=True),
            sa.Column("source_available_at", sa.DateTime(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_ref", sa.String(length=160), nullable=True),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "market", "code", "trade_date", "source", "source_ref",
                name="uq_security_valuation_source_ref",
            ),
        )
        for name, columns in {
            "ix_security_valuation_code_date": ["code", "trade_date"],
            "ix_security_valuation_available": ["source_available_at"],
        }.items():
            op.create_index(name, "security_valuation_daily", columns)

    if "fundamental_reports" not in tables:
        op.create_table(
            "fundamental_reports",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("exchange", sa.String(length=8), nullable=True),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("report_period", sa.Date(), nullable=False),
            sa.Column("report_type", sa.String(length=24), nullable=False, server_default="ANNUAL"),
            sa.Column("announced_at", sa.DateTime(), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("revision_number", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_restatement", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("roe", sa.Float(), nullable=True),
            sa.Column("revenue", sa.Float(), nullable=True),
            sa.Column("revenue_yoy", sa.Float(), nullable=True),
            sa.Column("net_profit", sa.Float(), nullable=True),
            sa.Column("net_profit_yoy", sa.Float(), nullable=True),
            sa.Column("gross_margin", sa.Float(), nullable=True),
            sa.Column("debt_ratio", sa.Float(), nullable=True),
            sa.Column("operating_cash_flow", sa.Float(), nullable=True),
            sa.Column("eps", sa.Float(), nullable=True),
            sa.Column("source_available_at", sa.DateTime(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_ref", sa.String(length=160), nullable=True),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "market", "code", "report_period", "report_type", "source", "source_ref",
                name="uq_fundamental_reports_source_ref",
            ),
        )
        for name, columns in {
            "ix_fundamental_reports_code_period": ["code", "report_period"],
            "ix_fundamental_reports_published": ["published_at"],
            "ix_fundamental_reports_available": ["source_available_at"],
        }.items():
            op.create_index(name, "fundamental_reports", columns)

    if "etf_metadata_history" not in tables:
        op.create_table(
            "etf_metadata_history",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("exchange", sa.String(length=8), nullable=True),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=True),
            sa.Column("index_code", sa.String(length=32), nullable=True),
            sa.Column("benchmark_code", sa.String(length=32), nullable=True),
            sa.Column("fund_type", sa.String(length=32), nullable=True),
            sa.Column("sector_theme_json", sa.JSON(), nullable=True),
            sa.Column("inception_date", sa.Date(), nullable=True),
            sa.Column("source_available_at", sa.DateTime(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_ref", sa.String(length=160), nullable=True),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "market", "code", "effective_date", "source", "source_ref",
                name="uq_etf_metadata_history_source_ref",
            ),
        )
        for name, columns in {
            "ix_etf_metadata_history_code_date": ["code", "effective_date"],
            "ix_etf_metadata_history_available": ["source_available_at"],
        }.items():
            op.create_index(name, "etf_metadata_history", columns)

    if "price_basis_metadata" not in tables:
        op.create_table(
            "price_basis_metadata",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("exchange", sa.String(length=8), nullable=True),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("basis", sa.String(length=16), nullable=False, server_default="QFQ"),
            sa.Column("adjustment_factor", sa.Float(), nullable=True),
            sa.Column("source_available_at", sa.DateTime(), nullable=True),
            sa.Column("captured_at", sa.DateTime(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_ref", sa.String(length=160), nullable=True),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="VALID"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "market", "code", "trade_date", "source", "source_ref",
                name="uq_price_basis_metadata_source_ref",
            ),
        )
        for name, columns in {
            "ix_price_basis_metadata_code_date": ["code", "trade_date"],
            "ix_price_basis_metadata_basis": ["basis"],
            "ix_price_basis_metadata_available": ["source_available_at"],
        }.items():
            op.create_index(name, "price_basis_metadata", columns)

    if "historical_data_sync_runs" not in tables:
        op.create_table(
            "historical_data_sync_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("data_type", sa.String(length=32), nullable=False),
            sa.Column("market", sa.String(length=16), nullable=False, server_default="CN"),
            sa.Column("start_date", sa.Date(), nullable=True),
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="QUEUED"),
            sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("inserted_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("provider", sa.String(length=64), nullable=True),
            sa.Column("source", sa.String(length=64), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("error_summary", sa.Text(), nullable=True),
            sa.Column("source_lineage_json", sa.JSON(), nullable=True),
            sa.Column("coverage_summary_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, columns in {
            "ix_historical_data_sync_runs_type": ["data_type"],
            "ix_historical_data_sync_runs_status": ["status"],
            "ix_historical_data_sync_runs_created": ["created_at"],
            "ix_historical_data_sync_runs_lease": ["lease_expires_at"],
        }.items():
            op.create_index(name, "historical_data_sync_runs", columns)


def downgrade() -> None:
    tables = _tables()
    for table_name in (
        "historical_data_sync_runs",
        "price_basis_metadata",
        "etf_metadata_history",
        "fundamental_reports",
        "security_valuation_daily",
        "security_classification_daily",
        "security_trading_status_daily",
        "security_lifecycle_events",
    ):
        if table_name in tables:
            op.drop_table(table_name)
