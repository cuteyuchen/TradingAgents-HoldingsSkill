"""Add encrypted instance-wide market provider settings."""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0024"
down_revision = "20261003_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Local init_db may have created this table before Alembic is invoked.
    if sa.inspect(op.get_bind()).has_table("market_provider_settings"):
        return
    op.create_table(
        "market_provider_settings",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("market_provider_settings")
