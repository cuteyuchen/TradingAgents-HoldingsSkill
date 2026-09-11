"""Wire adapters, explicit fallback and immutable CORE-3 integration."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from test_instrument_market import Adapters, NOW, TZ, bar, market, quote
from app.market.instruments import InstrumentMarketService
from app.market.providers.base import QuoteProvider
from app.market.providers.eastmoney import EastmoneyBatchQuoteProvider, EASTMONEY_INSTRUMENT_QUOTE_URL
from app.market.providers.fallback import FallbackQuoteProvider
from app.market.providers.fuyao import FuyaoKLineProvider, FuyaoQuoteProvider, FallbackKLineProvider
from app.market.providers.health import ProviderHealthRegistry, reset_runtime_provider_health_registry
from app.market.providers.instruments import InstrumentDataAdapters
from app.market.providers.tencent import TencentQuoteProvider, parse_tencent_line
from app.services.security_master import upsert_security


def tencent_line(code="600519", exchange="sh", *, time=None, price="100"):
    fields = [""] * 49
    fields[1:9] = ["Fixture", code, price, "99", "99", "200", "120", "80"]
    fields[30] = time or NOW.strftime("%Y%m%d%H%M%S")
    fields[32:35] = ["1.01", "101", "98"]
    fields[36:38] = ["200", "300"]
    for index in range(5):
        fields[9 + index * 2:11 + index * 2] = [str(99 - index * 0.01), str(index + 1)]
        fields[19 + index * 2:21 + index * 2] = [str(100 + index * 0.01), str(index + 2)]
    return f'v_{exchange}{code}="' + "~".join(fields) + '";'


class TencentHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        return SimpleNamespace(content=self.payload.encode(), raise_for_status=lambda: None)


class FuyaoClient:
    def __init__(self, *, quote_rows=None, bar_rows=None):
        self.calls = []
        self.quote_rows = quote_rows or []
        self.bar_rows = bar_rows or []

    def get(self, endpoint, *, params=None, capability=None):
        self.calls.append((endpoint, params))
        return SimpleNamespace(
            data={"timestamp": int(NOW.timestamp() * 1000), "item": self.bar_rows if "historical" in endpoint else self.quote_rows},
            endpoint=endpoint, request_id="fixture-request", latency_ms=1, attempts=1,
        )


def test_tencent_mixed_twenty_instruments_one_http_call(market):
    codes = [("600519", "sh", "STOCK"), ("159915", "sz", "ETF"), ("000300", "sh", "INDEX")]
    codes += [(f"601{i:03d}", "sh", "STOCK") for i in range(17)]
    for code, exchange, kind in codes:
        upsert_security(market.db, {"code": f"{code}.{exchange}", "security_type": kind})
    market.db.commit()
    http = TencentHTTP("\n".join(tencent_line(code, exchange) for code, exchange, _ in codes))
    adapters = InstrumentDataAdapters(quote_provider=TencentQuoteProvider(request=http))
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW)
    result = service.batch_quotes([f"{code}.{exchange}" for code, exchange, _ in codes])
    assert len(result.items) == 20 and all(item.last == 100 for item in result.items)
    assert len(http.calls) == 1
    assert "sh000300" in http.calls[0] and "sz159915" in http.calls[0]
    assert result.items[0].volume == 20000
    assert result.items[0].turnover == 3000000
    assert result.items[2].volume is result.items[2].turnover is None


def test_tencent_same_symbol_different_exchange_and_real_book_units(market):
    http = TencentHTTP(tencent_line("000001", "sh") + tencent_line("000001", "sz") + tencent_line())
    provider = TencentQuoteProvider(request=http)
    adapters = InstrumentDataAdapters(quote_provider=provider, order_book_provider=provider)
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW)
    result = service.batch_quotes(["000001.SH", "000001.SZ"])
    assert [item.code for item in result.items] == ["000001.SH", "000001.SZ"]
    book = service.order_book("600519")
    assert book.bids[0].volume == 100
    assert book.asks[0].volume == 200
    assert book.inner_volume == 8000
    assert book.outer_volume == 12000
    assert book.volume_unit == "shares"


def test_time_only_provider_does_not_fabricate_observed_date(market):
    http = TencentHTTP(tencent_line(time="15:00:00"))
    adapters = InstrumentDataAdapters(quote_provider=TencentQuoteProvider(request=http))
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW)
    result = service.quote("600519")
    assert result.observed_at is None
    assert result.trading_date is None
    assert "OBSERVED_TIME_MISSING" in result.quality_flags
    assert result.last == 100


def test_time_only_canonical_parser_preserves_price_validation():
    legacy = parse_tencent_line(tencent_line(time="15:00:00"), fetched_at=NOW)
    assert legacy.source_timestamp.astimezone(TZ).date() == NOW.date()
    assert "future_source_timestamp" in legacy.errors
    canonical = parse_tencent_line(
        tencent_line(time="15:00:00", price="-1"), fetched_at=NOW, infer_observed_date=False,
    )
    assert canonical.source_timestamp is canonical.trade_date is None
    assert canonical.quality_status.value == "INVALID"
    assert canonical.errors == ["negative_price"]


def test_fuyao_native_batch_and_index_endpoint_keep_identity(market):
    client = FuyaoClient(quote_rows=[
        {
            "thscode": code, "last_price": 100, "prev_price": 99,
            "open_price": 99, "high_price": 101, "low_price": 98, "volume": 123, "turnover": 12300,
        }
        for code in ("600519.SH", "159915.SZ", "000300.SH")
    ])
    adapters = InstrumentDataAdapters(quote_provider=FuyaoQuoteProvider(client=client))
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW)
    items = service.batch_quotes(["600519", "159915", "000300.SH"]).items
    assert all(item.last == 100 for item in items)
    assert items[0].volume == 123
    assert items[2].instrument.exchange == "SSE"
    assert client.calls == [
        ("/api/a-share/prices/snapshot", {"thscodes": "600519.SH,159915.SZ"}),
        ("/api/a-share-index/prices/snapshot", {"thscodes": "000300.SH"}),
    ]
    client.calls.clear()
    twenty = [f"601{i:03d}.SH" for i in range(20)]
    FuyaoQuoteProvider(client=client).get_instrument_quotes(twenty)
    assert len(client.calls) == 1


def test_eastmoney_uses_one_targeted_batch_not_full_market_scan(market):
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs["params"]))
        return {"data": {"diff": [
            {"f12": "600519", "f13": 1, "f2": 100, "f18": 99, "f17": 99, "f15": 101, "f16": 98, "f5": 10, "f6": 1000, "f124": int(NOW.timestamp())},
            {"f12": "000300", "f13": 1, "f2": 3000, "f18": 2990, "f17": 2990, "f15": 3010, "f16": 2980, "f124": int(NOW.timestamp())},
        ]}}

    adapters = InstrumentDataAdapters(quote_provider=EastmoneyBatchQuoteProvider(transport=request))
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW)
    result = service.batch_quotes(["600519", "000300.SH"])
    assert len(calls) == 1 and calls[0][0] == EASTMONEY_INSTRUMENT_QUOTE_URL
    assert calls[0][1]["secids"] == "1.600519,1.000300"
    assert result.items[0].volume == 1000
    assert result.items[1].last == 3000


def test_existing_fallback_chain_preserves_off_session_close(market):
    class Failure(QuoteProvider):
        name = "fuyao"

        def get_quotes(self, codes):
            raise RuntimeError("Authorization: PRIVATE_SENTINEL")

    http = TencentHTTP(tencent_line(time="20260910150000"))
    provider = FallbackQuoteProvider(
        [Failure(), TencentQuoteProvider(request=http)], health=ProviderHealthRegistry(),
    )
    adapters = InstrumentDataAdapters(quote_provider=provider)
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW.replace(hour=20))
    result = service.quote("600519")
    assert result.source == "tencent" and result.fallback is True
    assert result.quality == "B" and result.data_basis == "session_close"
    assert "PROVIDER_FALLBACK" in result.quality_flags
    assert "PRIVATE_SENTINEL" not in result.model_dump_json()
    assert len(http.calls) == 1


@pytest.mark.parametrize("code,kind,endpoint,adjustment", [
    ("600519.SH", "STOCK", "/api/a-share/prices/historical", "backward"),
    ("159915.SZ", "ETF", "/api/fund/market/historical", "none"),
    ("000300.SH", "INDEX", "/api/a-share-index/prices/historical", "none"),
])
def test_fuyao_history_uses_typed_endpoint_and_adjustment(market, code, kind, endpoint, adjustment):
    client = FuyaoClient(bar_rows=[{
        "date_ms": int((NOW - timedelta(days=1)).timestamp() * 1000),
        "open_price": 100, "high_price": 103, "low_price": 99, "close_price": 102,
        "volume": 20, "turnover": 2000,
    }])
    provider = FuyaoKLineProvider(client=client, now=lambda: NOW)
    adapters = InstrumentDataAdapters(quote_provider=market.adapters.quotes_fixture, history_provider=provider)
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW)
    result = service.bars(code, adjustment=adjustment)
    assert result.bars[0].close == 102
    assert client.calls[0][0] == endpoint
    assert client.calls[0][1]["thscode"] == code
    if kind == "STOCK":
        assert client.calls[0][1]["adjust"] == adjustment
    else:
        assert "adjust" not in client.calls[0][1]
    if kind == "INDEX":
        assert result.bars[0].volume is result.bars[0].turnover is None


def test_history_fallback_single_source_and_adjustment_mismatch(market):
    class History:
        name = "eastmoney_daily_qfq"
        supported_adjustments = ("none", "forward", "backward")
        wrong_adjustment = False
        mixed = False

        def get_historical(self, code, *, adjustment, **kwargs):
            return [{
                "trade_date": date(2026, 9, 9), "open": 100, "high": 103, "low": 99, "close": 102,
                "volume": 3, "amount": 3000, "fetched_at": NOW,
                "metadata": {"volume_unit": "lots"},
                "adjustment": "none" if self.wrong_adjustment else adjustment,
                "provider": "fuyao" if self.mixed else self.name,
            }]

    secondary = History()
    primary = FuyaoKLineProvider(client=FuyaoClient())
    adapters = InstrumentDataAdapters(
        quote_provider=market.adapters.quotes_fixture,
        history_provider=FallbackKLineProvider([primary, secondary]),
    )
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW)
    result = service.bars("600519", adjustment="backward")
    assert result.source == "eastmoney_daily_qfq" and result.fallback is True
    assert result.bars[0].volume == 300
    assert result.quality == "B" and result.mixed_sources is False
    secondary.wrong_adjustment = True
    assert service.bars("600519", adjustment="forward").status == "unavailable"
    secondary.wrong_adjustment = False
    secondary.mixed = True
    assert service.bars("159915", adjustment="forward").status == "unavailable"


@pytest.mark.parametrize("failures,expected", [
    ([False, False], "empty"), ([True, False], "unavailable"),
    ([False, True], "unavailable"), ([True, True], "unavailable"),
])
def test_history_empty_only_when_no_provider_failed(market, failures, expected):
    class History:
        name = "fixture"

        def __init__(self, fails):
            self.fails = fails

        def get_historical(self, *args, **kwargs):
            if self.fails:
                raise RuntimeError("provider failure")
            return []

    adapters = InstrumentDataAdapters(
        quote_provider=market.adapters.quotes_fixture,
        history_provider=FallbackKLineProvider([History(fails) for fails in failures]),
    )
    identity = market.service.metadata("600519").identity
    result = adapters.bars(identity, start=NOW.date(), end=NOW.date(), adjustment="none")
    assert result.status == expected


def test_legacy_history_sends_requested_adjustment_and_qualified_index(monkeypatch):
    from app.services import market_data
    from app.market.engine.history import LegacyMarketDataHistoryProvider

    requests = []

    def request(url, *, params):
        requests.append(params)
        return SimpleNamespace(json=lambda: {"data": {"klines": ["2026-09-09,100,102,103,99,10,102000"]}})

    monkeypatch.setattr(market_data, "_em_get", request)
    rows = LegacyMarketDataHistoryProvider().get_historical(
        "000300.SH", start=date(2026, 9, 1), end=date(2026, 9, 9), adjustment="none", instrument_type="INDEX",
    )
    assert requests[0]["secid"] == "1.000300"
    assert requests[0]["fqt"] == "0"
    assert requests[0]["beg"] == "20260901" and requests[0]["end"] == "20260909"
    assert rows[0]["amount"] == 102000


def test_capital_flow_wire_signs_and_no_invented_observation(market):
    def flow(code, **kwargs):
        assert code == "600519.SH"
        assert kwargs == {"limit": 20, "daily": True}
        return {
            "date": "2026-09-10", "main_net": 12, "small_net": -10,
            "history": [{"date": "2026-09-09", "main_net": -5}],
            "api_key": "PRIVATE_SENTINEL",
        }

    adapters = InstrumentDataAdapters(quote_provider=market.adapters.quotes_fixture, capital_flow_fetcher=flow)
    service = InstrumentMarketService(market.db, adapters=adapters, now=lambda: NOW)
    result = service.capital_flow("600519")
    assert result.current.main_net_inflow == 12 and result.current.small_net_inflow == -10
    assert result.history[0].main_net_inflow == -5
    assert result.observed_at is None and result.fetched_at is not None
    assert result.methodology == "provider_defined"
    assert "PRIVATE_SENTINEL" not in result.model_dump_json()


def test_malformed_quote_metadata_isolated_to_single_item(market):
    market.adapters.quotes_fixture.values["600519.SH"].metadata = {"turnover_unit": []}
    result = market.service.batch_quotes(["600519", "159915"])
    assert result.items[0].status == "unavailable"
    assert result.items[1].last == 100


def test_final_refresh_bypasses_cache_without_mutating_frozen_market(market):
    from app.services import analysis_engine, instrument_market_evidence

    assert analysis_engine.refresh_snapshot_quotes is instrument_market_evidence.refresh_snapshot_quotes
    evidence = market.service.evidence_snapshot(["600519"]).model_dump(mode="json")
    initial = {"instrument_market": evidence, "quotes": {"600519": {"price": 100}}}
    original = deepcopy(initial)
    market.adapters.quotes_fixture.values["600519.SH"].price = 101
    refreshed = instrument_market_evidence.refresh_snapshot_quotes(initial, ["600519"], service=market.service)
    assert refreshed["final_quote_refresh_status"] == "ok"
    assert refreshed["quotes"]["600519"]["price"] == 101
    assert initial == original
    assert refreshed["instrument_market"] == evidence
    assert len(market.adapters.quotes_fixture.calls) == 2
    market.adapters.quotes_fixture.fail = True
    failed = instrument_market_evidence.refresh_snapshot_quotes(initial, ["600519"], service=market.service)
    assert failed["final_quote_refresh_status"] == "failed"
    assert failed["quotes"]["600519"]["price"] is None


def test_core3_retries_reuse_same_facade_evidence(market):
    from app.analysis_workflow.evidence import freeze_evidence
    from app.analysis_workflow.resume import hash_input

    class Audit:
        run_id = 1

        def __init__(self):
            self.content = None
            self.run = SimpleNamespace(structured_result_json={})
            self.db = SimpleNamespace(query=lambda *args: self)

        def filter_by(self, **kwargs):
            return self

        def order_by(self, *args):
            return self

        def first(self):
            return self.artifact

        def load_artifact_content(self, key):
            return deepcopy(self.content)

        def record_artifact(self, kind, content, *, artifact_key):
            self.content = deepcopy(content)
            self.artifact = SimpleNamespace(id=1, sha256=hash_input(content))
            return self.artifact

        def bind_input_hash(self, *args):
            pass

        def _run(self):
            return self.run

        def _commit(self):
            pass

    input_payload = {
        "snapshot": {"id": 1},
        "market": {"captured_at": NOW.isoformat(), "instrument_market": market.service.evidence_snapshot(["600519"]).model_dump(mode="json")},
    }
    audit = Audit()
    frozen = freeze_evidence(audit, input_payload, {"profiles": []})
    input_payload["market"]["instrument_market"]["items"][0]["snapshot"]["quote"]["last"] = 999
    resumed = freeze_evidence(audit, input_payload, {"profiles": []})
    assert resumed.evidence_hash == frozen.evidence_hash
    assert resumed.input()["market"]["instrument_market"]["items"][0]["snapshot"]["quote"]["last"] == 100
    assert len(market.adapters.quotes_fixture.calls) == 1


def test_collector_consumes_facade_and_keeps_index_separate(market, monkeypatch):
    from app.config import settings
    from app.services.instrument_market_evidence import collect_market_snapshot

    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", True)
    result = collect_market_snapshot(["600519", "000001.SZ"], service=market.service)
    assert result["quotes"]["000001"]["canonical_code"] == "000001.SZ"
    assert result["indices"]["sh000001"]["canonical_code"] == "000001.SH"
    assert result["instrument_market"]["schema_version"] == "v3-market-2.evidence.v1"
    assert result["quality_grade"] == "A"
    assert len(market.adapters.quotes_fixture.calls) == 1


@pytest.mark.parametrize("volume,expected", [(200, 2), (None, None), (0, 0)])
def test_collector_preserves_volume_ratio_without_inventing_missing_volume(market, monkeypatch, volume, expected):
    from app.config import settings
    from app.services.instrument_market_evidence import collect_market_snapshot

    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", True)
    market.adapters.bar_rows = [
        bar((NOW.date() - timedelta(days=offset)).isoformat(), volume=100)
        for offset in range(6, 0, -1)
    ]
    market.adapters.bar_rows[-1]["volume"] = volume
    result = collect_market_snapshot(["600519"], service=market.service)
    assert result["technicals"]["600519"]["volume_ratio"] == expected
    assert len(market.adapters.quotes_fixture.calls) == 1


def test_acceptance_is_hermetic_for_all_market_modules(market, monkeypatch):
    import requests
    from app.config import settings

    def forbidden(*args, **kwargs):
        pytest.fail("acceptance must not contact a production provider")

    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", True)
    monkeypatch.setattr(settings, "ACCEPTANCE_NOW_UTC", NOW.isoformat())
    monkeypatch.setattr(settings, "ACCEPTANCE_TRADE_DATE", NOW.date().isoformat())
    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(requests.Session, "get", forbidden)
    reset_runtime_provider_health_registry()
    try:
        service = InstrumentMarketService(market.db, now=lambda: NOW)
        evidence = service.evidence_snapshot(["600519", "159915", "000300.SH"])
        assert all(item.snapshot.quote.last is not None for item in evidence.items)
        assert all(item.bars.bars for item in evidence.items)
        assert evidence.items[0].snapshot.order_book.status == "available"
        assert evidence.items[0].snapshot.capital_flow.provider == "acceptance"
    finally:
        reset_runtime_provider_health_registry()


def test_market_facade_has_no_analysis_or_model_dependency():
    from pathlib import Path
    from app.market import instruments
    from app.market.providers import instruments as adapters

    for module in (instruments, adapters):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "call_model" not in source
        assert "analysis_engine" not in source
        assert "analysis_workflow" not in source
