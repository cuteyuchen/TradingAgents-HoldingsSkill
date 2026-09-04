"""Shared admission checks for one full analysis per portfolio."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..v2_models import AnalysisJob

ACTIVE_ANALYSIS_STATUSES = frozenset({"queued", "running", "retrying"})


@dataclass(frozen=True, slots=True)
class AnalysisJobAdmission:
    """An enqueued job plus whether this caller owns its execution start."""

    job: AnalysisJob
    should_start: bool
    source: str


def active_portfolio_analysis(
    db: Session,
    *,
    user_id: int,
    portfolio_id: int,
) -> AnalysisJob | None:
    """Return the single active full analysis for a user portfolio, if any."""

    return (
        db.query(AnalysisJob)
        .filter(
            AnalysisJob.user_id == user_id,
            AnalysisJob.portfolio_id == portfolio_id,
            AnalysisJob.status.in_(ACTIVE_ANALYSIS_STATUSES),
        )
        .order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc())
        .first()
    )


__all__ = ["ACTIVE_ANALYSIS_STATUSES", "AnalysisJobAdmission", "active_portfolio_analysis"]
