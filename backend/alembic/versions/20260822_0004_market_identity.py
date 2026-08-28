"""Add SecurityMaster and TradingCalendar foundation tables.

Revision ID: 20260822_0004
Revises: 20260719_0003
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0004"
down_revision: str | None = "20260719_0003"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "security_master" not in tables:
        op.create_table(
            "security_master",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("market", sa.String(length=16), server_default="CN", nullable=False),
            sa.Column("exchange", sa.String(length=8), nullable=False),
            sa.Column("code", sa.String(length=6), nullable=False),
            sa.Column("symbol", sa.String(length=24), nullable=True),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("security_type", sa.String(length=24), server_default="STOCK", nullable=False),
            sa.Column("etf_category", sa.String(length=32), nullable=True),
            sa.Column("listing_date", sa.Date(), nullable=True),
            sa.Column("delisting_date", sa.Date(), nullable=True),
            sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
            sa.Column("is_st", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("is_suspended", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("board", sa.String(length=16), nullable=True),
            sa.Column("lot_size", sa.Integer(), server_default="100", nullable=True),
            sa.Column("currency", sa.String(length=8), server_default="CNY", nullable=False),
            sa.Column("source", sa.String(length=64), nullable=True),
            sa.Column("source_updated_at", sa.DateTime(), nullable=True),
            sa.Column("raw_metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "market",
                "exchange",
                "code",
                name="uq_security_master_market_exchange_code",
            ),
        )
        for column in (
            "market",
            "exchange",
            "code",
            "symbol",
            "name",
            "security_type",
            "etf_category",
            "status",
            "is_st",
            "is_suspended",
            "board",
        ):
            op.create_index(f"ix_security_master_{column}", "security_master", [column])

    if "trading_calendar" not in tables:
        op.create_table(
            "trading_calendar",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("trade_date", sa.Date(), nullable=False),
            sa.Column("market", sa.String(length=16), server_default="CN", nullable=False),
            sa.Column("is_open", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("previous_trade_date", sa.Date(), nullable=True),
            sa.Column("next_trade_date", sa.Date(), nullable=True),
            sa.Column("source", sa.String(length=64), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("market", "trade_date", name="uq_trading_calendar_market_date"),
        )
        for column in ("trade_date", "market", "is_open"):
            op.create_index(f"ix_trading_calendar_{column}", "trading_calendar", [column])


def downgrade() -> None:
    tables = _tables()
    if "trading_calendar" in tables:
        op.drop_table("trading_calendar")
    if "security_master" in tables:
        op.drop_table("security_master")
