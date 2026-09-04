"""Database-backed A-share trading calendar and session helpers."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import china_now
from ..market_models import TradingCalendar

CN_MARKET = "CN"
CHINA_TZ = ZoneInfo("Asia/Shanghai")

PRE_MARKET = "PRE_MARKET"
AUCTION = "AUCTION"
MORNING = "MORNING"
LUNCH = "LUNCH"
AFTERNOON = "AFTERNOON"
CLOSED = "CLOSED"


def normalize_trade_date(value: date | datetime | str) -> date:
    """Normalize date-like values in the A-share business timezone."""

    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(CHINA_TZ).date()
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"invalid trade date: {value!r}") from exc


def normalize_market(value: str | None) -> str:
    return str(value or CN_MARKET).strip().upper()


def _coerce_bool(value: bool | int | str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "open", "on", "是"}


def upsert_calendar_day(
    db: Session,
    trade_date: date | datetime | str,
    is_open: bool | int | str,
    *,
    market: str = CN_MARKET,
    previous_trade_date: date | datetime | str | None = None,
    next_trade_date: date | datetime | str | None = None,
    source: str | None = None,
) -> TradingCalendar:
    """Insert/update one calendar row; caller controls commit."""

    normalized_date = normalize_trade_date(trade_date)
    normalized_market = normalize_market(market)
    row = db.execute(
        select(TradingCalendar).where(
            TradingCalendar.market == normalized_market,
            TradingCalendar.trade_date == normalized_date,
        )
    ).scalar_one_or_none()
    values = {
        "trade_date": normalized_date,
        "market": normalized_market,
        "is_open": _coerce_bool(is_open),
        "previous_trade_date": normalize_trade_date(previous_trade_date) if previous_trade_date is not None else None,
        "next_trade_date": normalize_trade_date(next_trade_date) if next_trade_date is not None else None,
        "source": source,
    }
    if row is None:
        row = TradingCalendar(**values)
        db.add(row)
    else:
        for key, value in values.items():
            if value is not None or key in {"is_open", "trade_date", "market"}:
                setattr(row, key, value)
    db.flush()
    return row


def upsert_calendar(
    db: Session,
    rows: Iterable[TradingCalendar | Mapping[str, object]],
    *,
    market: str = CN_MARKET,
    source: str | None = None,
) -> list[TradingCalendar]:
    """Upsert a batch of calendar records."""

    result: list[TradingCalendar] = []
    for item in rows:
        if isinstance(item, TradingCalendar):
            result.append(upsert_calendar_day(
                db,
                item.trade_date,
                item.is_open,
                market=item.market,
                previous_trade_date=item.previous_trade_date,
                next_trade_date=item.next_trade_date,
                source=item.source or source,
            ))
            continue
        payload = dict(item)
        result.append(upsert_calendar_day(
            db,
            payload.get("trade_date") or payload.get("date"),
            payload.get("is_open", payload.get("open", False)),
            market=str(payload.get("market") or market),
            previous_trade_date=payload.get("previous_trade_date"),
            next_trade_date=payload.get("next_trade_date"),
            source=str(payload.get("source") or source) if payload.get("source") or source else None,
        ))
    return result


class TradingCalendarService:
    """Read-only calendar queries plus deterministic session classification."""

    def __init__(self, db: Session, *, market: str = CN_MARKET):
        self.db = db
        self.market = normalize_market(market)

    def row_for(self, value: date | datetime | str) -> TradingCalendar | None:
        day = normalize_trade_date(value)
        return self.db.execute(
            select(TradingCalendar).where(
                TradingCalendar.market == self.market,
                TradingCalendar.trade_date == day,
            )
        ).scalar_one_or_none()

    def is_trading_day(self, value: date | datetime | str) -> bool:
        """Return the persisted fact; unknown dates are not treated as open."""

        row = self.row_for(value)
        return bool(row and row.is_open)

    def previous_trading_day(self, value: date | datetime | str) -> date | None:
        day = normalize_trade_date(value)
        row = self.row_for(day)
        if row and row.previous_trade_date:
            return row.previous_trade_date
        return self.db.execute(
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.market == self.market,
                TradingCalendar.trade_date < day,
                TradingCalendar.is_open.is_(True),
            )
            .order_by(TradingCalendar.trade_date.desc())
            .limit(1)
        ).scalar_one_or_none()

    def next_trading_day(self, value: date | datetime | str) -> date | None:
        day = normalize_trade_date(value)
        row = self.row_for(day)
        if row and row.next_trade_date:
            return row.next_trade_date
        return self.db.execute(
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.market == self.market,
                TradingCalendar.trade_date > day,
                TradingCalendar.is_open.is_(True),
            )
            .order_by(TradingCalendar.trade_date.asc())
            .limit(1)
        ).scalar_one_or_none()

    def latest_trading_day(self) -> date | None:
        return self.db.execute(
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.market == self.market,
                TradingCalendar.is_open.is_(True),
            )
            .order_by(TradingCalendar.trade_date.desc())
            .limit(1)
        ).scalar_one_or_none()

    def current_session(self, value: datetime | None = None) -> str:
        moment = value or china_now()
        if moment.tzinfo is not None:
            moment = moment.astimezone(CHINA_TZ)
        if not self.is_trading_day(moment.date()):
            return CLOSED
        current = moment.time()
        if current < time(9, 15):
            return PRE_MARKET
        if current < time(9, 30):
            return AUCTION
        if current < time(11, 30):
            return MORNING
        if current < time(13, 0):
            return LUNCH
        if current < time(15, 0):
            return AFTERNOON
        return CLOSED

    def is_market_session(self, value: datetime | None = None) -> bool:
        return self.current_session(value) in {AUCTION, MORNING, AFTERNOON}


def is_trading_day(db: Session, value: date | datetime | str, *, market: str = CN_MARKET) -> bool:
    return TradingCalendarService(db, market=market).is_trading_day(value)


def previous_trading_day(db: Session, value: date | datetime | str, *, market: str = CN_MARKET) -> date | None:
    return TradingCalendarService(db, market=market).previous_trading_day(value)


def next_trading_day(db: Session, value: date | datetime | str, *, market: str = CN_MARKET) -> date | None:
    return TradingCalendarService(db, market=market).next_trading_day(value)


def latest_trading_day(db: Session, *, market: str = CN_MARKET) -> date | None:
    return TradingCalendarService(db, market=market).latest_trading_day()


def is_market_session(db: Session, value: datetime | None = None, *, market: str = CN_MARKET) -> bool:
    return TradingCalendarService(db, market=market).is_market_session(value)


def current_session(db: Session, value: datetime | None = None, *, market: str = CN_MARKET) -> str:
    return TradingCalendarService(db, market=market).current_session(value)


def next_open_date(
    db: Session,
    value: date | datetime | str,
    *,
    market: str = CN_MARKET,
    max_days: int = 370,
) -> date | None:
    """Find the first persisted open day on or after ``value``.

    The bounded search is deliberately fail-closed: a missing calendar range
    never turns a weekday heuristic into a schedulable trading day.
    """

    day = normalize_trade_date(value)
    return db.execute(
        select(TradingCalendar.trade_date)
        .where(
            TradingCalendar.market == normalize_market(market),
            TradingCalendar.trade_date >= day,
            TradingCalendar.trade_date <= day + timedelta(days=max_days),
            TradingCalendar.is_open.is_(True),
        )
        .order_by(TradingCalendar.trade_date.asc())
        .limit(1)
    ).scalar_one_or_none()
