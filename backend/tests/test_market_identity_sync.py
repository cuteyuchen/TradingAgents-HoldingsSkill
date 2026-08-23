"""Phase B.1 production lifecycle tests for identity data providers."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.market.providers.identity import (
    EastmoneyCalendarProvider,
    EastmoneySecurityProvider,
    OfficialCNCalendarProvider,
)
from app.market.providers import identity as identity_provider
from app.market_models import SecurityMaster, TradingCalendar
from app.services.market_identity_sync import (
    CALENDAR_NOT_INITIALIZED,
    CALENDAR_READY,
    calendar_status,
    ensure_local_calendar,
    start_remote_market_identity_sync,
    sync_security_master_from_provider,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    SecurityMaster.__table__.create(engine)
    TradingCalendar.__table__.create(engine)
    return Session(engine)


def test_official_calendar_bootstrap_initializes_empty_database():
    db = _session()
    try:
        assert calendar_status(db, as_of=date(2026, 8, 23))["status"] == CALENDAR_NOT_INITIALIZED

        status = ensure_local_calendar(db, as_of=date(2026, 8, 23))

        assert status["status"] == CALENDAR_READY
        assert status["current_date_initialized"] is True
        assert status["next_open_date"] == date(2026, 8, 24)
        service_rows = OfficialCNCalendarProvider().get_calendar(
            date(2026, 2, 17),
            date(2026, 2, 24),
        )
        by_date = {row["trade_date"]: row for row in service_rows}
        assert by_date[date(2026, 2, 17)]["is_open"] is False
        assert by_date[date(2026, 2, 24)]["is_open"] is True
    finally:
        db.close()


def test_official_calendar_does_not_guess_unpublished_2027_dates():
    provider = OfficialCNCalendarProvider()

    assert provider.supported_years == (2025, 2026)
    assert provider.get_calendar(date(2027, 1, 1), date(2027, 1, 31)) == []

    db = _session()
    try:
        status = ensure_local_calendar(db, as_of=date(2027, 1, 4), provider=provider)
        assert status["status"] == CALENDAR_NOT_INITIALIZED
        assert status["row_count"] == 0
    finally:
        db.close()


def test_eastmoney_security_provider_discovers_batch_identity_rows():
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(
            {
                "data": {
                    "total": 2,
                    "diff": [
                        {"f12": "600519", "f14": "贵州茅台"},
                        {"f12": "159915", "f14": "创业板ETF"},
                    ],
                }
            }
        )

    provider = EastmoneySecurityProvider(request=request, page_size=5000, min_interval_seconds=0)
    rows = provider.list_securities()

    assert len(calls) == 1
    assert [(row["code"], row["exchange"], row["security_type"]) for row in rows] == [
        ("600519", "SSE", "STOCK"),
        ("159915", "SZSE", "ETF"),
    ]

    db = _session()
    try:
        persisted = sync_security_master_from_provider(db, provider)
        assert len(persisted) == 2
    finally:
        db.close()


def test_eastmoney_calendar_provider_marks_only_returned_kline_dates_open():
    def request(_url, **_kwargs):
        return _Response(
            {
                "data": {
                    "klines": [
                        "2026-08-20,1,2,3",
                        "2026-08-21,1,2,3",
                    ]
                }
            }
        )

    rows = EastmoneyCalendarProvider(request=request).get_calendar(
        date(2026, 8, 20),
        date(2026, 8, 23),
    )
    by_date = {row["trade_date"]: row for row in rows}
    assert by_date[date(2026, 8, 20)]["is_open"] is True
    assert by_date[date(2026, 8, 21)]["is_open"] is True
    assert by_date[date(2026, 8, 22)]["is_open"] is False
    assert by_date[date(2026, 8, 23)]["is_open"] is False


def test_eastmoney_calendar_uses_asia_shanghai_current_date(monkeypatch):
    calls = []

    class _ShanghaiMidnightDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 8, 22, 16, 30, tzinfo=UTC)
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    def request(_url, **kwargs):
        calls.append(kwargs)
        return _Response({"data": {"klines": ["2026-08-21,1,2,3"]}})

    monkeypatch.setattr(identity_provider, "datetime", _ShanghaiMidnightDateTime)
    rows = EastmoneyCalendarProvider(request=request).get_calendar(
        date(2026, 8, 21),
        date(2026, 8, 23),
    )

    assert calls[0]["params"]["end"] == "20260823"
    assert rows[-1]["trade_date"] == date(2026, 8, 23)


def test_remote_sync_is_started_without_running_network_inline(monkeypatch):
    monkeypatch.setattr(settings, "CALENDAR_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "SECURITY_MASTER_SYNC_ENABLED", False)
    observed = {}

    class FakeThread:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def start(self):
            observed["started"] = True

    factory = sessionmaker(bind=create_engine("sqlite:///:memory:", future=True))
    thread = start_remote_market_identity_sync(
        session_factory=factory,
        thread_factory=FakeThread,
    )

    assert thread is not None
    assert observed["started"] is True
    assert observed["daemon"] is True
    assert observed["name"] == "market-identity-sync"
    assert callable(observed["target"])
