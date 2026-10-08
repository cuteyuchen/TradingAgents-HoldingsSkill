"""Offline expiry, isolation, and concurrent market-refresh contracts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.market.foundation import MarketFoundationService
from app.market_models import SecurityMaster
from app.market.session import MarketDataBasis
from app.services import market_snapshot_service as snapshots


@pytest.fixture(autouse=True)
def isolated_cache():
    snapshots.clear_market_foundation_snapshot_cache()
    yield
    snapshots.clear_market_foundation_snapshot_cache()


@pytest.mark.parametrize("quality,expiry", [
    ("VALID", 30), ("DEGRADED", 30), ("STALE", 30),
    ("MISSING", 15), ("INVALID", 15), ("CONFLICT", 15),
])
def test_snapshot_cache_expires_with_success_or_failure_ttl_and_copies_data(monkeypatch, quality, expiry):
    clock = [100.0]
    monkeypatch.setattr(snapshots.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(settings, "MARKET_FOUNDATION_LIVE_CACHE_SECONDS", 30)
    monkeypatch.setattr(settings, "MARKET_FOUNDATION_FAILURE_CACHE_SECONDS", 15)
    captured_at = datetime(2026, 9, 7, 2, 30, tzinfo=UTC)
    calls = []

    def load(_db, **kwargs):
        calls.append(kwargs)
        return {"quality_status": quality, "completed_at": captured_at, "quotes": [{"price": 10}]}

    monkeypatch.setattr(snapshots, "get_all_a_share_quote_snapshot", load)
    engine = create_engine("sqlite://")
    with Session(engine) as db:
        first = snapshots.get_cached_all_a_share_quote_snapshot(db)
        assert first["metadata"]["market_foundation_cache_hit"] is False
        first["quotes"][0]["price"] = 999
        clock[0] = 100 + expiry - 0.1
        cached = snapshots.get_cached_all_a_share_quote_snapshot(db)
        assert cached["metadata"]["market_foundation_cache_hit"] is True
        assert cached["quotes"][0]["price"] == 10
        assert cached["completed_at"] == captured_at
        assert len(calls) == 1
        clock[0] = 100 + expiry
        assert snapshots.get_cached_all_a_share_quote_snapshot(db)["metadata"]["market_foundation_cache_hit"] is False
        assert len(calls) == 2
    engine.dispose()


def test_simultaneous_snapshot_cache_misses_share_one_provider_refresh(monkeypatch):
    workers = 8
    start = Barrier(workers)
    entered = Event()
    release = Event()
    calls = []
    engine = create_engine("sqlite://")

    def load(_db, **kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(timeout=5)
        return {"quality_status": "VALID", "quotes": [{"price": 10}]}

    def read():
        with Session(engine) as db:
            start.wait(timeout=5)
            return snapshots.get_cached_all_a_share_quote_snapshot(db, max_age_seconds=30)

    monkeypatch.setattr(snapshots, "get_all_a_share_quote_snapshot", load)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(read) for _ in range(workers)]
        assert entered.wait(timeout=5)
        release.set()
        results = [future.result(timeout=5) for future in futures]
    assert len(calls) == 1
    assert sum(result["metadata"]["market_foundation_cache_hit"] for result in results) == workers - 1
    results[0]["quotes"][0]["price"] = 999
    assert all(result["quotes"][0]["price"] == 10 for result in results[1:])
    engine.dispose()


def test_snapshot_cache_isolates_database_day_provider_and_universe(monkeypatch):
    calls = []

    def load(_db, **kwargs):
        calls.append(kwargs)
        return {"quality_status": "VALID", "quotes": []}

    monkeypatch.setattr(snapshots, "get_all_a_share_quote_snapshot", load)
    engines = [create_engine("sqlite://") for _ in range(2)]
    with Session(engines[0]) as first, Session(engines[1]) as second:
        for db, kwargs in [
            (first, {}), (second, {}), (first, {"trade_date": date(2026, 9, 7)}),
            (first, {"provider": "tencent"}), (first, {"include_bse": False}),
            (first, {"include_suspended": False}),
        ]:
            assert snapshots.get_cached_all_a_share_quote_snapshot(db, **kwargs)["metadata"]["market_foundation_cache_hit"] is False
            assert snapshots.get_cached_all_a_share_quote_snapshot(db, **kwargs)["metadata"]["market_foundation_cache_hit"] is True
    assert len(calls) == 6
    for engine in engines:
        engine.dispose()


def test_refresh_exception_releases_lock_and_allows_retry():
    def fail():
        raise TimeoutError("upstream timeout")

    with pytest.raises(TimeoutError):
        snapshots.get_or_load_market_data_cache("failed", fail, ttl=30)
    result, cache_hit = snapshots.get_or_load_market_data_cache("failed", lambda: {"price": 10}, ttl=30)
    assert result == {"price": 10}
    assert cache_hit is False


@pytest.mark.parametrize("basis,ttl", [(MarketDataBasis.LIVE.value, 30), (MarketDataBasis.SESSION_CLOSE.value, 300)])
def test_foundation_forwards_configured_live_and_closed_ttls(monkeypatch, basis, ttl):
    calls = []
    monkeypatch.setattr(settings, "MARKET_FOUNDATION_LIVE_CACHE_SECONDS", 30)
    monkeypatch.setattr(settings, "MARKET_FOUNDATION_CLOSED_CACHE_SECONDS", 300)
    monkeypatch.setattr(settings, "MARKET_FOUNDATION_FAILURE_CACHE_SECONDS", 15)

    def load(_db, **kwargs):
        calls.append(kwargs)
        return {"quality_status": "VALID"}

    monkeypatch.setattr("app.market.foundation.get_cached_all_a_share_quote_snapshot", load)
    engine = create_engine("sqlite://")
    with Session(engine) as db:
        MarketFoundationService(db)._load_quote_snapshot(
            trade_date=date(2026, 9, 7), session=SimpleNamespace(data_basis=basis),
        )
    assert calls[0]["max_age_seconds"] == ttl
    assert calls[0]["failure_ttl_seconds"] == 15
    engine.dispose()


@pytest.mark.parametrize("available,expiry", [(True, 30), (False, 15)])
def test_overview_and_major_indices_share_cached_index_snapshot(monkeypatch, available, expiry):
    clock = [100.0]
    monkeypatch.setattr(snapshots.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(settings, "MARKET_FOUNDATION_LIVE_CACHE_SECONDS", 30)
    monkeypatch.setattr(settings, "MARKET_FOUNDATION_FAILURE_CACHE_SECONDS", 15)
    moment = datetime(2026, 9, 7, 2, 30, tzinfo=UTC)
    session = SimpleNamespace(trading_date=moment.date(), data_basis=MarketDataBasis.LIVE.value)
    calls = []

    class Provider:
        def get_index_snapshot(self, codes):
            calls.append(list(codes))
            if not available:
                raise TimeoutError("unavailable")
            return {"item": [{"thscode": "000001.SH", "last_price": 3000}]}

    monkeypatch.setattr("app.market.foundation.FuyaoDataProvider", Provider)
    engine = create_engine("sqlite://")
    SecurityMaster.__table__.create(engine)
    with Session(engine) as db:
        service = MarketFoundationService(db)
        monkeypatch.setattr(service.session_service, "resolve_session", lambda *_args: session)
        rows, error = service._load_major_index_rows(now=moment)
        assert bool(rows) == available
        assert (error is None) == available
        assert len(service.major_indices(now=moment)) == 6
        assert len(calls) == 1
        clock[0] += expiry
        service._load_major_index_rows(now=moment)
        assert len(calls) == 2
    engine.dispose()
