"""MARKET-1 session and systemic market-fact contracts."""
from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")


CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _session_factory():
    from app.market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot, MarketScoreSnapshot
    from app.market_models import SecurityMaster, TradingCalendar

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    for model in (SecurityMaster, TradingCalendar, MarketMetricSnapshot, MarketScoreSnapshot, AllAMedianIndexDaily):
        model.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _calendar(db: Session, *rows: tuple[date, bool, date | None, date | None]) -> None:
    from app.market_models import TradingCalendar

    db.add_all(
        [
            TradingCalendar(
                market="CN",
                trade_date=trade_date,
                is_open=is_open,
                previous_trade_date=previous,
                next_trade_date=next_day,
            )
            for trade_date, is_open, previous, next_day in rows
        ]
    )
    db.commit()


def _stock(
    db: Session,
    code: str,
    *,
    exchange: str = "SSE",
    is_st: bool = False,
    is_suspended: bool = False,
    board: str | None = None,
    security_type: str = "STOCK",
) -> None:
    from app.market_models import SecurityMaster

    db.add(
        SecurityMaster(
            market="CN",
            exchange=exchange,
            code=code,
            security_type=security_type,
            status="ACTIVE",
            is_st=is_st,
            is_suspended=is_suspended,
            board=board,
        )
    )


def _snapshot(quotes: list[dict], *, captured_at: datetime) -> dict:
    return {
        "snapshot_id": "snapshot-1",
        "provider": "fixture",
        "quality_status": "VALID",
        "coverage_ratio": 1.0,
        "completed_at": captured_at,
        "quotes": quotes,
    }


def _indices(captured_at: datetime, *, omit: str | None = None) -> list[dict]:
    from app.market.foundation import MAJOR_INDEX_DEFINITIONS

    return [
        {
            "thscode": item["code"],
            "name": item["name"],
            "last_price": 3000.0 + index,
            "pre_close": 2999.0 + index,
            "price_change_ratio_pct": 0.03,
            "source_timestamp": captured_at,
            "quality_status": "VALID",
            "provider": "fixture-index",
        }
        for index, item in enumerate(MAJOR_INDEX_DEFINITIONS)
        if item["code"] != omit
    ]


def test_market_session_uses_calendar_and_shanghai_clock() -> None:
    from app.market.session import MarketSessionService
    from app.services.trading_calendar import TradingCalendarService

    SessionLocal = _session_factory()
    trade_date = date(2026, 9, 7)
    with SessionLocal() as db:
        _calendar(
            db,
            (date(2026, 9, 4), True, date(2026, 9, 3), trade_date),
            (date(2026, 9, 5), False, date(2026, 9, 4), trade_date),
            (date(2026, 9, 6), False, date(2026, 9, 4), trade_date),
            (trade_date, True, date(2026, 9, 4), date(2026, 9, 8)),
            (date(2026, 9, 8), True, trade_date, date(2026, 9, 9)),
        )
        service = MarketSessionService(db)
        expectations = {
            (9, 20): "OPEN_AUCTION",
            (10, 0): "MORNING",
            (12, 0): "LUNCH_BREAK",
            (14, 0): "AFTERNOON",
            (14, 58): "CLOSE_AUCTION",
            (16, 0): "CLOSED",
        }
        for (hour, minute), expected in expectations.items():
            result = service.resolve_session(datetime(2026, 9, 7, hour, minute, tzinfo=CHINA_TZ))
            assert result.session == expected
        assert service.resolve_session(datetime(2026, 9, 7, 9, 25, tzinfo=CHINA_TZ)).session == "PRE_OPEN"
        assert TradingCalendarService(db).current_session(datetime(2026, 9, 7, 9, 25, tzinfo=CHINA_TZ)) == "PRE_MARKET"
        assert TradingCalendarService(db).is_market_session(datetime(2026, 9, 7, 9, 25, tzinfo=CHINA_TZ)) is False
        assert service.resolve_session(datetime(2026, 9, 7, 10, 0, tzinfo=CHINA_TZ)).data_basis == "live"
        assert service.resolve_session(datetime(2026, 9, 7, 16, 0, tzinfo=CHINA_TZ)).data_basis == "session_close"
        assert service.resolve_session(datetime(2026, 9, 7, 1, 20, tzinfo=UTC)).session == "OPEN_AUCTION"
        weekend = service.resolve_session(datetime(2026, 9, 6, 14, 0, tzinfo=CHINA_TZ))
        assert weekend.session == "NON_TRADING_DAY"
        assert weekend.data_basis == "previous_session_close"
        assert weekend.latest_valid_trading_date == date(2026, 9, 4)


