"""Allow confirmed holdings without a security code.

Revision ID: 20260719_0003
Revises: 20260719_0002
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260719_0003"
down_revision: str | None = "20260719_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("holding_items") as batch_op:
        batch_op.alter_column("code", existing_type=sa.String(length=16), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE holding_items SET code = '' WHERE code IS NULL")
    with op.batch_alter_table("holding_items") as batch_op:
        batch_op.alter_column("code", existing_type=sa.String(length=16), nullable=False)
