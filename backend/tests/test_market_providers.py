"""Offline contract tests for the Phase B provider layer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.market.codes import exchange_for_code, normalize_security_code, provider_symbol
from app.market.models import DataQualityStatus, NormalizedQuote
from app.market.providers import (
    EastmoneyBatchQuoteProvider,
    FallbackQuoteProvider,
    HealthTrackedQuoteProvider,
    InMemoryQuoteProvider,
    TencentQuoteProvider,
    build_quote_snapshot,
    create_quote_provider,
    reset_runtime_provider_health_registry,
)
from app.market.providers.health import ProviderHealthStatus, ProviderHealthTracker
from app.market.providers.base import QuoteProvider
from app.market.quality import compare_quotes, validate_normalized_quote


def _quote(code: str, *, price: float = 10.0, provider: str = "fixture") -> NormalizedQuote:
    return NormalizedQuote(
        code=code,
        name="fixture",
        security_type="ETF" if normalize_security_code(code).startswith(("15", "16", "18", "50", "51")) else "STOCK",
        price=price,
        prev_close=price - 0.1,
        open=price,
        high=price + 0.2,
        low=price - 0.2,
        pct_change=1.0,
        volume=1000,
        amount=10000,
        provider=provider,
        source_timestamp=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
    )


def _tencent_line(
    *,
    code: str = "600519",
    name: str = "贵州茅台",
    price: str = "1600.00",
    quote_time: str = "10:30:00",
) -> str:
    fields = [""] * 38
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[4] = "1590.00"
    fields[5] = "1595.00"
    fields[30] = quote_time
    fields[32] = "0.63"
    fields[33] = "1610.00"
    fields[34] = "1588.00"
    fields[36] = "123456"
    fields[37] = "987654321"
    return 'v_sh600519="' + "~".join(fields) + '";'


def test_code_normalization_and_exchange_inference_includes_sz_etf():
    assert [normalize_security_code(value) for value in ("600519", "sh600519", "SH600519", "600519.SH")] == [
        "600519",
        "600519",
        "600519",
        "600519",
    ]
    assert normalize_security_code("SZ000001") == "000001"
    assert normalize_security_code("000001.SZ") == "000001"
    assert normalize_security_code("not-a-code") == ""
    assert exchange_for_code("159915") == "SZSE"
    assert exchange_for_code("920001") == "BSE"
    assert exchange_for_code("900901") == "SSE"
    assert exchange_for_code("912345") is None
    assert provider_symbol("159915", "tencent") == "sz159915"
    assert provider_symbol("920001", "tencent") == "bj920001"


def test_tencent_adapter_decodes_gbk_and_normalizes_wire_fields():
    raw = _tencent_line().encode("gb18030")
    captured: dict[str, object] = {}

    class Response:
        content = raw

        def raise_for_status(self):
            return None

    def request(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return Response()

    provider = TencentQuoteProvider(request=request)
    result = provider.get_quotes(["SH600519", "600519"])
    quote = result["600519"]
    assert quote.provider == "tencent"
    assert quote.code == "600519"
    assert quote.price == pytest.approx(1600.0)
    assert quote.amount == pytest.approx(987654321.0)
    assert quote.source_timestamp is not None
    assert "sh600519" in str(captured["url"])


def test_legacy_tencent_parser_preserves_hhmmss_quote_time():
    from app.services.market_data import _parse_tencent_line

    parsed = _parse_tencent_line(_tencent_line(quote_time="10:30:00"))
    assert parsed is not None
    assert parsed["quote_time"] == "10:30:00"


def test_eastmoney_batch_adapter_uses_paging_and_only_exposes_normalized_fields():
    calls: list[dict[str, object]] = []

    pages = {
        1: {"data": {"total": 2, "diff": [{"f12": "600519", "f14": "贵州茅台", "f43": 1600.0, "f60": 1590.0, "f46": 1595.0, "f44": 1610.0, "f45": 1588.0, "f47": 123456, "f48": 987654321, "f170": 0.63, "f86": "20260822103000"}]}},
        2: {"data": {"total": 2, "diff": [{"f12": "159915", "f14": "创业板ETF", "f43": 2.5, "f60": 2.49, "f46": 2.495, "f44": 2.51, "f45": 2.48, "f47": 10, "f48": 20, "f170": 0.4}] }},
    }

    class Response:
        def __init__(self, value):
            self.value = value

        def json(self):
            return self.value

        def raise_for_status(self):
            return None

    def transport(url, **kwargs):
        calls.append(kwargs["params"])
        return Response(pages[int(kwargs["params"]["pn"])])

    provider = EastmoneyBatchQuoteProvider(transport=transport, page_size=1)
    result = provider.get_quotes(["600519", "159915"])
    assert len(calls) == 2
    assert result["600519"].price == pytest.approx(1600.0)
    assert result["159915"].exchange == "SZSE"
    assert result["159915"].security_type == "ETF"
    normalized = result["600519"].to_dict()
    assert "f43" not in normalized and "f12" not in normalized
    assert normalized["raw_reference"].startswith("https://push2.eastmoney.com/")


def test_eastmoney_factory_applies_configured_throttle():
    from app.config import settings

    provider = create_quote_provider("eastmoney")

    assert isinstance(provider, HealthTrackedQuoteProvider)
    assert isinstance(provider.provider, EastmoneyBatchQuoteProvider)
    assert provider.provider.min_interval_seconds == pytest.approx(
        settings.EASTMONEY_MIN_INTERVAL_SECONDS
    )


class _FailingProvider(QuoteProvider):
    name = "failing"

    def get_quotes(self, codes):
        raise RuntimeError("primary timeout")


def test_fallback_preserves_primary_error_and_marks_fallback_level():
    fallback = FallbackQuoteProvider([_FailingProvider(), InMemoryQuoteProvider({"600519": _quote("600519")})])
    result = fallback.get_quotes(["SH600519"])
    quote = result["600519"]
    assert quote.provider == "inmemory"
    assert quote.fallback_level == 1
    assert any("primary timeout" in error for error in quote.errors)
    assert any(item["error_code"] == "provider_failure" for item in fallback.last_errors)


def test_fallback_run_metadata_records_each_provider_contribution():
    primary = InMemoryQuoteProvider(
        {"600519": _quote("600519", provider="primary")},
        provider="primary",
    )
    secondary = InMemoryQuoteProvider(
        {"000001": _quote("000001", provider="secondary")},
        provider="secondary",
    )
    fallback = FallbackQuoteProvider([primary, secondary])

    result = fallback.get_quotes(["600519", "000001"])
    metadata = fallback.get_run_metadata()

    assert result["600519"].provider == "primary"
    assert result["000001"].provider == "secondary"
    assert result["000001"].fallback_level == 1
    assert metadata["provider"] == "mixed"
    assert metadata["provider_counts"] == {"primary": 1, "secondary": 1}
    assert metadata["fallback_level"] == 1
    assert any(
        item["provider"] == "primary" and item["error_code"] == "quote_unusable"
        for item in metadata["fallback_errors"]
    )


def test_fallback_all_fail_returns_explicit_missing_quote_and_diagnostics():
    provider = FallbackQuoteProvider([_FailingProvider(), InMemoryQuoteProvider({})])
    result = provider.get_quotes(["600519"])
    assert result["600519"].quality_status == DataQualityStatus.MISSING
    assert result["600519"].provider == "fallback"
    assert any(item["error_code"] == "all_providers_failed" for item in provider.last_errors)


def test_fallback_replaces_stale_primary_quote_by_default():
    stale = _quote("600519", provider="primary")
    stale.quality_status = DataQualityStatus.STALE
    primary = InMemoryQuoteProvider({"600519": stale}, provider="primary")
    secondary = InMemoryQuoteProvider({"600519": _quote("600519", price=11.0)}, provider="secondary")
    result = FallbackQuoteProvider([primary, secondary]).get_quotes(["600519"])
    assert result["600519"].provider == "secondary"
    assert result["600519"].fallback_level == 1


def test_health_tracker_circuit_recovery_and_latency():
    now = [0.0]
    tracker = ProviderHealthTracker(failure_threshold=2, cooldown_seconds=10, clock=lambda: now[0])
    tracker.record_failure("tencent", "timeout", latency_ms=12.5)
    state = tracker.record_failure("tencent", "timeout", latency_ms=25.0)
    assert state.status == ProviderHealthStatus.CIRCUIT_OPEN
    assert tracker.allow("tencent") is False
    now[0] = 11.0
    assert tracker.get("tencent").status == ProviderHealthStatus.RECOVERING
    assert tracker.allow("tencent") is True
    tracker.record_success("tencent", latency_ms=5.0)
    state = tracker.get("tencent")
    assert state.status == ProviderHealthStatus.HEALTHY
    assert state.consecutive_failures == 0
    assert state.last_latency_ms == pytest.approx(5.0)


def test_independent_factory_requests_share_runtime_circuit_and_recover():
    now = [0.0]
    registry = reset_runtime_provider_health_registry(
        failure_threshold=3, cooldown_seconds=10, clock=lambda: now[0]
    )

    def failing_request(url, **kwargs):
        raise RuntimeError("upstream timeout")

    for _ in range(3):
        provider = create_quote_provider("tencent", request=failing_request)
        with pytest.raises(RuntimeError):
            provider.get_quotes(["600519"])
    assert registry.get("tencent").status == ProviderHealthStatus.CIRCUIT_OPEN

    blocked = create_quote_provider("tencent", request=failing_request)
    with pytest.raises(RuntimeError, match="circuit"):
        blocked.get_quotes(["600519"])

    class Response:
        content = _tencent_line(
            quote_time=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")
        ).encode("utf-8")

        def raise_for_status(self):
            return None

    now[0] = 11.0
    recovered = create_quote_provider("tencent", request=lambda *args, **kwargs: Response())
    quote = recovered.get_quotes(["600519"])["600519"]
    assert quote.quality_status == DataQualityStatus.VALID
    assert registry.get("tencent").status == ProviderHealthStatus.HEALTHY


def test_direct_all_missing_response_counts_as_provider_failure():
    registry = reset_runtime_provider_health_registry(failure_threshold=2, cooldown_seconds=60)
    provider = HealthTrackedQuoteProvider(InMemoryQuoteProvider({}), health=registry)
    assert provider.get_quotes(["600519"])["600519"].quality_status == DataQualityStatus.MISSING
    assert provider.get_quotes(["600519"])["600519"].quality_status == DataQualityStatus.MISSING
    assert registry.get("inmemory").status == ProviderHealthStatus.CIRCUIT_OPEN


def test_direct_empty_request_does_not_call_provider_or_pollute_health():
    registry = reset_runtime_provider_health_registry(failure_threshold=1, cooldown_seconds=60)

    class CountingProvider(QuoteProvider):
        name = "counting"

        def __init__(self):
            self.calls = 0

        def get_quotes(self, codes):
            self.calls += 1
            return {}

    raw = CountingProvider()
    provider = HealthTrackedQuoteProvider(raw, health=registry)

    assert provider.get_quotes([]) == {}
    assert raw.calls == 0
    assert registry.snapshot() == []


def test_fallback_empty_request_clears_previous_run_metadata():
    registry = reset_runtime_provider_health_registry(failure_threshold=1, cooldown_seconds=60)
    provider = FallbackQuoteProvider(
        [InMemoryQuoteProvider({"600519": _quote("600519")}, provider="primary")],
        health=registry,
    )

    assert provider.get_quotes(["600519"])["600519"].quality_status == DataQualityStatus.VALID
    assert provider.get_quotes([]) == {}
    assert provider.last_errors == []
    assert provider.get_run_metadata()["provider"] == "fallback"


def test_direct_provider_rejects_non_mapping_batch_and_records_failure():
    registry = reset_runtime_provider_health_registry(failure_threshold=1, cooldown_seconds=60)

    class MalformedProvider(QuoteProvider):
        name = "malformed"

        def get_quotes(self, codes):
            return [_quote("600519")]

    provider = HealthTrackedQuoteProvider(MalformedProvider(), health=registry)

    with pytest.raises(TypeError, match="mapping"):
        provider.get_quotes(["600519"])
    state = registry.get("malformed")
    assert state.failure_count == 1
    assert state.status == ProviderHealthStatus.CIRCUIT_OPEN


def test_direct_provider_drops_quotes_outside_requested_universe():
    registry = reset_runtime_provider_health_registry(failure_threshold=1, cooldown_seconds=60)

    class UnexpectedProvider(QuoteProvider):
        name = "unexpected"

        def get_quotes(self, codes):
            return {"000001": _quote("000001")}

    provider = HealthTrackedQuoteProvider(UnexpectedProvider(), health=registry)
    result = provider.get_quotes(["600519"])

    assert result == {}
    assert registry.get("unexpected").failure_count == 1
    assert any(item["error_code"] == "unexpected_quote" for item in provider.last_errors)


def test_fallback_records_malformed_primary_and_uses_secondary():
    registry = reset_runtime_provider_health_registry(failure_threshold=1, cooldown_seconds=60)

    class MalformedProvider(QuoteProvider):
        name = "malformed"

        def get_quotes(self, codes):
            return [_quote("600519")]

    secondary = InMemoryQuoteProvider({"600519": _quote("600519", provider="secondary")}, provider="secondary")
    provider = FallbackQuoteProvider([MalformedProvider(), secondary], health=registry)

    result = provider.get_quotes(["600519"])

    assert result["600519"].provider == "secondary"
    assert result["600519"].fallback_level == 1
    assert registry.get("malformed").failure_count == 1
    assert any(item["provider"] == "malformed" for item in provider.last_errors)


def test_tencent_missing_price_is_not_reported_as_valid():
    class Response:
        content = _tencent_line(
            price="",
            quote_time=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S"),
        ).encode("utf-8")

        def raise_for_status(self):
            return None

    quote = TencentQuoteProvider(request=lambda *args, **kwargs: Response()).get_quotes(["600519"])["600519"]

    assert quote.quality_status == DataQualityStatus.MISSING
    assert "missing_price" in quote.errors


def test_quote_validation_and_cross_provider_conflict():
    now = datetime.now(UTC)
    valid = _quote("600519", price=100.0)
    close = _quote("600519", price=100.4)
    conflict = _quote("600519", price=102.0)
    assert validate_normalized_quote(valid, now=now).status == DataQualityStatus.VALID
    assert compare_quotes(valid, close).quality_status == DataQualityStatus.VALID
    compared = compare_quotes(valid, conflict)
    assert compared.quality_status == DataQualityStatus.CONFLICT
    assert "price_conflict" in compared.errors
    suspended = _quote("600519")
    suspended.is_suspended = True
    status = compare_quotes(valid, suspended)
    assert status.trade_status_conflict is True


def test_tencent_uses_bounded_batches_for_large_universe():
    calls: list[str] = []

    class Response:
        content = b""

        def raise_for_status(self):
            return None

    def request(url, **kwargs):
        calls.append(url)
        return Response()

    codes = [f"{100000 + index:06d}" for index in range(1001)]
    provider = TencentQuoteProvider(request=request, batch_size=400)
    result = provider.get_quotes(codes)
    assert len(calls) == 3
    assert max(map(len, calls)) < 5000
    assert len(result) == len(codes)


def test_inmemory_provider_and_snapshot_scale_for_5000_quotes():
    quotes = {_code: _quote(_code) for _code in (str(100000 + index) for index in range(5001))}
    provider = InMemoryQuoteProvider(quotes)
    result = provider.get_all_a_share_quotes(quotes.keys())
    snapshot = build_quote_snapshot(result.values(), expected_count=5001, provider=provider.name)
    assert len(result) == 5001
    assert snapshot.received_count == 5001
    assert snapshot.coverage_ratio == pytest.approx(1.0)
    assert snapshot.quality_status == DataQualityStatus.VALID


def test_snapshot_collection_without_explicit_provider_is_fail_closed(monkeypatch):
    import app.services.market_snapshot_service as snapshot_service

    snapshot_service.set_snapshot_provider(None)

    def network_must_not_run(*args, **kwargs):
        raise AssertionError("default snapshot collection must not access a public provider")

    monkeypatch.setattr(snapshot_service, "build_all_a_quote_provider", network_must_not_run)
    result = snapshot_service.collect_snapshot_quotes({"codes": ["600519"], "expected_count": 1})
    assert result["quotes"] == []
    assert result["provider"] == "unconfigured"
    assert result["errors"][0]["code"] == "provider_not_configured"


def test_legacy_factory_wrapper_accepts_route_transport_and_timeout():
    from app.market.providers import build_quote_provider

    class Response:
        content = _tencent_line().encode("utf-8")

        def raise_for_status(self):
            return None

    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    provider = build_quote_provider(
        route="critical",
        primary="tencent",
        fallbacks=(),
        request=request,
        timeout=2.0,
    )
    quote = provider.get_quotes(["600519"])["600519"]
    assert quote.provider == "tencent"
    assert calls and calls[0][1]["timeout"] == 2.0


def test_legacy_fetch_quotes_keeps_quote_missing_error(monkeypatch):
    import app.services.market_data as market_data

    class MissingProvider:
        def __init__(self, **kwargs):
            pass

        def get_quotes(self, codes):
            return {
                "600519": NormalizedQuote(
                    code="600519",
                    provider="tencent",
                    quality_status=DataQualityStatus.MISSING,
                    errors=["quote_missing"],
                )
            }

    monkeypatch.setattr(market_data, "TencentQuoteProvider", MissingProvider)
    result = market_data.fetch_quotes(["600519"])
    assert result["600519"]["error"] == "quote_missing"
    assert result["600519"]["stale"] is True
    assert result["600519"]["source"] == "Tencent qt.gtimg.cn"
