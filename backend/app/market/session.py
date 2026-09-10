"""Canonical A-share market-session resolution.

The persisted :class:`TradingCalendar` remains the authority for whether a
calendar date is an open session.  This module only adds the intraday phase
and data-basis vocabulary that every market consumer can share.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from ..clock import china_now
from ..services.trading_calendar import CHINA_TZ, TradingCalendarService, normalize_trade_date


class MarketSession(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    OPEN_AUCTION = "OPEN_AUCTION"
    MORNING = "MORNING"
    LUNCH_BREAK = "LUNCH_BREAK"
    AFTERNOON = "AFTERNOON"
    CLOSE_AUCTION = "CLOSE_AUCTION"
    CLOSED = "CLOSED"
    NON_TRADING_DAY = "NON_TRADING_DAY"
    DATA_ABNORMAL = "DATA_ABNORMAL"


class MarketDataBasis(str, Enum):
    LIVE = "live"
    SESSION_CLOSE = "session_close"
    PREVIOUS_SESSION_CLOSE = "previous_session_close"


_LIVE_SESSIONS = {
    MarketSession.OPEN_AUCTION,
    MarketSession.MORNING,
    MarketSession.LUNCH_BREAK,
    MarketSession.AFTERNOON,
    MarketSession.CLOSE_AUCTION,
}
_OPEN_SESSIONS = {
    MarketSession.OPEN_AUCTION,
    MarketSession.MORNING,
    MarketSession.AFTERNOON,
    MarketSession.CLOSE_AUCTION,
}


@dataclass(frozen=True, slots=True)
class MarketSessionResolution:
    market: str
    timezone: str
    session: str
    scheduled_session: str | None
    is_trading_day: bool | None
    is_market_open: bool
    trading_date: date
    latest_valid_trading_date: date | None
    previous_trading_date: date | None
    next_trading_date: date | None
    resolved_at: datetime
    data_status: str
    data_basis: str
    display_label: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in (
            "trading_date",
            "latest_valid_trading_date",
            "previous_trading_date",
            "next_trading_date",
        ):
            value = result.get(key)
            result[key] = value.isoformat() if value else None
        result["resolved_at"] = self.resolved_at.isoformat()
        return result


class MarketSessionService:
    """Resolve one unambiguous China-market session from persisted calendar facts."""

    def __init__(self, db: Session, *, market: str = "CN") -> None:
        self.db = db
        self.market = str(market or "CN").upper()
        self.calendar = TradingCalendarService(db, market=self.market)

    @staticmethod
    def _local_now(value: datetime | None) -> datetime:
        moment = value or china_now()
        if moment.tzinfo is None:
            return moment.replace(tzinfo=CHINA_TZ)
        return moment.astimezone(CHINA_TZ)

    def resolve_previous_trading_date(self, value: date | datetime | str) -> date | None:
        return self.calendar.previous_trading_day(normalize_trade_date(value))

    def resolve_next_trading_date(self, value: date | datetime | str) -> date | None:
        return self.calendar.next_trading_day(normalize_trade_date(value))

    def resolve_latest_valid_trading_date(self, now: datetime | None = None) -> date | None:
        local = self._local_now(now)
        if self.calendar.is_trading_day(local.date()):
            return local.date()
        return self.calendar.previous_trading_day(local.date())

    @staticmethod
    def _scheduled_session(current: time) -> MarketSession:
        if current < time(9, 15):
            return MarketSession.PRE_OPEN
        if current < time(9, 25):
            return MarketSession.OPEN_AUCTION
        if current < time(9, 30):
            return MarketSession.PRE_OPEN
        if current < time(11, 30):
            return MarketSession.MORNING
        if current < time(13, 0):
            return MarketSession.LUNCH_BREAK
        if current < time(14, 57):
            return MarketSession.AFTERNOON
        if current < time(15, 0):
            return MarketSession.CLOSE_AUCTION
        return MarketSession.CLOSED

    @staticmethod
    def _label(session: MarketSession) -> str:
        return {
            MarketSession.PRE_OPEN: "开盘前",
            MarketSession.OPEN_AUCTION: "开盘集合竞价",
            MarketSession.MORNING: "交易中",
            MarketSession.LUNCH_BREAK: "午间休市",
            MarketSession.AFTERNOON: "交易中",
            MarketSession.CLOSE_AUCTION: "收盘集合竞价",
            MarketSession.CLOSED: "已收盘",
            MarketSession.NON_TRADING_DAY: "非交易日",
            MarketSession.DATA_ABNORMAL: "行情数据异常",
        }[session]

    def resolve_session(
        self,
        now: datetime | None = None,
        *,
        data_status: str = "ok",
    ) -> MarketSessionResolution:
        """Resolve session and data basis without substituting weekday heuristics.

        ``data_status`` is intentionally an input rather than a calendar fact:
        a caller that cannot obtain the required live batch can expose
        ``DATA_ABNORMAL`` while preserving the calendar's authoritative answer.
        """

        local = self._local_now(now)
        day = local.date()
        calendar_row = self.calendar.row_for(day)
        previous = self.calendar.previous_trading_day(day)
        next_day = self.calendar.next_trading_day(day)
        normalized_status = str(data_status or "ok").lower()

        if calendar_row is None:
            session = MarketSession.DATA_ABNORMAL
            return MarketSessionResolution(
                market=self.market,
                timezone="Asia/Shanghai",
                session=session.value,
                scheduled_session=None,
                is_trading_day=None,
                is_market_open=False,
                trading_date=day,
                latest_valid_trading_date=previous,
                previous_trading_date=previous,
                next_trading_date=next_day,
                resolved_at=local,
                data_status="abnormal",
                data_basis=MarketDataBasis.PREVIOUS_SESSION_CLOSE.value,
                display_label=self._label(session),
            )

        if not calendar_row.is_open:
            session = MarketSession.NON_TRADING_DAY
            return MarketSessionResolution(
                market=self.market,
                timezone="Asia/Shanghai",
                session=session.value,
                scheduled_session=None,
                is_trading_day=False,
                is_market_open=False,
                trading_date=day,
                latest_valid_trading_date=previous,
                previous_trading_date=previous,
                next_trading_date=next_day,
                resolved_at=local,
                data_status=normalized_status if normalized_status != "ok" else "ok",
                data_basis=MarketDataBasis.PREVIOUS_SESSION_CLOSE.value,
                display_label=self._label(session),
            )

        scheduled = self._scheduled_session(local.time())
        basis = (
            MarketDataBasis.SESSION_CLOSE
            if scheduled == MarketSession.CLOSED
            else MarketDataBasis.PREVIOUS_SESSION_CLOSE
            if scheduled == MarketSession.PRE_OPEN
            else MarketDataBasis.LIVE
        )
        effective = scheduled
        if normalized_status == "abnormal" and scheduled in _LIVE_SESSIONS | {MarketSession.CLOSED}:
            effective = MarketSession.DATA_ABNORMAL
        return MarketSessionResolution(
            market=self.market,
            timezone="Asia/Shanghai",
            session=effective.value,
            scheduled_session=scheduled.value if effective != scheduled else None,
            is_trading_day=True,
            is_market_open=scheduled in _OPEN_SESSIONS and effective != MarketSession.DATA_ABNORMAL,
            trading_date=day,
            latest_valid_trading_date=day,
            previous_trading_date=previous,
            next_trading_date=next_day,
            resolved_at=local,
            data_status=normalized_status,
            data_basis=basis.value,
            display_label=self._label(effective),
        )


__all__ = [
    "MarketDataBasis",
    "MarketSession",
    "MarketSessionResolution",
    "MarketSessionService",
]
