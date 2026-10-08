"""Unified research coverage, incremental candidates and no future leakage."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from test_instrument_market import NOW, market, quote
from app.config import settings
from app.services import instrument_market_evidence as collector, market_data, unified_evidence as unified
from app.services.security_master import upsert_security


def event(**changes):
    return {"title": "美国半导体行业政策更新", "source": "fixture", "source_url": "https://example.org/news/1",
            "published_at": NOW.isoformat(), "fetched_at": NOW.isoformat(), **changes}


def pack_with(*records):
    return {"schema_version": unified.SCHEMA_VERSION, "records": {row["id"]: row for row in records}, "requested_codes": ["600519"]}


@pytest.mark.parametrize("clock", ["published_at", "fetched_at", "available_at", "observed_at"])
def test_each_future_clock_excludes_the_entire_record(clock):
    kwargs = {"source": "fixture", "fetched_at": NOW, "published_at": NOW, "observed_at": NOW, "available_at": NOW}
    kwargs[clock] = NOW + timedelta(seconds=1)
    record = unified.make_evidence_record("financials", {"private_future_fact": 999}, **kwargs)
    original = deepcopy(record)
    result = unified.project_unified_evidence(pack_with(record), NOW)
    assert result["records"] == {}
    assert result["excluded"][0]["reason"] == "NOT_YET_AVAILABLE"
    assert "private_future_fact" not in str(result)
    assert record == original


def test_old_publication_retrieved_today_cannot_enter_yesterday_replay():
    row = unified.make_evidence_record("financials", {"period_end": "2025-12-31"}, source="fixture",
                                       published_at=NOW - timedelta(days=100), fetched_at=NOW)
    result = unified.project_unified_evidence(pack_with(row), NOW - timedelta(days=1))
    assert not result["records"]
    assert unified.evidence_time(row["available_at"]) == NOW


def test_missing_clock_and_expiration_are_explicit_and_version_is_stable():
    missing = unified.make_evidence_record("news", {"title": "untimed"}, source="fixture", fetched_at=None)
    stale = unified.make_evidence_record("quote", {"last": 10}, source="fixture", fetched_at=NOW, observed_at=NOW,
                                         code="600519.SH", status="degraded")
    result = unified.project_unified_evidence(pack_with(missing, stale), NOW + timedelta(minutes=5))
    assert result["records"][stale["id"]]["status"] == "stale"
    assert result["coverage"]["600519"]["quote"] == 0
    assert result["excluded"][0]["reason"] == "AVAILABILITY_UNPROVEN"
    assert result["evidence_version"] == unified.project_unified_evidence(pack_with(missing, stale), NOW + timedelta(minutes=5))["evidence_version"]


def test_date_only_publication_does_not_appear_before_day_end():
    row = unified.make_evidence_record("announcement", {}, source="fixture", fetched_at=NOW, published_at="2026-09-10")
    assert not unified.project_unified_evidence(pack_with(row), NOW)["records"]
    assert unified.evidence_time("not a timestamp") is None


def test_event_industry_holding_links_are_inferences_and_time_filtered():
    metadata = {"identity": {"name": "测试股份"}, "metadata": {"industry": "半导体"}}
    result = unified.build_unified_evidence(
        {"captured_at": NOW.isoformat(), "news": [event()]}, requested_codes=["600519"],
        supplemental={"metadata": {"600519": metadata}},
    )
    assert any(row["kind"] == "international_event" for row in result["records"].values())
    assert result["event_links"][0]["code"] == "600519"
    assert result["event_links"][0]["sectors"] == ["半导体"]
    assert result["event_links"][0]["evidence_type"] == "inference"
    assert result["event_links"][0]["causal_effect"] == "unverified"
    replay = unified.project_unified_evidence(result, NOW - timedelta(seconds=1))
    assert replay["event_links"] == []


def stub_extras(monkeypatch):
    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", False)
    monkeypatch.setattr(collector, "collect_supplemental_evidence", lambda *args, **kwargs: {})
    monkeypatch.setattr(market_data, "fetch_market_news", lambda **kwargs: [event()])
    monkeypatch.setattr(market_data, "fetch_sector_heat", lambda **kwargs: [])
    monkeypatch.setattr(market_data, "fetch_etf_leaders", lambda: [])


def test_all_twelve_holdings_receive_all_announcements(market, monkeypatch):
    stub_extras(monkeypatch)
    codes = [f"601{index:03d}" for index in range(12)]
    for code in codes:
        upsert_security(market.db, {"code": code + ".SH", "security_type": "STOCK"})
        market.adapters.quotes_fixture.values[code + ".SH"] = quote(code + ".SH")
    market.db.commit()
    calls = []
    def announcements(code, **kwargs):
        calls.append(code)
        return [event(title=f"{code} 公告 {index}") for index in range(5)]
    monkeypatch.setattr(market_data, "fetch_announcements", announcements)
    snapshot = collector.collect_market_snapshot(codes, service=market.service)
    assert calls == codes
    assert sum(len(rows) for rows in snapshot["announcements"].values()) == 60
    assert all(snapshot["unified_evidence"]["coverage"][code]["announcement"] == 5 for code in codes)
    assert len(market.adapters.quotes_fixture.calls) == 1


def test_candidate_enrichment_reuses_market_and_existing_holdings(market, monkeypatch):
    stub_extras(monkeypatch)
    announcement_calls = []
    monkeypatch.setattr(market_data, "fetch_announcements", lambda code, **kwargs: announcement_calls.append(code) or [event()])
    snapshot = collector.collect_market_snapshot(["600519"], service=market.service)
    original = deepcopy(snapshot)
    def forbidden(**kwargs):
        pytest.fail("candidate enrichment must not refetch global market context")
    monkeypatch.setattr(market_data, "fetch_market_news", forbidden)
    monkeypatch.setattr(market_data, "fetch_sector_heat", forbidden)
    enriched = collector.enrich_candidate_evidence(snapshot, ["600519", "159915", "159915"], service=market.service)
    assert snapshot == original
    assert announcement_calls == ["600519", "159915"]
    assert market.adapters.quotes_fixture.calls[-1] == ["159915.SZ"]
    assert enriched["unified_evidence"]["requested_codes"] == ["600519", "159915"]
    assert enriched["evidence_version"] != original["evidence_version"]
    assert collector.enrich_candidate_evidence(enriched, ["159915"], service=market.service) is enriched


def test_unknown_identity_is_a_gap_without_dropping_known_holdings(market, monkeypatch):
    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", True)
    snapshot = collector.collect_market_snapshot(["600519", "600999"], service=market.service)
    assert snapshot["quotes"]["600519"]["price"] == 100
    assert snapshot["unified_evidence"]["coverage"]["600999"]["quote"] == 0
    assert "identity:600999.SH:unavailable" in snapshot["errors"]


def test_no_partial_window_is_labelled_twenty_day_average(market, monkeypatch):
    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", True)
    snapshot = collector.collect_market_snapshot(["600519"], service=market.service)
    assert snapshot["technicals"]["600519"]["ma20"] is None
    assert snapshot["unified_evidence"]["coverage"]["600519"]["factors"] == 0


def test_sector_pagination_includes_laggards(monkeypatch):
    calls = []
    def load(url, params):
        page = int(params["pn"])
        calls.append(page)
        rows = [{"f12": f"BK{page}{index}", "f14": "行业", "f3": 2 if page == 1 else -3} for index in range(2)]
        return SimpleNamespace(json=lambda: {"data": {"total": 4, "diff": rows}})
    monkeypatch.setattr(market_data, "_em_get", load)
    result = market_data.fetch_sector_heat(limit=500)
    assert calls == [1, 2]
    assert len(result) == 4 and result[-1]["pct_change"] == -3


def test_financial_provider_rows_keep_publication_and_fetch_metadata(market, monkeypatch):
    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", False)
    monkeypatch.setattr(unified, "utc_now", lambda: NOW)
    calls = []
    class Provider:
        client = SimpleNamespace(configured=True, base_url="https://example.org")
        def response(self, endpoint):
            calls.append(endpoint)
            return SimpleNamespace(endpoint=endpoint, data={"timestamp": int(NOW.timestamp() * 1000), "item": [
                {"thscode": "600519.SH", "pe_ttm": 12, "net_profit": 200, "published_at": "2026-09-09T18:00:00+08:00"},
            ]})
        def get_valuation(self, codes):
            return self.response("/valuation")
        def get(self, endpoint, **kwargs):
            return self.response(endpoint)
        def get_indicators(self, code, report):
            return self.response("/indicators")
    provider = Provider()
    supplement = unified.collect_supplemental_evidence(market.service, ["600519"], provider=provider, include_market=False)
    result = unified.build_unified_evidence({"captured_at": NOW.isoformat()}, requested_codes=["600519"], supplemental=supplement)
    records = list(result["records"].values())
    financials = [row for row in records if row["kind"] == "financials"]
    assert len(financials) == 4 and result["coverage"]["600519"]["valuation"] == 1
    assert financials[0]["source_url"].startswith("https://example.org/")
    assert financials[0]["published_at"] and financials[0]["fetched_at"]
    assert len(calls) == 5
    unified.collect_supplemental_evidence(market.service, ["600519"], provider=provider, include_market=False)
    assert len(calls) == 5


def test_unconfigured_financial_provider_does_not_invent_metrics(market, monkeypatch):
    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", False)
    provider = SimpleNamespace(client=SimpleNamespace(configured=False))
    supplement = unified.collect_supplemental_evidence(market.service, ["600519", "159915"], provider=provider, include_market=False)
    assert supplement["financials"] == supplement["valuation"] == {}
    assert len(supplement["gaps"]) == 2
    assert all(row["reason"] == "PROVIDER_NOT_CONFIGURED" and row["code"] == "600519" for row in supplement["gaps"])


def test_nested_provider_indicators_are_retained_and_missing_metrics_not_available(market, monkeypatch):
    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", False)
    monkeypatch.setattr(unified, "utc_now", lambda: NOW)
    class Provider:
        client = SimpleNamespace(configured=True, base_url="https://example.org")
        def get_valuation(self, codes):
            return SimpleNamespace(endpoint="/valuation", data={"item": [{"thscode": "600519.SH", "pe_ttm": None}]})
        def get(self, endpoint, **kwargs):
            return SimpleNamespace(endpoint=endpoint, data={"item": []})
        def get_indicators(self, code, report):
            return SimpleNamespace(endpoint="/indicators", data={"abilities": [{"ability": "growth", "indicators": [{"index_id": "revenue_yoy", "value": 2.5}]}]})
    supplement = unified.collect_supplemental_evidence(market.service, ["600519"], provider=Provider(), include_market=False)
    result = unified.build_unified_evidence({"captured_at": NOW.isoformat()}, requested_codes=["600519"], supplemental=supplement)
    assert result["coverage"]["600519"]["valuation"] == 0
    assert result["coverage"]["600519"]["financials"] == 1
    assert any("FINANCIAL_METRICS_MISSING" == gap["reason"] for gap in result["gaps"])


def test_unavailable_market_metrics_stay_unavailable():
    overview = {"breadth": {"status": "unavailable", "advancers": 0}, "all_a_median": {"status": "unavailable", "current_value": None}}
    result = unified.build_unified_evidence({"captured_at": NOW.isoformat()}, requested_codes=[], supplemental={"overview": overview})
    breadth = next(row for row in result["records"].values() if row["kind"] == "market_breadth")
    assert breadth["status"] == "unavailable" and "MARKET_CONTEXT_UNAVAILABLE" in breadth["gaps"]


def test_future_event_is_removed_from_legacy_and_unified_context(market, monkeypatch):
    stub_extras(monkeypatch)
    monkeypatch.setattr(market_data, "fetch_announcements", lambda *args, **kwargs: [])
    monkeypatch.setattr(market_data, "fetch_market_news", lambda **kwargs: [event(title="future event", published_at=(NOW + timedelta(days=1)).isoformat())])
    snapshot = collector.collect_market_snapshot(["600519"], service=market.service)
    assert snapshot["news"] == []
    assert "future event" not in str(snapshot["unified_evidence"])


def test_context_index_cannot_inflate_stock_quote_coverage(market, monkeypatch):
    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", True)
    snapshot = collector.collect_market_snapshot(["000001.SZ"], service=market.service)
    assert snapshot["unified_evidence"]["coverage"]["000001"]["quote"] == 1


def test_safe_source_links_reject_embedded_credentials():
    record = unified.make_evidence_record("news", {"url": "https://example.org/news?api_key=credential"}, source="fixture", fetched_at=NOW, source_url="https://example.org/news?api_key=credential")
    assert record["source_url"] is None
    assert "SOURCE_URL_MISSING" in record["gaps"]
    assert "credential" not in str(record)


def test_merge_keeps_exclusion_audit_for_future_financials():
    future = unified.make_evidence_record("financials", {"net_profit": 99}, source="fixture", fetched_at=NOW + timedelta(days=1))
    first = unified.project_unified_evidence(pack_with(future), NOW)
    merged = unified.merge_unified_evidence(first, pack_with(), as_of=NOW)
    assert merged["excluded"] == first["excluded"]
    assert unified.project_unified_evidence(merged, NOW)["evidence_version"] == merged["evidence_version"]
