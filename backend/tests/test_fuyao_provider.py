"""Offline canonicalization and provider-chain tests for Fuyao."""
from __future__ import annotations

from datetime import date

from app.market.models import DataQualityStatus
from app.market.providers import (
    FuyaoCalendarProvider,
    FuyaoClient,
    FuyaoKLineProvider,
    FuyaoQuoteProvider,
    FuyaoSecurityProvider,
    FuyaoResponse,
    FallbackQuoteProvider,
    InMemoryQuoteProvider,
    create_quote_provider,
)


class StubClient(FuyaoClient):
    def __init__(self, responses):
        super().__init__(api_key="test", min_interval_seconds=0)
        self.responses = responses
        self.calls = []

    def get(self, path, *, params=None, capability=None):
        self.calls.append((path, dict(params or {}), capability))
        value = self.responses[path]
        if callable(value):
            value = value(self.calls[-1])
        return value


def response(path, data, *, request_id="request-1"):
    return FuyaoResponse(code=0, message="success", request_id=request_id, data=data, endpoint=path, latency_ms=2.5, attempts=1)


def test_quote_provider_normalizes_sh_sz_bj_etf_timestamp_and_missing():
    data = {
        "timestamp": 1784275991000,
        "total": 5,
        "item": [
            {"thscode": "600519.SH", "ticker": "600519", "last_price": 1600, "prev_price": 1590, "price_change_ratio_pct": 0.63, "open_price": 1595, "high_price": 1610, "low_price": 1588, "volume": 123, "turnover": 999},
            {"thscode": "000001.SZ", "ticker": "000001", "last_price": 11.2, "prev_price": 11.0, "price_change_ratio_pct": 1.81, "volume": 10, "turnover": 20},
            {"thscode": "920001.BJ", "ticker": "920001", "last_price": 8.1, "prev_price": 8.0, "price_change_ratio_pct": 1.25, "volume": 2, "turnover": 3},
            {"thscode": "159915.SZ", "ticker": "159915", "last_price": 2.5, "prev_price": 2.49, "price_change_ratio_pct": 0.4, "volume": 100, "turnover": 250},
            {"thscode": "601318.SH", "ticker": "601318", "last_price": None, "prev_price": 40.0},
        ],
    }
    client = StubClient({"/api/a-share/prices/snapshot": response("/api/a-share/prices/snapshot", data)})
    provider = FuyaoQuoteProvider(client=client, batch_size=10)
    result = provider.get_quotes(["600519", "000001", "920001", "159915", "601318"])

    assert result["600519"].price == 1600
    assert result["600519"].exchange == "SSE"
    assert result["000001"].exchange == "SZSE"
    assert result["920001"].exchange == "BSE"
    assert result["159915"].exchange == "SZSE"
    assert result["600519"].source_timestamp is not None
    assert result["600519"].trade_date == date(2026, 7, 17)
    assert result["601318"].quality_status == DataQualityStatus.MISSING
    assert client.calls[0][1]["thscodes"] == "600519.SH,000001.SZ,920001.BJ,159915.SZ,601318.SH"


def test_all_a_quote_provider_uses_official_limit_offset_pagination():
    pages = {
        0: response("/api/a-share/prices/snapshot", {"timestamp": 1784275991000, "total": 3, "item": [{"thscode": "000001.SZ", "last_price": 11}, {"thscode": "600519.SH", "last_price": 1600}]}),
        2: response("/api/a-share/prices/snapshot", {"timestamp": 1784275991000, "total": 3, "item": [{"thscode": "159915.SZ", "last_price": 2.5}]}),
    }
    client = StubClient({"/api/a-share/prices/snapshot": lambda call: pages[call[1]["offset"]]})
    provider = FuyaoQuoteProvider(client=client, page_size=2, full_market_threshold=1)
    result = provider.get_all_a_share_quotes(["000001", "600519", "159915"])

    assert set(result) == {"000001", "600519", "159915"}
    assert [call[1]["offset"] for call in client.calls] == [0, 2]
    assert all("thscodes" not in call[1] for call in client.calls)


