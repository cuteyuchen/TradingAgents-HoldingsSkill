"""Session-safe SQLite table probes that never open a second connection."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def table_exists(db: Session, name: str) -> bool:
    try:
        row = db.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = :name"
            ),
            {"name": name},
        ).scalar_one_or_none()
        return row is not None
    except Exception:  # noqa: BLE001
        return False
