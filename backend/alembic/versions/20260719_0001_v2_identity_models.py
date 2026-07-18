"""Add V2 users, refresh tokens, and model settings.

Revision ID: 20260719_0001
Revises:
Create Date: 2026-07-19
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260719_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=True),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)
        op.create_index("ix_users_username", "users", ["username"], unique=True)
        op.create_index("ix_users_status", "users", ["status"], unique=False)

    tables = _tables()
    if "refresh_tokens" not in tables:
        op.create_table(
            "refresh_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("device_info", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
        op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)
        op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"], unique=False)

    tables = _tables()
    if "model_providers" not in tables:
        op.create_table(
            "model_providers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("display_name", sa.String(length=64), nullable=False),
            sa.Column("base_url", sa.String(length=512), nullable=True),
            sa.Column("encrypted_api_key", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "display_name", name="uq_model_provider_user_name"),
        )
        op.create_index("ix_model_providers_user_id", "model_providers", ["user_id"], unique=False)
        op.create_index("ix_model_providers_provider", "model_providers", ["provider"], unique=False)

    tables = _tables()
    if "model_profiles" not in tables:
        op.create_table(
            "model_profiles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("provider_id", sa.Integer(), nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False),
            sa.Column("model_name", sa.String(length=128), nullable=False),
            sa.Column("parameters_json", sa.JSON(), nullable=True),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_health_status", sa.String(length=32), nullable=True),
            sa.Column("last_health_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "user_id",
                "purpose",
                "model_name",
                name="uq_model_profile_user_purpose_model",
            ),
        )
        op.create_index("ix_model_profiles_user_id", "model_profiles", ["user_id"], unique=False)
        op.create_index("ix_model_profiles_provider_id", "model_profiles", ["provider_id"], unique=False)
        op.create_index("ix_model_profiles_purpose", "model_profiles", ["purpose"], unique=False)
        op.create_index("ix_model_profiles_is_default", "model_profiles", ["is_default"], unique=False)


def downgrade() -> None:
    tables = _tables()
    for table_name in ("model_profiles", "model_providers", "refresh_tokens", "users"):
        if table_name in tables:
            op.drop_table(table_name)
            tables.remove(table_name)
