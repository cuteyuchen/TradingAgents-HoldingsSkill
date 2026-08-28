"""Point-in-time cutoff canonicalisation for the historical foundation.

SQLAlchemy DateTime values in this application are UTC-naive.  An A-share
trading day ends in Asia/Shanghai, so a Shanghai day-end cutoff must be
converted to the equivalent UTC-naive instant before comparing persisted
``source_available_at`` values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def shanghai_end_of_day_to_utc_naive(day: date | datetime) -> datetime:
    if isinstance(day, datetime):
        day = day.date()
    return datetime.combine(day, time(23, 59, 59), tzinfo=CHINA_TZ).astimezone(
        UTC
    ).replace(tzinfo=None)


def visible(value: datetime | None, cutoff: datetime) -> bool:
    """A missing availability time is never visible at any historical cutoff."""

    parsed = to_utc_naive(value)
    return parsed is not None and parsed <= cutoff


def fundamental_visible_at(value: Any) -> datetime | None:
    """Return the point-in-time instant a fundamental version becomes visible.

    ``published_at`` is mandatory.  When ``source_available_at`` exists the
    version is only visible from the later of the two instants.  A restatement
    without a known source availability time is never visible: accepting it
    would let a future revision leak into an earlier as-of query.
    """

    published_at = to_utc_naive(getattr(value, "published_at", None))
    if published_at is None:
        return None
    source_available_at = to_utc_naive(getattr(value, "source_available_at", None))
    revision_number = int(getattr(value, "revision_number", 0) or 0)
    if revision_number > 0 and source_available_at is None:
        return None
    if source_available_at is None:
        return published_at
    return max(published_at, source_available_at)


__all__ = [
    "CHINA_TZ",
    "fundamental_visible_at",
    "shanghai_end_of_day_to_utc_naive",
    "to_utc_naive",
    "visible",
]
