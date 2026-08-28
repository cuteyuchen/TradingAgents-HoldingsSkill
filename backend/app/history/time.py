"""Point-in-time cutoff canonicalisation for the historical foundation.

SQLAlchemy DateTime values in this application are UTC-naive.  An A-share
trading day ends in Asia/Shanghai, so a Shanghai day-end cutoff must be
converted to the equivalent UTC-naive instant before comparing persisted
``source_available_at`` values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
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


__all__ = [
    "CHINA_TZ",
    "shanghai_end_of_day_to_utc_naive",
    "to_utc_naive",
    "visible",
]