def test_calendar_missing_or_quote_failure_is_not_reported_as_closed() -> None:
    from app.market.session import MarketSessionService

    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _calendar(db, (date(2026, 9, 7), True, date(2026, 9, 4), date(2026, 9, 8)))
        service = MarketSessionService(db)
        abnormal = service.resolve_session(datetime(2026, 9, 7, 10, 0, tzinfo=CHINA_TZ), data_status="abnormal")
        assert abnormal.session == "DATA_ABNORMAL"
        assert abnormal.scheduled_session == "MORNING"
        assert abnormal.data_status == "abnormal"
        missing = service.resolve_session(datetime(2026, 9, 9, 10, 0, tzinfo=CHINA_TZ))
        assert missing.session == "DATA_ABNORMAL"
        assert missing.is_trading_day is None


def test_previous_and_next_trading_day_follow_calendar_across_holiday() -> None:
    from app.market.session import MarketSessionService

    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _calendar(
            db,
            (date(2026, 9, 30), True, date(2026, 9, 29), date(2026, 10, 9)),
            (date(2026, 10, 1), False, date(2026, 9, 30), date(2026, 10, 9)),
            (date(2026, 10, 8), False, date(2026, 9, 30), date(2026, 10, 9)),
            (date(2026, 10, 9), True, date(2026, 9, 30), date(2026, 10, 12)),
        )
        service = MarketSessionService(db)
        assert service.resolve_previous_trading_date(date(2026, 10, 9)) == date(2026, 9, 30)
        assert service.resolve_next_trading_date(date(2026, 9, 30)) == date(2026, 10, 9)
        holiday = service.resolve_session(datetime(2026, 10, 1, 11, 0, tzinfo=CHINA_TZ))
        assert holiday.session == "NON_TRADING_DAY"
        assert holiday.latest_valid_trading_date == date(2026, 9, 30)


def test_all_a_median_breadth_limits_and_etf_exclusion() -> None:
    from app.market.foundation import MarketFoundationService

    SessionLocal = _session_factory()
    captured_at = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
    with SessionLocal() as db:
        _calendar(db, (date(2026, 9, 7), True, date(2026, 9, 4), date(2026, 9, 8)))
        _stock(db, "600001")  # -10%, normal main board
        _stock(db, "600002")  # +10%, normal main board
        _stock(db, "600003", is_st=True)  # +5%, ST
        _stock(db, "300001", exchange="SZSE", board="CHINEXT")  # +20%
        _stock(db, "688001", board="STAR")  # -20%
        _stock(db, "920001", exchange="BSE", board="BSE")  # -30%
        _stock(db, "000001", exchange="SZSE")  # unchanged
        _stock(db, "600004", is_suspended=True)
        _stock(db, "600005")  # quote exists but has no usable prev_close
        _stock(db, "159915", exchange="SZSE", security_type="ETF")
        db.commit()
        quotes = [
            {"code": "600001", "price": 9.0, "prev_close": 10.0, "amount": 10, "volume": 99999, "quality_status": "VALID"},
            {"code": "600002", "price": 11.0, "prev_close": 10.0, "amount": 100, "volume": 1, "quality_status": "VALID"},
            {"code": "600003", "price": 10.5, "prev_close": 10.0, "amount": 100, "quality_status": "VALID"},
            {"code": "300001", "price": 12.0, "prev_close": 10.0, "amount": 100, "quality_status": "VALID"},
            {"code": "688001", "price": 8.0, "prev_close": 10.0, "amount": 100, "quality_status": "VALID"},
            {"code": "920001", "price": 7.0, "prev_close": 10.0, "amount": 100, "quality_status": "VALID"},
            {"code": "000001", "price": 10.0, "prev_close": 10.0, "amount": 100, "quality_status": "VALID"},
            {"code": "600005", "price": 10.0, "amount": 100, "quality_status": "VALID"},
            {"code": "159915", "price": 99.0, "prev_close": 10.0, "amount": 99_999_999, "quality_status": "VALID"},
        ]
        result = MarketFoundationService(db).build_from_snapshot(
            _snapshot(quotes, captured_at=captured_at),
            now=datetime(2026, 9, 7, 10, 0, tzinfo=CHINA_TZ),
            major_index_rows=_indices(captured_at),
        )
        median_metric = result["all_a_median"]
        breadth = result["breadth"]
        assert median_metric["daily_median_return"] == 0.0
        assert median_metric["universe_total"] == 9
        assert median_metric["eligible_count"] == 7
        assert median_metric["excluded_count"] == 1
        assert median_metric["suspended_count"] == 1
        assert breadth["advancers"] == 3
        assert breadth["decliners"] == 3
        assert breadth["unchanged"] == 1
        assert breadth["limit_up"] == 3
        assert breadth["limit_down"] == 3
        assert breadth["total"] == 8
        assert result["turnover_concentration"]["total_turnover"] == 610
        assert result["turnover_concentration"]["ratio"] == 100 / 610


