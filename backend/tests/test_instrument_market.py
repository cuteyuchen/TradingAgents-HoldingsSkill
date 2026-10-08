"""Offline contracts for MARKET-2, including session, cache and partial failures."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.market.instrument_schemas import InstrumentCapabilities, InstrumentQuoteResponse
from app.market.instruments import InstrumentLookupError, InstrumentMarketService
from app.market.models import NormalizedQuote
from app.market.providers.base import QuoteProvider
from app.market.providers.instruments import InstrumentDataAdapters, InstrumentProviderResult
from app.market_models import SecurityMaster, TradingCalendar
from app.services.market_snapshot_service import clear_market_foundation_snapshot_cache
from app.services.security_master import get_security, upsert_security


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 10, 10, 0, tzinfo=TZ)


def quote(code="600519.SH", **changes):
    values = dict(
        code=code, provider="fixture", price=100, prev_close=99, open=99,
        high=101, low=98, volume=1000, amount=100000,
        source_timestamp=NOW - timedelta(seconds=1), fetched_at=NOW,
    )
    values.update(changes)
    return NormalizedQuote(**values)


def bar(day="2026-09-09", **changes):
    return dict(time=day, open=100, high=103, low=99, close=102, volume=100, turnover=10000) | changes


class Quotes(QuoteProvider):
    name = "fixture"

    def __init__(self):
        self.calls = []
        self.values = {code: quote(code) for code in ("600519.SH", "159915.SZ", "000300.SH", "000001.SZ", "000001.SH")}
        self.fail = False

    def get_quotes(self, codes):
        raise AssertionError("facade must use the qualified batch contract")

    def get_instrument_quotes(self, codes, *, instrument_types=None):
        self.calls.append(list(codes))
        if self.fail:
            raise RuntimeError("private provider failure")
        return {code: deepcopy(self.values[code]) for code in codes if code in self.values}


class Adapters(InstrumentDataAdapters):
    def __init__(self):
        self.quotes_fixture = Quotes()
        super().__init__(quote_provider=self.quotes_fixture, history_provider=SimpleNamespace(name="fixture"))
        self.bar_rows = [bar("2026-09-08"), bar()]
        self.bar_calls = []
        self.book_calls = 0
        self.flow_calls = 0
        self.book_failure = False
        self.flow_failure = False
        self.book_data = {
            "bids": [{"price": 99 - i / 100, "volume": 1000} for i in range(5)],
            "asks": [{"price": 100 + i / 100, "volume": 2000} for i in range(5)],
        }
        self.flow_data = {
            "current": {"main_net_inflow": 12, "large_net_inflow": 7, "small_net_inflow": -10},
            "history": [
                {"time": "2026-09-09", "main_net_inflow": 10},
                {"time": "2026-09-08", "main_net_inflow": -5},
            ],
        }

    def bars(self, instrument, *, start, end, adjustment):
        self.bar_calls.append((instrument.code, start, end, adjustment))
        return InstrumentProviderResult(
            data={"bars": deepcopy(self.bar_rows)}, provider="fixture", fetched_at=NOW,
            observed_at=NOW - timedelta(seconds=1),
        )

    def order_book(self, instrument):
        self.book_calls += 1
        if self.book_failure:
            raise RuntimeError("order book private failure")
        return InstrumentProviderResult(
            data=deepcopy(self.book_data), provider="fixture", fetched_at=NOW,
            observed_at=NOW - timedelta(seconds=1), trading_date=NOW.date(),
        )

    def capital_flow(self, instrument):
        self.flow_calls += 1
        if self.flow_failure:
            raise RuntimeError("capital flow private failure")
        return InstrumentProviderResult(
            data=deepcopy(self.flow_data), provider="fixture", fetched_at=NOW,
            observed_at=NOW - timedelta(seconds=1), trading_date=NOW.date(),
        )


@pytest.fixture
def market():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SecurityMaster.__table__.create(engine)
    TradingCalendar.__table__.create(engine)
    clear_market_foundation_snapshot_cache()
    with Session(engine, expire_on_commit=False) as db:
        for code, kind in (
            ("600519.SH", "STOCK"), ("159915.SZ", "ETF"), ("000300.SH", "INDEX"),
            ("000001.SZ", "STOCK"), ("000001.SH", "INDEX"),
        ):
            upsert_security(db, {"code": code, "security_type": kind, "name": "Fixture", "board": "MAIN"})
        for offset in range(20):
            day = date(2026, 9, 1) + timedelta(days=offset)
            db.add(TradingCalendar(trade_date=day, market="CN", is_open=day.weekday() < 5))
        db.commit()
        adapters = Adapters()
        clock = [NOW]
        service = InstrumentMarketService(db, adapters=adapters, now=lambda: clock[0])
        yield SimpleNamespace(db=db, adapters=adapters, service=service, clock=clock)
    clear_market_foundation_snapshot_cache()
    engine.dispose()


@pytest.mark.parametrize("code,kind", [("600519.SH", "STOCK"), ("159915.SZ", "ETF"), ("000300.SH", "INDEX")])
def test_identity_capabilities_and_quote(market, code, kind):
    metadata = market.service.metadata(code)
    assert metadata.identity.code == code
    assert metadata.identity.instrument_type == kind
    assert metadata.identity.symbol == code[:6]
    assert metadata.capabilities.fundamentals == (kind == "STOCK")
    assert metadata.capabilities.etf_profile == (kind == "ETF")
    result = market.service.quote(code)
    assert result.last == 100
    assert result.change == 1
    assert result.change_pct == pytest.approx(100 / 99)
    assert result.observed_at != result.fetched_at
    assert result.data_basis == "live"


@pytest.mark.parametrize("code", ["600519", "600519.SH", "SH600519", "sh600519", "SSE600519"])
def test_canonical_normalization(market, code):
    assert market.service.quote(code).instrument.code == "600519.SH"


def test_same_symbol_exchanges_never_alias(market):
    market.adapters.quotes_fixture.values["000001.SH"].price = 3000
    values = market.service.batch_quotes(["000001.SH", "000001.SZ", "000001"]).items
    assert [(item.code, item.last) for item in values] == [("000001.SH", 3000), ("000001.SZ", 100)]
    assert get_security(market.db, "000001.SH").security_type == "INDEX"


def test_unknown_does_not_create_identity_or_call_provider(market):
    count = len(market.db.execute(select(SecurityMaster)).scalars().all())
    with pytest.raises(InstrumentLookupError, match="INSTRUMENT_NOT_FOUND"):
        market.service.quote("600999.SH")
    with pytest.raises(InstrumentLookupError, match="INVALID_INSTRUMENT_CODE"):
        market.service.quote("secret-600519-wrong")
    assert not market.adapters.quotes_fixture.calls
    assert len(market.db.execute(select(SecurityMaster)).scalars().all()) == count
    with pytest.raises(InstrumentLookupError):
        market.service.quote("600519.SZ")


def test_index_unsupported_does_not_call_providers(market):
    result = market.service.snapshot("000300.SH")
    assert result.order_book.status == result.capital_flow.status == "unsupported"
    assert not result.capabilities.order_book
    assert not result.capabilities.capital_flow
    assert market.adapters.book_calls == market.adapters.flow_calls == 0
    assert result.quality == result.quote.quality != "F"


def test_null_missing_and_nonfinite_json(market):
    market.adapters.quotes_fixture.values["600519.SH"] = quote(
        open=float("nan"), volume=float("inf"), amount=float("-inf"),
        source_timestamp=None, trade_date=None, turnover_rate=None,
    )
    result = market.service.quote("600519")
    assert result.open is result.volume is result.turnover is result.turnover_rate is None
    assert result.observed_at is None
    assert result.fetched_at is not None
    assert "OBSERVED_TIME_MISSING" in result.quality_flags
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_explicit_fallback_downgrades(market):
    market.adapters.quotes_fixture.values["600519.SH"].fallback_level = 1
    result = market.service.quote("600519")
    assert result.fallback is True
    assert result.source == "fixture"
    assert result.quality == "B"
    assert "PROVIDER_FALLBACK" in result.quality_flags


def test_suspended_has_distinct_status(market):
    get_security(market.db, "600519").is_suspended = True
    market.db.commit()
    assert market.service.quote("600519").error_code == "INSTRUMENT_SUSPENDED"
    assert market.service.quote("600519").fetched_at is None
    assert market.service.order_book("600519").status == "unavailable"
    assert not market.adapters.quotes_fixture.calls
    assert market.adapters.book_calls == 0


def test_batch_partial_failure_deduplication_order_and_single_call(market):
    del market.adapters.quotes_fixture.values["159915.SZ"]
    result = market.service.batch_quotes(["000300.SH", "600519", "159915.SZ", "SH600519", "600999.SH"])
    assert [item.code for item in result.items] == ["000300.SH", "600519.SH", "159915.SZ", "600999.SH"]
    assert [item.last for item in result.items] == [100, 100, None, None]
    assert result.items[2].error_code == "QUOTE_PROVIDER_FAILED"
    assert result.items[3].error_code == "INSTRUMENT_NOT_FOUND"
    assert len(market.adapters.quotes_fixture.calls) == 1


def test_twenty_codes_one_batch_call(market):
    codes = [f"601{index:03d}.SH" for index in range(20)]
    for code in codes:
        upsert_security(market.db, {"code": code, "security_type": "STOCK"})
        market.adapters.quotes_fixture.values[code] = quote(code)
    market.db.commit()
    assert len(market.service.batch_quotes(codes).items) == 20
    assert market.adapters.quotes_fixture.calls == [codes]


@pytest.mark.parametrize("codes", [[], ["600519"] * 101])
def test_batch_limit(market, codes):
    with pytest.raises(InstrumentLookupError, match="BATCH_SIZE_INVALID"):
        market.service.batch_quotes(codes)


@pytest.mark.parametrize("interval", ["1d", "1w", "1M"])
@pytest.mark.parametrize("code", ["600519.SH", "159915.SZ", "000300.SH"])
def test_same_bars_api_all_types_and_intervals(market, code, interval):
    market.adapters.bar_rows = [bar("2026-08-03"), bar("2026-09-09"), bar("2026-09-02")]
    result = market.service.bars(code, interval=interval, start=date(2026, 8, 1), limit=2)
    assert result.interval == interval
    assert len(result.bars) == 2
    assert [row.time for row in result.bars] == sorted(row.time for row in result.bars)
    assert result.mixed_sources is False


def test_daily_aggregation_ohlcv(market):
    market.adapters.bar_rows = [
        bar("2026-09-07", open=100, high=105, low=99, close=101, volume=20),
        bar("2026-09-08", open=101, high=104, low=98, close=102, volume=30),
    ]
    weekly = market.service.bars("600519", interval="1w").bars[0]
    assert (weekly.open, weekly.high, weekly.low, weekly.close, weekly.volume) == (100, 105, 98, 102, 50)
    assert weekly.turnover == 20000


@pytest.mark.parametrize("adjustment", ["none", "forward", "backward"])
def test_bars_adjustment_is_forwarded_and_cache_isolated(market, adjustment):
    result = market.service.bars("600519", adjustment=adjustment)
    assert result.adjustment == adjustment
    assert market.adapters.bar_calls[-1][-1] == adjustment


def test_bars_adjustment_and_instrument_cache_keys(market):
    for code in ("600519", "159915"):
        for adjustment in ("none", "forward", "backward"):
            market.service.bars(code, adjustment=adjustment)
            market.service.bars(code, adjustment=adjustment)
    assert len(market.adapters.bar_calls) == 6


def test_index_adjustment_unsupported_is_explicit(market):
    result = market.service.bars("000300.SH", adjustment="forward")
    assert result.status == "unsupported"
    assert result.error_code == "ADJUSTMENT_UNSUPPORTED"
    assert not market.adapters.bar_calls


def test_bars_start_end_limit_and_invalid_ranges(market):
    market.adapters.bar_rows = [bar("2026-09-07"), bar("2026-09-08"), bar("2026-09-09")]
    result = market.service.bars("600519", start=date(2026, 9, 7), end=date(2026, 9, 8), limit=1)
    assert [row.time for row in result.bars] == [date(2026, 9, 8)]
    for params in ({"limit": 1001}, {"limit": 0}, {"interval": "5m"}, {"adjustment": "QFQ"}, {"start": date(2026, 9, 10), "end": date(2026, 9, 9)}):
        with pytest.raises(InstrumentLookupError):
            market.service.bars("600519", **params)


@pytest.mark.parametrize("changes,flag", [
    ({"high": 99}, "INVALID_BAR_OHLC"), ({"low": 104}, "INVALID_BAR_OHLC"),
    ({"close": float("nan")}, "INVALID_BAR_OHLC"), ({"high": float("inf")}, "INVALID_BAR_OHLC"),
    ({"volume": -1}, "NEGATIVE_BAR_VOLUME_OR_TURNOVER"),
    ({"turnover": -1}, "NEGATIVE_BAR_VOLUME_OR_TURNOVER"),
    ({"time": "nonsense"}, "INVALID_BAR_TIME"),
])
def test_malformed_bars_are_excluded_and_quality_degraded(market, changes, flag):
    market.adapters.bar_rows = [bar("2026-09-08"), bar() | changes]
    result = market.service.bars("600519")
    assert len(result.bars) == 1
    assert result.quality == "C"
    assert flag in result.quality_flags
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_duplicate_bar_times_not_arbitrarily_selected(market):
    market.adapters.bar_rows = [bar("2026-09-08"), bar(), bar(close=101)]
    result = market.service.bars("600519")
    assert [row.time for row in result.bars] == [date(2026, 9, 8)]
    assert "DUPLICATE_BAR_TIME" in result.quality_flags


def test_order_book_sorting_five_levels_and_derived_fields(market):
    market.adapters.book_data["bids"].reverse()
    market.adapters.book_data["asks"].reverse()
    result = market.service.order_book("600519")
    assert [level.level for level in result.bids] == [1, 2, 3, 4, 5]
    assert [level.level for level in result.asks] == [1, 2, 3, 4, 5]
    assert result.bids[0].price > result.bids[4].price
    assert result.asks[0].price < result.asks[4].price
    assert result.bid_volume_total == 5000
    assert result.ask_volume_total == 10000
    assert result.order_difference == -5000
    assert result.order_ratio == pytest.approx(-100 / 3)
    assert result.derived and result.derived_fields
    assert result.inner_volume is result.outer_volume is None


def test_crossed_book_only_downgraded_in_continuous_trading(market):
    market.adapters.book_data["bids"][0]["price"] = 101
    result = market.service.order_book("600519")
    assert result.quality == "C"
    assert "CROSSED_ORDER_BOOK" in result.quality_flags


def test_empty_vs_unavailable_book(market):
    market.adapters.book_data = {}
    assert market.service.order_book("600519").status == "empty"
    clear_market_foundation_snapshot_cache()
    market.adapters.book_failure = True
    assert market.service.order_book("600519").status == "unavailable"


def test_capital_flow_signs_methodology_ordering(market):
    result = market.service.capital_flow("600519")
    assert result.current.main_net_inflow == 12
    assert result.current.small_net_inflow == -10
    assert result.provider_derived is True
    assert result.methodology == "provider_defined"
    assert result.quality == "B"
    assert [row.time for row in result.history] == [date(2026, 9, 8), date(2026, 9, 9)]


def test_etf_capital_flow_capability_and_provider_failure(market):
    assert market.service.capital_flow("159915").status == "unsupported"
    market.adapters.etf_capital_flow = True
    assert market.service.metadata("159915").capabilities.capital_flow is True
    assert market.service.capital_flow("159915").current.main_net_inflow == 12
    market.adapters.flow_failure = True
    assert market.service.capital_flow("600519").status == "unavailable"


def test_snapshot_uses_worst_supported_grade_and_preserves_partial_data(market):
    market.adapters.book_failure = True
    result = market.service.snapshot("600519")
    assert [result.quote.quality, result.capital_flow.quality, result.order_book.quality] == ["A", "B", "F"]
    assert result.quality == result.data_quality.quality == "F"
    assert result.quote.last == 100
    assert result.capital_flow.current.main_net_inflow == 12
    assert "latest_analysis" not in result.model_dump()


@pytest.mark.parametrize("module,payload", [
    ("order_book", {"bids": None}),
    ("order_book", {"bids": "invalid"}),
    ("capital_flow", {"current": "invalid"}),
    ("capital_flow", {"history": None}),
])
def test_malformed_module_payload_does_not_break_snapshot(market, module, payload):
    if module == "order_book":
        market.adapters.book_data = payload
    else:
        market.adapters.flow_data = payload
    snapshot = market.service.snapshot("600519")
    assert snapshot.quote.last == 100
    assert getattr(snapshot, module).status == "unavailable"
    assert snapshot.quality == "F"


def test_malformed_rows_are_excluded_without_losing_valid_module_data(market):
    market.adapters.book_data["bids"].append(None)
    market.adapters.flow_data["history"].append("invalid")
    snapshot = market.service.snapshot("600519")
    assert len(snapshot.order_book.bids) == 5
    assert len(snapshot.capital_flow.history) == 2
    assert snapshot.quality == "C"
    assert "INVALID_ORDER_BOOK_LEVEL" in snapshot.order_book.quality_flags
    assert "INVALID_CAPITAL_FLOW_ROW" in snapshot.capital_flow.quality_flags


def test_malformed_bars_container_returns_unavailable(market):
    market.adapters.bar_rows = None
    result = market.service.bars("600519")
    assert result.status == "unavailable"
    assert result.error_code == "INVALID_BARS_PAYLOAD"


@pytest.mark.parametrize("current,observed,basis,stale", [
    (NOW, NOW - timedelta(minutes=10), "live", True),
    (datetime(2026, 9, 10, 20, tzinfo=TZ), datetime(2026, 9, 10, 15, tzinfo=TZ), "session_close", False),
    (datetime(2026, 9, 12, 20, tzinfo=TZ), datetime(2026, 9, 11, 15, tzinfo=TZ), "previous_session_close", False),
    (datetime(2026, 9, 14, 10, tzinfo=TZ), datetime(2026, 9, 11, 15, tzinfo=TZ), "previous_session_close", True),
    (datetime(2026, 9, 10, 20, tzinfo=TZ), datetime(2026, 9, 10, 11, tzinfo=TZ), "live", True),
    (datetime(2026, 9, 10, 12, tzinfo=TZ), datetime(2026, 9, 10, 11, 30, tzinfo=TZ), "live", False),
])
def test_session_aware_stale(market, current, observed, basis, stale):
    market.clock[0] = current
    market.adapters.quotes_fixture.values["600519.SH"] = quote(source_timestamp=observed, fetched_at=current)
    result = market.service.quote("600519")
    assert result.data_basis == basis
    assert (result.status == "stale") == stale
    assert result.observed_at != result.fetched_at


def test_cache_hit_force_refresh_instrument_isolation_and_failure_ttl(market, monkeypatch):
    from app.services import market_snapshot_service

    clock = [100.0]
    monkeypatch.setattr(market_snapshot_service.time, "monotonic", lambda: clock[0])
    first = market.service.quote("600519")
    first.last = -1
    assert market.service.quote("600519").last == 100
    market.service.quote("159915")
    assert len(market.adapters.quotes_fixture.calls) == 2
    market.service.quote("600519", force_refresh=True)
    assert len(market.adapters.quotes_fixture.calls) == 3
    market.adapters.quotes_fixture.fail = True
    assert market.service.quote("000300.SH").status == "unavailable"
    market.service.quote("000300.SH")
    assert len(market.adapters.quotes_fixture.calls) == 4
    clock[0] += 1.1
    market.adapters.quotes_fixture.fail = False
    assert market.service.quote("000300.SH").last == 100
    assert len(market.adapters.quotes_fixture.calls) == 5


def test_evidence_is_json_frozen_and_batches_quotes(market):
    evidence = market.service.evidence_snapshot(["600519", "159915", "000300.SH"])
    serialized = evidence.model_dump_json()
    assert len(market.adapters.quotes_fixture.calls) == 1
    market.adapters.quotes_fixture.values["600519.SH"].price = 1000
    market.service.batch_quotes(["600519"], force_refresh=True)
    assert evidence.model_dump_json() == serialized
    assert json.loads(serialized)["schema_version"] == "v3-market-2.evidence.v1"


def test_secret_sentinel_not_in_public_quote_metadata_snapshot_or_evidence(market):
    secret = "SENTINEL_NEVER_EXPOSE_7z9"
    q = market.adapters.quotes_fixture.values["600519.SH"]
    q.errors = [f"Authorization: {secret}"]
    q.raw_reference = f"https://user:{secret}@provider.example"
    q.metadata.update(api_key=secret, cookie=secret, credential=secret, raw={"secret": secret})
    get_security(market.db, "600519").raw_metadata_json = {
        "api_key": secret, "raw": {"secret": secret},
        "industry": f"https://user:{secret}@provider.example",
    }
    market.db.commit()
    for result in (
        market.service.metadata("600519"), market.service.snapshot("600519"),
        market.service.evidence_snapshot(["600519"]),
    ):
        payload = result.model_dump_json()
        assert secret not in payload
        assert "Authorization" not in payload
        assert "api_key" not in payload


def test_authenticated_api_contract_and_validation(market):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers.instruments_v3 import instrument_service
    from app.v2_dependencies import get_current_user

    paths = ("", "/quote", "/bars", "/order-book", "/capital-flow", "/snapshot")
    client = TestClient(app)
    for path in paths:
        assert client.get("/api/v3/market/instruments/600519.SH" + path).status_code == 401
    assert client.post("/api/v3/market/instruments/quotes", json={"codes": ["600519"]}).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[instrument_service] = lambda: market.service
    try:
        for path in paths:
            assert client.get("/api/v3/market/instruments/600519.SH" + path).status_code == 200
        assert client.get("/api/v3/market/instruments/600999.SH").status_code == 404
        assert client.get("/api/v3/market/instruments/invalid/quote").status_code == 422
        assert client.get("/api/v3/market/instruments/600519/bars?limit=1001").status_code == 422
        assert client.get("/api/v3/market/instruments/600519/bars?interval=5m").status_code == 422
        assert client.get("/api/v3/market/instruments/600519/bars?adjustment=qfq").status_code == 422
        assert client.post("/api/v3/market/instruments/quotes", json={"codes": ["600519"] * 101}).status_code == 422
        response = client.get("/api/v3/market/instruments/000300.SH/order-book")
        assert response.status_code == 200 and response.json()["status"] == "unsupported"
        result = client.post("/api/v3/market/instruments/quotes", json={"codes": ["600519", "600999"]})
        assert result.status_code == 200
        assert result.json()["items"][1]["error_code"] == "INSTRUMENT_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()
