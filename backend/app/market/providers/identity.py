"""Concrete SecurityMaster and TradingCalendar provider adapters.

The official calendar provider is intentionally offline.  It gives a newly
upgraded deployment an authoritative local calendar before the scheduler is
started, while the Eastmoney adapters provide explicit operator-triggered or
background refresh paths without making FastAPI startup depend on the network.
"""
from __future__ import annotations

import bisect
import math
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

import requests

from ..codes import exchange_for_code, normalize_security_code
from .base import CalendarProvider, SecurityProvider
from .fuyao import FuyaoCalendarProvider, FuyaoSecurityProvider


EASTMONEY_SECURITY_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_CALENDAR_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

OFFICIAL_CALENDAR_SOURCE = "sse_official_schedule"
CHINA_TZ = ZoneInfo("Asia/Shanghai")

# Published SSE/SZSE/BSE holiday closures.  Weekends are always closed even
# when they are statutory make-up workdays.
_OFFICIAL_CLOSURES: dict[int, tuple[tuple[date, date], ...]] = {
    2025: (
        (date(2025, 1, 1), date(2025, 1, 1)),
        (date(2025, 1, 28), date(2025, 2, 4)),
        (date(2025, 4, 4), date(2025, 4, 6)),
        (date(2025, 5, 1), date(2025, 5, 5)),
        (date(2025, 5, 31), date(2025, 6, 2)),
        (date(2025, 10, 1), date(2025, 10, 8)),
    ),
    2026: (
        (date(2026, 1, 1), date(2026, 1, 3)),
        (date(2026, 2, 15), date(2026, 2, 23)),
        (date(2026, 4, 4), date(2026, 4, 6)),
        (date(2026, 5, 1), date(2026, 5, 5)),
        (date(2026, 6, 19), date(2026, 6, 21)),
        (date(2026, 9, 25), date(2026, 9, 27)),
        (date(2026, 10, 1), date(2026, 10, 7)),
    ),
}


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _is_official_open(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    closures = _OFFICIAL_CLOSURES.get(day.year)
    if closures is None:
        return False
    return not any(start <= day <= end for start, end in closures)


def _linked_calendar_rows(
    days: list[date],
    *,
    open_days: list[date],
    source: str,
    market: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in days:
        index = bisect.bisect_left(open_days, day)
        is_open = index < len(open_days) and open_days[index] == day
        previous_index = index - 1
        next_index = index + 1 if is_open else index
        rows.append(
            {
                "trade_date": day,
                "market": market,
                "is_open": is_open,
                "previous_trade_date": open_days[previous_index] if previous_index >= 0 else None,
                "next_trade_date": open_days[next_index] if next_index < len(open_days) else None,
                "source": source,
            }
        )
    return rows


class OfficialCNCalendarProvider(CalendarProvider):
    """Offline calendar built from published mainland exchange closures."""

    name = OFFICIAL_CALENDAR_SOURCE

    @property
    def supported_years(self) -> tuple[int, ...]:
        return tuple(sorted(_OFFICIAL_CLOSURES))

    def get_calendar(
        self,
        start: date,
        end: date,
        *,
        market: str = "CN",
    ) -> list[dict[str, Any]]:
        if start > end:
            raise ValueError("calendar start must not be after end")
        normalized_market = str(market or "CN").strip().upper()
        if normalized_market != "CN":
            raise ValueError(f"unsupported calendar market: {normalized_market}")

        supported_days = [
            day
            for day in _date_range(start, end)
            if day.year in _OFFICIAL_CLOSURES
        ]
        if not supported_days:
            return []
        full_start = date(min(self.supported_years), 1, 1)
        full_end = date(max(self.supported_years), 12, 31)
        open_days = [day for day in _date_range(full_start, full_end) if _is_official_open(day)]
        return _linked_calendar_rows(
            supported_days,
            open_days=open_days,
            source=self.name,
            market=normalized_market,
        )


def _mapping_payload(response: Any) -> Mapping[str, Any]:
    if isinstance(response, Mapping):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        payload = json_method()
        return payload if isinstance(payload, Mapping) else {}
    return {}


def _security_type(code: str) -> str:
    return "ETF" if code.startswith(("15", "16", "18", "50", "51", "56", "58")) else "STOCK"


class EastmoneySecurityProvider(SecurityProvider):
    """Batch SecurityMaster discovery using Eastmoney's public list endpoint."""

    name = "eastmoney_security"

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        request: Callable[..., Any] | None = None,
        timeout: float = 10.0,
        page_size: int = 5000,
        max_pages: int = 20,
        min_interval_seconds: float = 0.8,
        market_filter: str = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
    ) -> None:
        self.session = session or requests.Session()
        self.request = request
        self.timeout = float(timeout)
        self.page_size = max(1, min(int(page_size), 5000))
        self.max_pages = max(1, int(max_pages))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.market_filter = market_filter
        self._last_request = 0.0
        self._lock = Lock()

    def _get(self, page: int) -> Mapping[str, Any]:
        params = {
            "pn": page,
            "pz": self.page_size,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": self.market_filter,
            "fields": "f12,f14",
        }
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if self.min_interval_seconds and elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            request = self.request or self.session.get
            response = request(
                EASTMONEY_SECURITY_URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
                timeout=self.timeout,
            )
            self._last_request = time.monotonic()
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        return _mapping_payload(response)

    def list_securities(self, *, market: str = "CN") -> list[dict[str, Any]]:
        normalized_market = str(market or "CN").strip().upper()
        if normalized_market != "CN":
            raise ValueError(f"unsupported security market: {normalized_market}")
        result: dict[str, dict[str, Any]] = {}
        fetched_at = datetime.now(UTC)
        total: int | None = None
        for page in range(1, self.max_pages + 1):
            payload = self._get(page)
            data = payload.get("data") or {}
            if not isinstance(data, Mapping):
                break
            raw_rows = data.get("diff") or []
            if isinstance(raw_rows, Mapping):
                raw_rows = list(raw_rows.values())
            if not isinstance(raw_rows, list) or not raw_rows:
                break
            try:
                total = int(data.get("total")) if data.get("total") is not None else total
            except (TypeError, ValueError):
                pass
            for raw in raw_rows:
                if not isinstance(raw, Mapping):
                    continue
                code = normalize_security_code(raw.get("f12") or raw.get("code"))
                exchange = exchange_for_code(code)
                if not code or exchange is None:
                    continue
                result.setdefault(
                    code,
                    {
                        "market": normalized_market,
                        "exchange": exchange,
                        "code": code,
                        "name": str(raw.get("f14") or raw.get("name") or "").strip() or None,
                        "security_type": _security_type(code),
                        "status": "ACTIVE",
                        "source": self.name,
                        "source_updated_at": fetched_at,
                    },
                )
            if total is not None and page * self.page_size >= total:
                break
            if len(raw_rows) < self.page_size and total is None:
                break
        return list(result.values())


class EastmoneyCalendarProvider(CalendarProvider):
    """Historical SSE trading dates inferred from the SSE Composite kline."""

    name = "eastmoney_sse_calendar"

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        request: Callable[..., Any] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.session = session or requests.Session()
        self.request = request
        self.timeout = float(timeout)

    def get_calendar(
        self,
        start: date,
        end: date,
        *,
        market: str = "CN",
    ) -> list[dict[str, Any]]:
        if start > end:
            raise ValueError("calendar start must not be after end")
        normalized_market = str(market or "CN").strip().upper()
        if normalized_market != "CN":
            raise ValueError(f"unsupported calendar market: {normalized_market}")
        historical_end = min(end, datetime.now(CHINA_TZ).date())
        if start > historical_end:
            return []
        request = self.request or self.session.get
        response = request(
            EASTMONEY_CALENDAR_URL,
            params={
                "secid": "1.000001",
                "klt": 101,
                "fqt": 0,
                "beg": start.strftime("%Y%m%d"),
                "end": historical_end.strftime("%Y%m%d"),
                "lmt": max(1, math.ceil((historical_end - start).days * 5 / 7) + 32),
                "fields1": "f1",
                "fields2": "f51",
            },
            headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
            timeout=self.timeout,
        )
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        payload = _mapping_payload(response)
        data = payload.get("data") or {}
        raw_klines = data.get("klines") if isinstance(data, Mapping) else []
        open_days: list[date] = []
        for raw in raw_klines or []:
            first = str(raw).split(",", 1)[0]
            try:
                value = date.fromisoformat(first[:10])
            except ValueError:
                continue
            if start <= value <= historical_end:
                open_days.append(value)
        open_days = sorted(set(open_days))
        return _linked_calendar_rows(
            list(_date_range(start, historical_end)),
            open_days=open_days,
            source=self.name,
            market=normalized_market,
        )


def build_security_provider(name: str, **kwargs: Any) -> SecurityProvider:
    canonical = str(name or "").strip().lower().replace("-", "_")
    if canonical in {"fuyao", "ths", "tonghuashun", "fuyao_security"}:
        return FuyaoSecurityProvider(**kwargs)
    if canonical in {"eastmoney", "eastmoney_security"}:
        return EastmoneySecurityProvider(**kwargs)
    raise ValueError(f"unknown security provider: {name}")


def build_calendar_provider(name: str, **kwargs: Any) -> CalendarProvider:
    canonical = str(name or "").strip().lower().replace("-", "_")
    if canonical in {"fuyao", "ths", "tonghuashun", "fuyao_calendar"}:
        return FuyaoCalendarProvider(**kwargs)
    if canonical in {"official", "official_cn", OFFICIAL_CALENDAR_SOURCE}:
        return OfficialCNCalendarProvider()
    if canonical in {"eastmoney", "eastmoney_calendar", "eastmoney_sse_calendar"}:
        return EastmoneyCalendarProvider(**kwargs)
    raise ValueError(f"unknown calendar provider: {name}")


__all__ = [
    "EASTMONEY_CALENDAR_URL",
    "EASTMONEY_SECURITY_URL",
    "EastmoneyCalendarProvider",
    "EastmoneySecurityProvider",
    "FuyaoCalendarProvider",
    "FuyaoSecurityProvider",
    "OFFICIAL_CALENDAR_SOURCE",
    "OfficialCNCalendarProvider",
    "build_calendar_provider",
    "build_security_provider",
]