def test_top5_turnover_uses_amount_and_ceiling_population() -> None:
    from app.market.foundation import MarketFoundationService

    SessionLocal = _session_factory()
    captured_at = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
    with SessionLocal() as db:
        _calendar(db, (date(2026, 9, 7), True, date(2026, 9, 4), date(2026, 9, 8)))
        quotes: list[dict] = []
        for index in range(100):
            code = f"{600000 + index:06d}"
            _stock(db, code)
            quotes.append({
                "code": code,
                "price": 10.0,
                "prev_close": 10.0,
                "amount": 1000.0 if index < 5 else 1.0,
                "volume": 1.0 if index < 5 else 1_000_000.0,
                "quality_status": "VALID",
            })
        _stock(db, "159916", exchange="SZSE", security_type="ETF")
        db.commit()
        service = MarketFoundationService(db)
        current = service.build_from_snapshot(
            _snapshot(quotes, captured_at=captured_at),
            now=datetime(2026, 9, 7, 10, 0, tzinfo=CHINA_TZ),
            major_index_rows=_indices(captured_at),
        )["turnover_concentration"]
        assert current["eligible_count"] == 100
        assert current["top_n"] == 5
        assert current["ratio"] == 5000.0 / 5095.0
        _stock(db, "600100")
        db.commit()
        quotes.append({"code": "600100", "price": 10.0, "prev_close": 10.0, "amount": 1.0, "quality_status": "VALID"})
        expanded = service.build_from_snapshot(
            _snapshot(quotes, captured_at=captured_at + timedelta(seconds=1)),
            now=datetime(2026, 9, 7, 10, 1, tzinfo=CHINA_TZ),
            major_index_rows=_indices(captured_at),
        )["turnover_concentration"]
        assert expanded["eligible_count"] == 101
        assert expanded["top_n"] == 6


def test_overview_uses_one_batch_and_weekend_reuses_previous_close() -> None:
    from app.market.foundation import MarketFoundationService
    from app.market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot

    SessionLocal = _session_factory()
    captured_at = datetime(2026, 9, 4, 8, 5, tzinfo=UTC)
    calls = {"quotes": 0, "indices": 0}
    with SessionLocal() as db:
        _calendar(
            db,
            (date(2026, 9, 4), True, date(2026, 9, 3), date(2026, 9, 7)),
            (date(2026, 9, 5), False, date(2026, 9, 4), date(2026, 9, 7)),
            (date(2026, 9, 6), False, date(2026, 9, 4), date(2026, 9, 7)),
            (date(2026, 9, 7), True, date(2026, 9, 4), date(2026, 9, 8)),
        )
        _stock(db, "600001")
        db.commit()

        def quote_loader(**_kwargs):
            calls["quotes"] += 1
            return _snapshot([
                {"code": "600001", "price": 10.0, "prev_close": 10.0, "amount": 1_000_000.0, "quality_status": "VALID"}
            ], captured_at=captured_at)

        def index_loader(**_kwargs):
            calls["indices"] += 1
            return {"items": _indices(captured_at)}

        close_service = MarketFoundationService(
            db,
            quote_snapshot_loader=quote_loader,
            index_snapshot_loader=index_loader,
        )
        close = close_service.overview(now=datetime(2026, 9, 4, 16, 5, tzinfo=CHINA_TZ))
        assert close["session"]["session"] == "CLOSED"
        assert close["session"]["data_basis"] == "session_close"
        assert calls == {"quotes": 1, "indices": 1}
        assert db.execute(select(MarketMetricSnapshot)).scalar_one_or_none() is not None
        assert db.execute(select(AllAMedianIndexDaily)).scalar_one_or_none() is not None

        def forbidden_loader(**_kwargs):
            raise AssertionError("weekend must not fetch live quotes")

        weekend = MarketFoundationService(db, quote_snapshot_loader=forbidden_loader).overview(
            now=datetime(2026, 9, 6, 13, 0, tzinfo=CHINA_TZ)
        )
        assert weekend["session"]["session"] == "NON_TRADING_DAY"
        assert weekend["session"]["data_basis"] == "previous_session_close"
        assert weekend["quote_as_of"] == captured_at.isoformat()

        weekend_indices = MarketFoundationService(
            db,
            index_snapshot_loader=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("weekend must not refresh major indices")
            ),
        ).major_indices(now=datetime(2026, 9, 6, 13, 0, tzinfo=CHINA_TZ))
        assert len(weekend_indices) == 6
        assert all(item["as_of"] == captured_at.isoformat() for item in weekend_indices)


