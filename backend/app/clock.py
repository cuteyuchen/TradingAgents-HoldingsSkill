"""Small clock seam used only by the isolated browser acceptance runtime."""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .config import settings

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    """Return the real clock unless explicit acceptance mode is enabled."""

    if settings.ACCEPTANCE_MODE:
        try:
            value = datetime.fromisoformat(settings.ACCEPTANCE_NOW_UTC.replace("Z", "+00:00"))
            return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def utc_now_naive() -> datetime:
    return utc_now().replace(tzinfo=None)


def china_now() -> datetime:
    return utc_now().astimezone(CHINA_TZ)


def acceptance_trade_date(default: date | None = None) -> date:
    fallback = default or china_now().date()
    try:
        return date.fromisoformat(settings.ACCEPTANCE_TRADE_DATE)
    except ValueError:
        return fallback


__all__ = ["CHINA_TZ", "acceptance_trade_date", "china_now", "utc_now", "utc_now_naive"]
