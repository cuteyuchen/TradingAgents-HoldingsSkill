"""Focused Phase B.1 regression tests for snapshot and provider contracts."""
from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.market.models import DataQualityStatus, NormalizedQuote
from app.market.providers import (
    FallbackQuoteProvider,
    InMemoryQuoteProvider,
    reset_runtime_provider_health_registry,
)
from app.market.providers.base import build_quote_snapshot
from app.market.providers.identity import EastmoneySecurityProvider
from app.market_models import SecurityMaster
from app.market_runtime_models import MarketSnapshot, ProviderHealth, SourceLineage
from app.services.security_master import upsert_security


def _runtime_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    MarketSnapshot.__table__.create(engine)
    ProviderHealth.__table__.create(engine)
    SourceLineage.__table__.create(engine)
    return Session(engine)


def _security_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    SecurityMaster.__table__.create(engine)
    return Session(engine)


def _fresh_quote(
    code: str,
    *,
    provider: str = "fixture",
    source_timestamp: datetime | None = None,
    fetched_at: datetime | None = None,
    fallback_level: int = 0,
    raw_reference: str | None = None,
) -> NormalizedQuote:
    now = fetched_at or datetime.now(UTC)
    return NormalizedQuote(
        code=code,
        name=code,
        price=10.0,
        prev_close=9.9,
        open=9.95,
        high=10.1,
        low=9.8,
        pct_change=1.01,
        volume=1000,
        amount=10_000,
        provider=provider,
        source_timestamp=source_timestamp or now,
        fetched_at=now,
        fallback_level=fallback_level,
        raw_reference=raw_reference,
    )


def test_snapshot_freshness_uses_source_timestamp_and_exact_boundary():
    completed_at = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)
    stale = _fresh_quote(
        "600519",
        source_timestamp=completed_at - timedelta(seconds=91),
        fetched_at=completed_at,
    )
    fresh_at_boundary = _fresh_quote(
        "000001",
        source_timestamp=completed_at - timedelta(seconds=90),
        fetched_at=completed_at,
    )

    stale_snapshot = build_quote_snapshot(
        [stale],
        expected_count=1,
        completed_at=completed_at,
        max_age_seconds=90,
    )
    fresh_snapshot = build_quote_snapshot(
        [fresh_at_boundary],
        expected_count=1,
        completed_at=completed_at,
        max_age_seconds=90,
    )

    assert stale_snapshot.quality_status == DataQualityStatus.STALE
    assert stale_snapshot.quotes[0].quality_status == DataQualityStatus.STALE
    assert "quote_stale" in stale_snapshot.quotes[0].errors
    assert fresh_snapshot.quality_status == DataQualityStatus.VALID
    assert fresh_snapshot.quotes[0].quality_status == DataQualityStatus.VALID


def test_snapshot_does_not_count_unexpected_quote_toward_requested_coverage():
    completed_at = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)
    unexpected = _fresh_quote(
        "000001",
        source_timestamp=completed_at,
        fetched_at=completed_at,
    )

    snapshot = build_quote_snapshot(
        [unexpected],
        expected_count=1,
        requested_codes=["600519"],
        completed_at=completed_at,
        max_age_seconds=90,
    )

    assert snapshot.received_count == 0
    assert snapshot.coverage_ratio == 0.0
    assert "600519" in snapshot.missing_codes


def test_fallback_replaces_120_second_primary_quote_using_90_second_freshness():
    now = datetime.now(UTC)
    primary = InMemoryQuoteProvider(
        {
            "600519": _fresh_quote(
                "600519",
                provider="primary",
                source_timestamp=now - timedelta(seconds=120),
                fetched_at=now,
            )
        },
        provider="primary",
    )
    secondary = InMemoryQuoteProvider(
        {
            "600519": _fresh_quote(
                "600519",
                provider="secondary",
                source_timestamp=now - timedelta(seconds=10),
                fetched_at=now,
            )
        },
        provider="secondary",
    )

    result = FallbackQuoteProvider([primary, secondary]).get_quotes(["600519"])

    assert result["600519"].provider == "secondary"
    assert result["600519"].fallback_level == 1