def test_all_a_fallback_chain_preserves_primary_pagination_capability():
    pages = {
        0: response(
            "/api/a-share/prices/snapshot",
            {
                "timestamp": 1784275991000,
                "total": 3,
                "item": [
                    {"thscode": "000001.SZ", "last_price": 11},
                    {"thscode": "600519.SH", "last_price": 1600},
                ],
            },
        ),
        2: response(
            "/api/a-share/prices/snapshot",
            {
                "timestamp": 1784275991000,
                "total": 3,
                "item": [{"thscode": "159915.SZ", "last_price": 2.5}],
            },
        ),
    }
    client = StubClient({"/api/a-share/prices/snapshot": lambda call: pages[call[1]["offset"]]})
    primary = FuyaoQuoteProvider(client=client, page_size=2, full_market_threshold=1)
    fallback = FallbackQuoteProvider([primary, InMemoryQuoteProvider({})])

    result = fallback.get_all_a_share_quotes(["000001", "600519", "159915"])

    assert set(result) == {"000001", "600519", "159915"}
    assert [call[1]["offset"] for call in client.calls] == [0, 2]
    assert all("thscodes" not in call[1] for call in client.calls)


def test_security_provider_maps_only_official_asset_fields_and_paginates():
    pages = {
        0: response("/api/meta/tickers/list", {"item": [{"thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台", "exchange": "SH", "asset_type": "a-share", "currency": "CNY"}, {"thscode": "159915.SZ", "ticker": "159915", "name": "创业板ETF", "exchange": "SZ", "asset_type": "fund-etf", "currency": "CNY"}]}),
        2: response("/api/meta/tickers/list", {"item": []}),
    }
    client = StubClient({"/api/meta/tickers/list": lambda call: pages[call[1]["offset"]]})
    provider = FuyaoSecurityProvider(client=client, page_size=2)
    rows = provider.list_securities()

    assert [(row["code"], row["exchange"], row["security_type"]) for row in rows] == [("600519", "SSE", "STOCK"), ("159915", "SZSE", "ETF")]
    assert "status" not in rows[0]
    assert [call[1]["offset"] for call in client.calls] == [0, 2]


def test_calendar_maps_returned_open_days_and_links_neighbors():
    client = StubClient({"/api/a-share/calendar/trading-days": response("/api/a-share/calendar/trading-days", {"item": [{"date_ms": 1784160000000, "date": "20260716"}, {"date_ms": 1784246400000, "date": "20260717"}]} )})
    rows = FuyaoCalendarProvider(client=client).get_calendar(date(2026, 7, 16), date(2026, 7, 19))
    by_date = {row["trade_date"]: row for row in rows}
    assert by_date[date(2026, 7, 16)]["is_open"] is True
    assert by_date[date(2026, 7, 18)]["is_open"] is False
    assert by_date[date(2026, 7, 18)]["previous_trade_date"] == date(2026, 7, 17)


def test_historical_kline_preserves_available_at_source_timestamp_and_filters_range():
    client = StubClient({"/api/a-share/prices/historical": response("/api/a-share/prices/historical", {"timestamp": 1784275991000, "item": [{"date_ms": 1784160000000, "open_price": 1, "high_price": 2, "low_price": 0.5, "close_price": 1.5, "volume": 10, "turnover": 20}, {"date_ms": 1784246400000, "open_price": 1.5, "high_price": 2.5, "low_price": 1.2, "close_price": 2, "volume": 11, "turnover": 22}]} )})
    provider = FuyaoKLineProvider(client=client, now=lambda: __import__("datetime").datetime(2026, 7, 18, tzinfo=__import__("datetime").timezone.utc))
    rows = provider.get_historical("600519", start=date(2026, 7, 16), end=date(2026, 7, 17), adjustment="QFQ")

    assert len(rows) == 2
    assert rows[0]["provider"] == "fuyao_historical"
    assert rows[0]["adjustment"] == "QFQ"
    assert rows[0]["prev_close"] is None
    assert rows[1]["prev_close"] == 1.5
    assert rows[0]["available_at"] is not None
    assert rows[0]["metadata"]["source_timestamp_ms"] == 1784275991000


def test_fallback_uses_existing_provider_when_fuyao_returns_missing():
    client = StubClient({"/api/a-share/prices/snapshot": response("/api/a-share/prices/snapshot", {"timestamp": 1784275991000, "item": []})})
    primary = FuyaoQuoteProvider(client=client)
    secondary = InMemoryQuoteProvider({"600519": {"code": "600519", "price": 1600, "prev_close": 1590, "provider": "fixture"}})
    result = FallbackQuoteProvider([primary, secondary]).get_quotes(["600519"])
    assert result["600519"].provider == "inmemory"
    assert result["600519"].fallback_level == 1


def test_acceptance_factory_never_constructs_a_production_quote_provider(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", True)
    provider = create_quote_provider("fuyao")

    assert provider.name == "acceptance"
    quote = provider.get_quotes(["600519"])["600519"]
    assert quote.metadata["deterministic"] is True
