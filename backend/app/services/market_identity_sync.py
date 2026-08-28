"""SecurityMaster and TradingCalendar initialization/synchronization lifecycle."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from ..database import SessionLocal
from ..market.providers.base import CalendarProvider, SecurityProvider
from ..market.providers.identity import (
    OfficialCNCalendarProvider,
    build_calendar_provider,
    build_security_provider,
)
from ..market_models import TradingCalendar
from .security_master import upsert_securities
from .trading_calendar import CHINA_TZ, TradingCalendarService, normalize_market, upsert_calendar


logger = logging.getLogger(__name__)

CALENDAR_READY = "ready"
CALENDAR_NOT_INITIALIZED = "calendar_not_initialized"
CALENDAR_OUT_OF_RANGE = "calendar_out_of_range"


def _local_date(value: date | datetime | None = None) -> date:
    if value is None:
        return datetime.now(CHINA_TZ).date()
    if isinstance(value, datetime):
        return value.astimezone(CHINA_TZ).date() if value.tzinfo else value.date()
    return value


def calendar_status(
    db: Session,
    *,
    market: str = "CN",
    as_of: date | datetime | None = None,
) -> dict[str, Any]:
    """Expose whether the persisted calendar can safely drive the scheduler."""

    normalized_market = normalize_market(market)
    day = _local_date(as_of)
    count, first_date, last_date = db.execute(
        select(
            func.count(TradingCalendar.id),
            func.min(TradingCalendar.trade_date),
            func.max(TradingCalendar.trade_date),
        ).where(TradingCalendar.market == normalized_market)
    ).one()
    count = int(count or 0)
    if count == 0:
        status = CALENDAR_NOT_INITIALIZED
        current_row = None
        next_open = None
    else:
        service = TradingCalendarService(db, market=normalized_market)
        current_row = service.row_for(day)
        next_open = db.execute(
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.market == normalized_market,
                TradingCalendar.trade_date > day,
                TradingCalendar.is_open.is_(True),
            )
            .order_by(TradingCalendar.trade_date.asc())
            .limit(1)
        ).scalar_one_or_none()
        status = CALENDAR_READY if current_row is not None and next_open is not None else CALENDAR_OUT_OF_RANGE
    return {
        "status": status,
        "market": normalized_market,
        "as_of": day,
        "row_count": count,
        "first_date": first_date,
        "last_date": last_date,
        "current_date_initialized": current_row is not None,
        "next_open_date": next_open,
    }


def sync_calendar_from_provider(
    db: Session,
    provider: CalendarProvider,
    *,
    start: date,
    end: date,
    market: str = "CN",
) -> list[TradingCalendar]:
    rows = provider.get_calendar(start, end, market=market)
    if not rows:
        raise RuntimeError(f"calendar_provider_returned_no_rows:{type(provider).__name__}")
    persisted = upsert_calendar(db, rows, market=market)
    db.commit()
    return persisted


def sync_security_master_from_provider(
    db: Session,
    provider: SecurityProvider,
    *,
    market: str = "CN",
):
    rows = provider.list_securities(market=market)
    if not rows:
        raise RuntimeError(f"security_provider_returned_no_rows:{type(provider).__name__}")
    persisted = upsert_securities(db, rows)
    db.commit()
    return persisted


def ensure_local_calendar(
    db: Session,
    *,
    as_of: date | datetime | None = None,
    provider: CalendarProvider | None = None,
    market: str = "CN",
) -> dict[str, Any]:
    """Fill an empty/out-of-range calendar from the bundled offline source."""

    before = calendar_status(db, market=market, as_of=as_of)
    if before["status"] == CALENDAR_READY:
        return before
    day = _local_date(as_of)
    selected = provider or OfficialCNCalendarProvider()
    try:
        sync_calendar_from_provider(
            db,
            selected,
            start=date(day.year, 1, 1),
            end=date(day.year, 12, 31),
            market=market,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "Local trading calendar bootstrap failed for %s; scheduler state remains %s",
            day.year,
            before["status"],
        )
        return before
    after = calendar_status(db, market=market, as_of=day)
    logger.info(
        "Trading calendar bootstrap completed: status=%s rows=%s range=%s..%s",
        after["status"],
        after["row_count"],
        after["first_date"],
        after["last_date"],
    )
    return after


def initialize_local_market_identity(
    *,
    session_factory: sessionmaker = SessionLocal,
    as_of: date | datetime | None = None,
) -> dict[str, Any]:
    """Run the fast offline bootstrap used before the scheduler starts."""

    with session_factory() as db:
        if settings.CALENDAR_BOOTSTRAP_ENABLED:
            return ensure_local_calendar(db, as_of=as_of)
        status = calendar_status(db, as_of=as_of)
        if status["status"] != CALENDAR_READY:
            logger.warning("Trading calendar state: %s", status["status"])
        return status


def run_remote_market_identity_sync(
    *,
    session_factory: sessionmaker = SessionLocal,
    as_of: date | datetime | None = None,
) -> None:
    """Refresh enabled sources; failures are logged and never escape startup."""

    day = _local_date(as_of)
    if settings.CALENDAR_SYNC_ENABLED:
        try:
            provider = build_calendar_provider(settings.CALENDAR_SYNC_PROVIDER)
            with session_factory() as db:
                sync_calendar_from_provider(
                    db,
                    provider,
                    start=day - timedelta(days=settings.CALENDAR_SYNC_LOOKBACK_DAYS),
                    end=day,
                )
        except Exception:
            logger.exception("Background TradingCalendar synchronization failed")
    if settings.SECURITY_MASTER_SYNC_ENABLED:
        try:
            provider = build_security_provider(
                settings.SECURITY_MASTER_SYNC_PROVIDER,
                min_interval_seconds=settings.EASTMONEY_MIN_INTERVAL_SECONDS,
            )
            with session_factory() as db:
                sync_security_master_from_provider(db, provider)
        except Exception:
            logger.exception("Background SecurityMaster synchronization failed")


def start_remote_market_identity_sync(
    *,
    session_factory: sessionmaker = SessionLocal,
    thread_factory: Callable[..., threading.Thread] = threading.Thread,
) -> threading.Thread | None:
    """Start enabled remote sync in a daemon thread without awaiting network."""

    global _REMOTE_SYNC_THREAD
    if not (settings.CALENDAR_SYNC_ENABLED or settings.SECURITY_MASTER_SYNC_ENABLED):
        return None
    thread = thread_factory(
        target=run_remote_market_identity_sync,
        kwargs={"session_factory": session_factory},
        name="market-identity-sync",
        daemon=True,
    )
    thread.start()
    _REMOTE_SYNC_THREAD = thread
    return thread


def stop_remote_market_identity_sync(timeout_seconds: float = 5.0) -> None:
    """Bounded graceful wait for the optional identity refresh thread."""

    global _REMOTE_SYNC_THREAD
    thread = _REMOTE_SYNC_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=max(0.0, timeout_seconds))
    _REMOTE_SYNC_THREAD = None


_REMOTE_SYNC_THREAD: threading.Thread | None = None


__all__ = [
    "CALENDAR_NOT_INITIALIZED",
    "CALENDAR_OUT_OF_RANGE",
    "CALENDAR_READY",
    "calendar_status",
    "ensure_local_calendar",
    "initialize_local_market_identity",
    "run_remote_market_identity_sync",
    "start_remote_market_identity_sync",
    "stop_remote_market_identity_sync",
    "sync_calendar_from_provider",
    "sync_security_master_from_provider",
]
