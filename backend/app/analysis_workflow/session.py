"""Independent SQLAlchemy session for workflow audit writes."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..database import SessionLocal


def open_audit_session() -> Session:
    """Open a session that must not be shared with business transactions."""

    return SessionLocal()