def test_snapshot_api_ignores_client_owned_derived_fields(monkeypatch):
    from app.routers import market_v3

    captured: dict[str, object] = {}
    trusted_timestamp = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)

    def collect(request):
        captured.update(request)
        return {
            "quotes": [
                _fresh_quote(
                    "600519",
                    provider="trusted",
                    source_timestamp=trusted_timestamp,
                    fetched_at=trusted_timestamp,
                    fallback_level=1,
                    raw_reference="https://trusted.example/quotes",
                )
            ],
            "provider": "trusted",
            "fallback_level": 1,
            "requested_route": request["route"],
        }

    monkeypatch.setattr(market_v3, "collect_snapshot_quotes", collect)
    monkeypatch.setattr(market_v3, "sync_runtime_provider_health", lambda _db: [])
    db = _runtime_session()
    try:
        payload = market_v3.MarketSnapshotRequest(
            codes=["600519.SH"],
            expected_count=99_999,
            provider="critical",
            fallback_level=99,
            trade_date=date(2099, 1, 1),
            provider_endpoint="https://attacker.invalid/forged",
            persist=True,
        )
        snapshot = market_v3.create_quote_snapshot(payload, db=db, _current_user=object())

        assert captured == {"codes": ["600519"], "route": "critical"}
        assert snapshot["expected_count"] == 1
        assert snapshot["received_count"] == 1
        assert snapshot["coverage_ratio"] == 1.0
        assert snapshot["provider"] == "trusted"
        assert snapshot["fallback_level"] == 1
        assert snapshot["trade_date"] == "2026-08-23"

        row = db.query(MarketSnapshot).one()
        lineage = db.query(SourceLineage).one()
        assert row.expected_count == 1
        assert row.fallback_level == 1
        assert lineage.provider == "trusted"
        assert lineage.provider_endpoint == "https://trusted.example/quotes"
        assert "attacker.invalid" not in str(lineage.provider_endpoint)
    finally:
        db.close()


def test_runtime_health_circuit_state_is_persisted_and_recovers_after_cooldown():
    from app.services.market_snapshot_service import sync_runtime_provider_health

    clock = [0.0]
    registry = reset_runtime_provider_health_registry(
        failure_threshold=2,
        cooldown_seconds=10,
        clock=lambda: clock[0],
    )
    db = _runtime_session()
    try:
        registry.record_failure("tencent", "timeout-1", latency_ms=11.0)
        registry.record_failure("tencent", "timeout-2", latency_ms=22.0)
        sync_runtime_provider_health(db)
        db.commit()

        row = db.query(ProviderHealth).one()
        assert row.status == "CIRCUIT_OPEN"
        assert row.failure_count == 2
        assert row.consecutive_failures == 2
        assert row.last_error == "timeout-2"
        assert row.last_latency_ms == 22.0

        clock[0] = 11.0
        sync_runtime_provider_health(db)
        db.commit()
        db.refresh(row)
        assert row.status == "RECOVERING"
        assert row.consecutive_failures == 2
    finally:
        db.close()
        reset_runtime_provider_health_registry()


def test_security_provider_classifies_supported_code_families_conservatively():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "total": 6,
                    "diff": [
                        {"f12": "600519", "f14": "沪市股票"},
                        {"f12": "159915", "f14": "深市ETF"},
                        {"f12": "510300", "f14": "沪市ETF"},
                        {"f12": "588000", "f14": "科创ETF"},
                        {"f12": "920001", "f14": "北交所股票"},
                        {"f12": "912345", "f14": "未知代码"},
                    ],
                }
            }

    provider = EastmoneySecurityProvider(
        request=lambda *_args, **_kwargs: Response(),
        page_size=5000,
        min_interval_seconds=0,
    )
    rows = {row["code"]: row for row in provider.list_securities()}

    assert rows["600519"]["exchange"] == "SSE"
    assert rows["600519"]["security_type"] == "STOCK"
    assert rows["159915"]["exchange"] == "SZSE"
    assert rows["159915"]["security_type"] == "ETF"
    assert rows["510300"]["security_type"] == "ETF"
    assert rows["588000"]["security_type"] == "ETF"
    assert rows["920001"]["exchange"] == "BSE"
    assert rows["920001"]["security_type"] == "STOCK"
    assert "912345" not in rows


def test_all_a_snapshot_uses_core_stock_universe_in_one_batch(monkeypatch):
    from app.services import market_snapshot_service

    db = _security_session()
    calls: list[dict[str, object]] = []
    try:
        upsert_security(db, {"code": "600519", "security_type": "STOCK"})
        upsert_security(db, {"code": "000001", "security_type": "STOCK", "is_st": True})
        upsert_security(db, {"code": "300750", "security_type": "STOCK", "is_suspended": True})
        upsert_security(db, {"code": "920001", "security_type": "STOCK"})
        upsert_security(db, {"code": "510300", "security_type": "ETF"})
        upsert_security(db, {"code": "600000", "security_type": "STOCK", "status": "DELISTED"})
        db.commit()

        def collect(request):
            calls.append(dict(request))
            now = datetime.now(UTC)
            return {
                "quotes": [_fresh_quote(code, fetched_at=now) for code in request["codes"]],
                "provider": "fixture",
                "requested_route": request["route"],
            }

        monkeypatch.setattr(market_snapshot_service, "collect_snapshot_quotes", collect)
        snapshot = market_snapshot_service.get_all_a_share_quote_snapshot(db)

        assert calls == [{"codes": ["000001", "600519"], "route": "all_a"}]
        assert snapshot["expected_count"] == 2
        assert snapshot["received_count"] == 2
        assert [quote["code"] for quote in snapshot["quotes"]] == ["000001", "600519"]
    finally:
        db.close()