def test_single_missing_major_index_keeps_partial_contract_and_unknown_risk_when_core_missing() -> None:
    from app.market.foundation import MAJOR_INDEX_DEFINITIONS, MarketFoundationService

    SessionLocal = _session_factory()
    captured_at = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
    with SessionLocal() as db:
        _calendar(db, (date(2026, 9, 7), True, date(2026, 9, 4), date(2026, 9, 8)))
        _stock(db, "600001")
        db.commit()
        partial = MarketFoundationService(db).build_from_snapshot(
            _snapshot([
                {"code": "600001", "price": 10.0, "prev_close": 10.0, "amount": 100.0, "quality_status": "VALID"}
            ], captured_at=captured_at),
            now=datetime(2026, 9, 7, 10, 0, tzinfo=CHINA_TZ),
            major_index_rows=_indices(captured_at, omit="000688.SH"),
        )
        assert len(partial["major_indices"]) == 6
        missing = next(item for item in partial["major_indices"] if item["code"] == "000688.SH")
        assert missing["status"] == "unavailable"
        assert missing["as_of"] is None
        assert partial["all_a_median"]["quality_grade"] == "B"

        unavailable = MarketFoundationService(db).build_from_snapshot(
            _snapshot([], captured_at=captured_at),
            now=datetime(2026, 9, 7, 10, 0, tzinfo=CHINA_TZ),
            major_index_rows=_indices(captured_at),
        )
        assert unavailable["systemic_risk"]["risk_level"] == "UNKNOWN"
        assert unavailable["systemic_risk"]["risk_score"] is None
        assert "CRITICAL_MARKET_DATA_UNAVAILABLE" in unavailable["systemic_risk"]["quality_flags"]
        assert {item["code"] for item in partial["major_indices"]} == {item["code"] for item in MAJOR_INDEX_DEFINITIONS}


def test_history_metrics_expose_20d_trend_and_250d_percentiles() -> None:
    from app.market.foundation import MarketFoundationService, SYSTEMIC_MARKET_VERSION
    from app.market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot
    from app.market.schemas import MarketOverviewResponse

    SessionLocal = _session_factory()
    current_date = date(2026, 9, 7)
    captured_at = datetime(2026, 9, 7, 8, 5, tzinfo=UTC)
    with SessionLocal() as db:
        _calendar(db, (current_date, True, current_date - timedelta(days=1), current_date + timedelta(days=1)))
        _stock(db, "600001")
        for offset in range(250, 0, -1):
            trade_date = current_date - timedelta(days=offset)
            index_value = 1250.0 - offset
            db.add(AllAMedianIndexDaily(
                market="CN",
                trade_date=trade_date,
                median_return=0.0,
                index_value=index_value,
                eligible_count=1,
                quality_status="VALID",
                calculation_version=SYSTEMIC_MARKET_VERSION,
                available_at=captured_at - timedelta(days=offset),
            ))
            db.add(MarketMetricSnapshot(
                snapshot_id=f"history-{offset}",
                market="CN",
                trade_date=trade_date,
                captured_at=(captured_at - timedelta(days=offset)).replace(tzinfo=None),
                calculation_version=SYSTEMIC_MARKET_VERSION,
                metrics_json={
                    "foundation": {
                        "turnover_concentration": {"ratio": 0.1},
                        "total_turnover": {"value": 1_000_000.0},
                    }
                },
            ))
        db.commit()
        result = MarketFoundationService(db).build_from_snapshot(
            _snapshot([
                {"code": "600001", "price": 10.0, "prev_close": 10.0, "amount": 2_000_000.0, "quality_status": "VALID"}
            ], captured_at=captured_at),
            now=datetime(2026, 9, 7, 16, 0, tzinfo=CHINA_TZ),
            major_index_rows=_indices(captured_at),
        )
        assert result["all_a_median"]["trend_20d"] == 1249.0 / 1230.0 - 1.0
        assert result["all_a_median"]["percentile_250d"] == 1.0
        assert result["turnover_concentration"]["avg_20d"] == 0.1
        assert result["turnover_concentration"]["percentile_250d"] == 1.0
        assert result["total_turnover"]["avg_20d"] == 1_000_000.0
        payload = dict(result)
        payload.pop("_universe_counts", None)
        assert MarketOverviewResponse.model_validate(payload).quote_as_of == captured_at


