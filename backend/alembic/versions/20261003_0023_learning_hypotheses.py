"""Add versioned learning hypotheses, independent validation and context references.

Revision ID: 20261003_0023
Revises: 20260905_0022
"""
from alembic import op

revision = "20261003_0023"
down_revision = "20260905_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.memory.models import LearningContextReference, LearningHypothesis, LearningValidation
    for model in (LearningHypothesis, LearningValidation, LearningContextReference):
        model.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    from app.memory.models import LearningContextReference, LearningHypothesis, LearningValidation
    for model in (LearningContextReference, LearningValidation, LearningHypothesis):
        model.__table__.drop(bind=op.get_bind(), checkfirst=True)
