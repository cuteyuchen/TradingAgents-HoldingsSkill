"""Offline tests for the Phase B SecurityMaster and TradingCalendar foundation."""
from datetime import date, datetime, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.market_models import SecurityMaster, TradingCalendar
from app.services.security_master import (
    BSE,
    ETF,
    SSE,
    SZSE,
    STOCK,
    get_market_universe,
    get_security,
    infer_exchange,
    normalize_security_code,
    upsert_security,
)
from app.services.trading_calendar import (
    AFTERNOON,
    AUCTION,
    CLOSED,
    LUNCH,
    MORNING,
    PRE_MARKET,
    TradingCalendarService,
    upsert_calendar,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    SecurityMaster.__table__.create(engine)
    TradingCalendar.__table__.create(engine)
    return Session(engine)


def test_security_code_normalization_and_exchange_inference():
    assert normalize_security_code("600519") == "600519"
    assert normalize_security_code("sh600519") == "600519"
    assert normalize_security_code("600519.SH") == "600519"
    assert normalize_security_code("SH600519") == "600519"
    assert normalize_security_code("not-a-security") == ""

    assert infer_exchange("600519") == SSE
    assert infer_exchange("159915") == SZSE
    assert infer_exchange("830799") == BSE
    assert infer_exchange("000001", "深交所") == SZSE


def test_security_upsert_updates_status_and_keeps_identity():
    db = _session()
    try:
        first = upsert_security(
            db,
            {
                "code": "600519.SH",
                "name": "贵州茅台",
                "security_type": STOCK,
                "source": "fixture",
            },
        )
        db.commit()
        assert first.id is not None
        assert first.exchange == SSE
        assert first.symbol == "600519.SH"

        second = upsert_security(
            db,
            {
                "code": "600519",
                "name": "贵州茅台",
                "security_type": STOCK,
                "status": "SUSPENDED",
                "is_suspended": True,
                "source": "fixture-refresh",
            },
        )
        db.commit()
        assert second.id == first.id
        assert second.status == "SUSPENDED"
        assert second.is_suspended is True
        assert get_security(db, "sh600519").id == first.id
    finally:
        db.close()


def test_security_universe_distinguishes_stock_etf_and_filters_state():
    db = _session()
    try:
        upsert_security(db, {"code": "600519", "name": "贵州茅台", "security_type": STOCK})
        upsert_security(db, {"code": "510300", "name": "沪深300ETF", "security_type": ETF, "etf_category": "BROAD_ETF"})
        upsert_security(db, {"code": "000001", "name": "平安银行", "security_type": STOCK, "is_st": True})
        upsert_security(db, {"code": "300750", "name": "宁德时代", "security_type": STOCK, "is_suspended": True})
        db.commit()

        stocks = get_market_universe(db, security_type=STOCK, include_st=False, include_suspended=False)
        assert [item.code for item in stocks] == ["600519"]
        etfs = get_market_universe(db, security_type=ETF)
        assert [item.code for item in etfs] == ["510300"]
        assert get_market_universe(db, exchange="SSE", security_type=ETF)[0].code == "510300"
    finally:
        db.close()


def test_calendar_persists_open_and_holiday_and_previous_next_links():
    db = _session()
    try:
        upsert_calendar(
            db,
            [
                {"trade_date": "2026-08-20", "is_open": True, "source": "fixture"},
                {
                    "trade_date": "2026-08-21",
                    "is_open": False,
                    "previous_trade_date": "2026-08-20",
                    "next_trade_date": "2026-08-24",
                    "source": "fixture",
                },
                {
                    "trade_date": "2026-08-24",
                    "is_open": True,
                    "previous_trade_date": "2026-08-20",
                    "source": "fixture",
                },
            ]
        )
        db.commit()
        service = TradingCalendarService(db)
        assert service.is_trading_day(date(2026, 8, 20)) is True
        assert service.is_trading_day(date(2026, 8, 21)) is False
        assert service.is_trading_day(date(2026, 8, 22)) is False  # weekend absent from DB
        assert service.previous_trading_day(date(2026, 8, 21)) == date(2026, 8, 20)
        assert service.next_trading_day(date(2026, 8, 21)) == date(2026, 8, 24)
        assert service.latest_trading_day() == date(2026, 8, 24)
    finally:
        db.close()


def test_calendar_sessions_use_asia_shanghai_and_closed_on_holiday():
    db = _session()
    try:
        upsert_calendar(db, [{"trade_date": "2026-08-20", "is_open": True}])
        db.commit()
        service = TradingCalendarService(db)
        assert service.current_session(datetime(2026, 8, 20, 0, 59, tzinfo=timezone.utc)) == PRE_MARKET
        assert service.current_session(datetime(2026, 8, 20, 1, 20, tzinfo=timezone.utc)) == AUCTION
        assert service.current_session(datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)) == MORNING
        assert service.current_session(datetime(2026, 8, 20, 3, 30, tzinfo=timezone.utc)) == LUNCH
        assert service.current_session(datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)) == AFTERNOON
        assert service.current_session(datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)) == CLOSED
        assert service.current_session(datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)) == CLOSED
        assert service.is_market_session(datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)) is True
        assert service.is_market_session(datetime(2026, 8, 20, 3, 30, tzinfo=timezone.utc)) is False
    finally:
        db.close()


def test_new_tables_have_expected_unique_constraints():
    db = _session()
    try:
        inspector = inspect(db.bind)
        assert "uq_security_master_market_exchange_code" in {
            item["name"] for item in inspector.get_unique_constraints("security_master")
        }
        assert "uq_trading_calendar_market_date" in {
            item["name"] for item in inspector.get_unique_constraints("trading_calendar")
        }
    finally:
        db.close()
