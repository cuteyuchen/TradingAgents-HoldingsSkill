"""Offline daily-bar window repair and revision contracts."""
from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.market_engine_models import DailyBarCache
from app.market_models import TradingCalendar
from app.services.daily_bar_cache import load_daily_bars, sync_daily_bar_cache, upsert_daily_bars


DAY = date(2026, 9, 7)
CUTOFF = datetime(2026, 9, 7, 8, 30, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in (DailyBarCache, TradingCalendar):
        model.__table__.create(engine)
    with Session(engine) as session:
        session.add_all(
            TradingCalendar(market="CN", trade_date=DAY - timedelta(days=offset), is_open=True)
            for offset in range(5)
        )
        session.commit()
        yield session
    engine.dispose()


def _bar(offset: int, *, close: float = 100.0, adjustment: str = "QFQ", **extra) -> dict:
    return {
        "code": "600519",
        "trade_date": DAY - timedelta(days=offset),
        "close": close,
        "adjustment": adjustment,
        "provider": "fixture",
        "fetched_at": CUTOFF,
        "available_at": CUTOFF - timedelta(days=offset),
        **extra,
    }


class Provider:
    name = "fixture"

    def __init__(self, bars: list[dict]):
        self.bars = bars
        self.calls = []

    def get_kline(self, code, *, limit=30):
        self.calls.append((code, limit))
        return self.bars[-limit:]


@pytest.mark.parametrize(
    "cached_offsets,expected_limit",
    [([0], 5), ([0, 1, 2, 3], 5), ([0, 1, 3, 4], 5), (list(range(5)), 2)],
)
def test_sync_repairs_head_and_middle_gaps_and_always_refreshes_overlap(db, cached_offsets, expected_limit):
    upsert_daily_bars(db, [_bar(offset) for offset in cached_offsets])
    db.commit()
    provider = Provider([_bar(offset, close=110.0 - offset) for offset in range(4, -1, -1)])

    result = sync_daily_bar_cache(db, provider, ["600519"], as_of=DAY, available_at=CUTOFF, bootstrap_limit=5)

    assert provider.calls == [("600519", expected_limit)]
    assert result["status"] == "ready"
    assert result["skipped_codes"] == 0
    assert result["missing_open_days"] == 0
    assert db.scalar(select(func.count()).select_from(DailyBarCache)) == 5
    current = load_daily_bars(db, ["600519"], trade_date=DAY, available_at=CUTOFF)[-1]
    assert current["close"] == 110.0
    assert current["available_at"] == CUTOFF.replace(tzinfo=None)
    assert current["fetched_at"] == CUTOFF.replace(tzinfo=None)

    rerun = sync_daily_bar_cache(db, provider, ["600519"], as_of=DAY, available_at=CUTOFF, bootstrap_limit=5)
    assert provider.calls[-1] == ("600519", 2)
    assert rerun["status"] == "ready"
    assert db.scalar(select(func.count()).select_from(DailyBarCache)) == 5


def test_sync_replaces_same_day_provisional_values_without_losing_source_times(db):
    upsert_daily_bars(db, [_bar(0, close=101.0, available_at=CUTOFF - timedelta(hours=2))])
    db.commit()
    provider = Provider([_bar(0, close=105.0, metadata={"finalized": True})])

    result = sync_daily_bar_cache(db, provider, ["600519"], as_of=DAY, available_at=CUTOFF, bootstrap_limit=1)

    assert result["persisted_rows"] == 1
    assert result["status"] == "ready"
    assert db.scalar(select(func.count()).select_from(DailyBarCache)) == 1
    current = load_daily_bars(db, ["600519"], trade_date=DAY, available_at=CUTOFF)[-1]
    assert current["close"] == 105.0
    assert current["metadata"]["finalized"] is True
    assert current["available_at"] == CUTOFF.replace(tzinfo=None)
    assert load_daily_bars(db, ["600519"], trade_date=DAY, available_at=CUTOFF - timedelta(minutes=1)) == []


def test_overlap_preserves_previous_close_even_when_provider_returns_descending_dates(db):
    upsert_daily_bars(db, [_bar(offset, close=100.0 - offset) for offset in range(5)])
    db.commit()
    provider = Provider([_bar(0, close=110.0), _bar(1, close=109.0)])

    result = sync_daily_bar_cache(db, provider, ["600519"], as_of=DAY, available_at=CUTOFF, bootstrap_limit=5)

    assert result["status"] == "ready"
    assert provider.calls == [("600519", 2)]
    bars = load_daily_bars(db, ["600519"], trade_date=DAY, available_at=CUTOFF)
    assert bars[-2]["prev_close"] == 98.0
    assert bars[-1]["prev_close"] == 109.0


@pytest.mark.parametrize("older_available", [True, False])
def test_upsert_never_overwrites_newer_known_revision_with_older_source(db, older_available):
    upsert_daily_bars(db, [_bar(0, close=110.0)])
    db.commit()
    older = _bar(
        0,
        close=100.0,
        available_at=CUTOFF - timedelta(minutes=1) if older_available else CUTOFF,
        fetched_at=CUTOFF + timedelta(minutes=1) if older_available else CUTOFF - timedelta(minutes=1),
    )

    assert upsert_daily_bars(db, [older]) == 0
    assert load_daily_bars(db, ["600519"], trade_date=DAY, available_at=CUTOFF)[-1]["close"] == 110.0


def test_historical_sync_cannot_drop_later_revision_or_claim_rejected_bar_is_visible(db):
    upsert_daily_bars(db, [_bar(0, close=110.0, available_at=CUTOFF + timedelta(minutes=1))])
    db.commit()

    result = sync_daily_bar_cache(
        db, Provider([_bar(0, close=100.0)]), ["600519"],
        as_of=DAY, available_at=CUTOFF, bootstrap_limit=1,
    )

    assert result["status"] == "degraded"
    assert result["persisted_rows"] == 0
    assert result["missing_open_days"] == 1
    assert load_daily_bars(db, ["600519"], trade_date=DAY, available_at=CUTOFF) == []
    assert load_daily_bars(db, ["600519"], trade_date=DAY)[-1]["close"] == 110.0


@pytest.mark.parametrize("bars", [[_bar(0)], [_bar(4), _bar(3), _bar(1), _bar(0)]])
def test_sync_reports_unknown_listing_or_suspension_gaps_without_fabricating_bars(db, bars):
    provider = Provider(bars)

    result = sync_daily_bar_cache(db, provider, ["600519"], as_of=DAY, available_at=CUTOFF, bootstrap_limit=5)

    assert result["status"] == "degraded"
    assert result["failed_codes"] == 0
    assert result["incomplete_codes"] == 1
    assert result["missing_open_days"] == 5 - len(bars)
    assert db.scalar(select(func.count()).select_from(DailyBarCache)) == len(bars)


def test_sync_does_not_assume_missing_calendar_is_a_complete_window(db):
    db.execute(TradingCalendar.__table__.delete())
    db.commit()
    provider = Provider([_bar(offset) for offset in range(4, -1, -1)])

    result = sync_daily_bar_cache(db, provider, ["600519"], as_of=DAY, available_at=CUTOFF, bootstrap_limit=5)

    assert result["status"] == "degraded"
    assert result["incomplete_codes"] == 1
    assert result["missing_open_days"] == 0
    assert provider.calls == [("600519", 5)]


def test_sync_keeps_raw_and_qfq_separate_and_rejects_future_availability(db):
    upsert_daily_bars(db, [_bar(0, close=90.0)])
    db.commit()
    future = _bar(0, close=999.0, adjustment="RAW", available_at=CUTOFF + timedelta(minutes=1))
    provider = Provider([_bar(1, close=95.0, adjustment="RAW"), future])

    result = sync_daily_bar_cache(
        db, provider, ["600519"], as_of=DAY, available_at=CUTOFF, bootstrap_limit=2, adjustment="RAW",
    )

    assert result["status"] == "degraded"
    assert result["missing_open_days"] == 1
    assert load_daily_bars(db, ["600519"], adjustment="QFQ")[-1]["close"] == 90.0
    raw = load_daily_bars(db, ["600519"], trade_date=DAY, available_at=CUTOFF, adjustment="RAW")
    assert [(bar["trade_date"], bar["close"]) for bar in raw] == [(DAY - timedelta(days=1), 95.0)]


def test_sync_never_relabels_unmarked_qfq_output_as_raw(db):
    raw = _bar(0)
    raw.pop("adjustment")
    result = sync_daily_bar_cache(
        db, Provider([raw]), ["600519"], as_of=DAY, available_at=CUTOFF, bootstrap_limit=1, adjustment="RAW",
    )

    assert result["failed_codes"] == 1
    assert result["status"] == "degraded"
    assert db.scalar(select(func.count()).select_from(DailyBarCache)) == 0


def test_sync_does_not_require_unfinished_today_in_finalized_window(db):
    before_close = datetime(2026, 9, 7, 6, 30, tzinfo=UTC)
    upsert_daily_bars(db, [_bar(offset) for offset in range(1, 5)])
    db.commit()
    provider = Provider([_bar(offset) for offset in range(4, 0, -1)])

    result = sync_daily_bar_cache(
        db, provider, ["600519"], as_of=DAY, available_at=before_close, bootstrap_limit=4,
    )

    assert result["status"] == "ready"
    assert result["missing_open_days"] == 0
    assert provider.calls == [("600519", 2)]


def test_unfinished_current_row_does_not_displace_finalized_window(db):
    before_close = datetime(2026, 9, 7, 6, 30, tzinfo=UTC)
    provisional = _bar(0, available_at=before_close)
    upsert_daily_bars(db, [*[_bar(offset) for offset in range(1, 5)], provisional])
    db.commit()
    provider = Provider([_bar(1), provisional])

    result = sync_daily_bar_cache(
        db, provider, ["600519"], as_of=DAY, available_at=before_close, bootstrap_limit=4,
    )

    assert result["status"] == "ready"
    assert result["missing_open_days"] == 0