def test_daily_persistence_keeps_its_capture_distinct_from_other_metric_versions() -> None:
    from app.market.foundation import MarketFoundationService, SYSTEMIC_MARKET_VERSION
    from app.market_engine_models import MarketMetricSnapshot

    SessionLocal = _session_factory()
    captured_at = datetime(2026, 9, 7, 8, 5, tzinfo=UTC)
    with SessionLocal() as db:
        _calendar(db, (date(2026, 9, 7), True, date(2026, 9, 4), date(2026, 9, 8)))
        _stock(db, "600001")
        db.commit()
        service = MarketFoundationService(db)
        overview = service.build_from_snapshot(
            _snapshot([
                {"code": "600001", "price": 10.0, "prev_close": 10.0, "amount": 100.0, "quality_status": "VALID"}
            ], captured_at=captured_at),
            now=datetime(2026, 9, 7, 16, 0, tzinfo=CHINA_TZ),
            major_index_rows=_indices(captured_at),
        )
        db.add(MarketMetricSnapshot(
            snapshot_id="phase-c-same-capture",
            market="CN",
            trade_date=date(2026, 9, 7),
            captured_at=captured_at.replace(tzinfo=None),
            calculation_version="market-engine-v1",
        ))
        db.commit()

        service.persist_daily_snapshot(overview, snapshot=_snapshot([], captured_at=captured_at))
        db.commit()
        service.persist_daily_snapshot(overview, snapshot=_snapshot([], captured_at=captured_at))
        db.commit()

        rows = list(db.execute(
            select(MarketMetricSnapshot).where(
                MarketMetricSnapshot.calculation_version == SYSTEMIC_MARKET_VERSION,
            )
        ).scalars())
        assert len(rows) == 1
        assert rows[0].captured_at != captured_at.replace(tzinfo=None)


def test_market_foundation_api_requires_authentication() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    for path in (
        "/api/v3/market/session",
        "/api/v3/market/major-indices",
        "/api/v3/market/systemic-risk",
        "/api/v3/market/overview",
    ):
        assert client.get(path).status_code == 401


def test_market_foundation_api_contract_preserves_partial_index_response(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.market.foundation import MarketFoundationService
    from app.routers import market_engine_v3
    from app.v2_dependencies import get_current_user

    SessionLocal = _session_factory()
    captured_at = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
    with SessionLocal() as db:
        _calendar(db, (date(2026, 9, 7), True, date(2026, 9, 4), date(2026, 9, 8)))
        _stock(db, "600001")
        db.commit()
        fixture = MarketFoundationService(db).build_from_snapshot(
            _snapshot([
                {"code": "600001", "price": 10.0, "prev_close": 10.0, "amount": 100.0, "quality_status": "VALID"}
            ], captured_at=captured_at),
            now=datetime(2026, 9, 7, 10, 0, tzinfo=CHINA_TZ),
            major_index_rows=_indices(captured_at, omit="000688.SH"),
        )
        fixture.pop("_universe_counts", None)

        class FixtureFoundation:
            def __init__(self, _db):
                pass

            def overview(self, *, now=None):
                return fixture

            def major_indices(self, *, now=None):
                return fixture["major_indices"]

        monkeypatch.setattr(market_engine_v3, "MarketFoundationService", FixtureFoundation)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: object()
        try:
            client = TestClient(app)
            overview = client.get("/api/v3/market/overview?as_of=2026-09-07T10:00:00%2B08:00")
            assert overview.status_code == 200
            assert len(overview.json()["major_indices"]) == 6
            assert next(item for item in overview.json()["major_indices"] if item["code"] == "000688.SH")["status"] == "unavailable"
            assert client.get("/api/v3/market/major-indices").status_code == 200
            assert client.get("/api/v3/market/session?as_of=2026-09-07T10:00:00%2B08:00").json()["session"] == "MORNING"
        finally:
            app.dependency_overrides.clear()
