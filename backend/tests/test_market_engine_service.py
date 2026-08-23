from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import os
import sys

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from pydantic import ValidationError

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.market.engine import ComponentScore
from app.market_engine_models import AllAMedianIndexDaily, DailyBarCache, MarketMetricSnapshot, MarketScoreSnapshot
from app.market_models import SecurityMaster, TradingCalendar
from app.market_runtime_models import MarketSnapshot, SourceLineage
from app.routers.market_engine_v3 import MarketCalculateRequest
from app.services.daily_bar_cache import sync_daily_bar_cache
from app.services.market_engine import MarketEngine, _component_confidence, _preview_median_index
from app.services.market_snapshot_service import build_quote_snapshot


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    for model in (
        SecurityMaster,
        TradingCalendar,
        MarketMetricSnapshot,
        MarketScoreSnapshot,
        MarketSnapshot,
        SourceLineage,
        AllAMedianIndexDaily,
        DailyBarCache,
    ):
        model.__table__.create(engine)
    return Session(engine)


def test_overall_component_confidence_uses_component_confidence_values() -> None:
    components = {
        "breadth": ComponentScore("breadth", 60, confidence=100),
        "trend": ComponentScore("trend", 60, confidence=100),
        "liquidity": ComponentScore("liquidity", 60, confidence=10),
        "profitability": ComponentScore("profitability", 60, confidence=100),
        "diffusion": ComponentScore("diffusion", 60, confidence=100),
        "crowding": ComponentScore("crowding", 60, confidence=100),
        "tail_risk": ComponentScore("tail_risk", 60, confidence=100),
    }
    assert _component_confidence(components) == pytest.approx(86.5)


def test_quote_trade_date_must_match_calculation_date() -> None:
    db = _session()
    try:
        day = date(2026, 8, 23)
        with pytest.raises(ValueError, match="quote_trade_date_mismatch"):
            MarketEngine(db).calculate(
                trade_date=day,
                captured_at=datetime(2026, 8, 23, 1, 35, tzinfo=UTC),
                securities=[
                    {
                        "code": "600519",
                        "exchange": "SSE",
                        "security_type": "STOCK",
                        "listing_date": day - timedelta(days=400),
                    }
                ],
                trading_calendar=[
                    {"trade_date": day - timedelta(days=offset), "is_open": True}
                    for offset in range(400, -1, -1)
                ],
                quotes=[
                    {
                        "code": "600519",
                        "trade_date": day - timedelta(days=1),
                        "price": 102,
                        "prev_close": 100,
                        "quality_status": "VALID",
                    }
                ],
                persist=False,
            )
    finally:
        db.close()


def test_outage_uses_last_reliable_score_and_preview_does_not_persist() -> None:
    db = _session()
    try:
        captured_at = datetime(2026, 8, 21, 7, 35, tzinfo=UTC)
        db.add(
            MarketScoreSnapshot(
                snapshot_id="reliable-1",
                market="CN",
                trade_date=date(2026, 8, 21),
                captured_at=captured_at,
                display_score=68,
                raw_score=68,
                regime="RISK_ON",
                confidence=92,
                quality_status="VALID",
                is_frozen=False,
            )
        )
        # A replay/outage calculation must not borrow this future snapshot.
        db.add(
            MarketScoreSnapshot(
                snapshot_id="reliable-future",
                market="CN",
                trade_date=date(2026, 8, 23),
                captured_at=datetime(2026, 8, 23, 3, 35, tzinfo=UTC),
                display_score=92,
                raw_score=92,
                regime="STRONG_RISK_ON",
                confidence=95,
                quality_status="VALID",
                is_frozen=False,
            )
        )
        db.commit()

        trade_date = date(2026, 8, 23)
        securities = [
            {
                "code": "600519",
                "exchange": "SSE",
                "security_type": "STOCK",
                "listing_date": trade_date - timedelta(days=30),
            }
        ]
        calendar = [
            {"trade_date": trade_date - timedelta(days=offset), "is_open": True}
            for offset in range(30, -1, -1)
        ]

        result = MarketEngine(db).calculate(
            trade_date=trade_date,
            captured_at=datetime(2026, 8, 23, 1, 35, tzinfo=UTC),
            securities=securities,
            trading_calendar=calendar,
            quotes=[],
            persist=False,
        )

        assert result["display_score"] == 68
        assert result["is_frozen"] is True
        assert result["quality_status"] == "FROZEN"
        assert result["freeze_reason"] == "data_quality"
        assert db.scalar(select(func.count()).select_from(MarketMetricSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(AllAMedianIndexDaily)) == 0
        assert db.scalar(select(func.count()).select_from(MarketScoreSnapshot)) == 2
    finally:
        db.close()


def test_market_calculate_api_rejects_client_owned_market_inputs() -> None:
    """Raw universe/quote/history rows remain a service-test-only seam."""

    with pytest.raises(ValidationError):
        MarketCalculateRequest(quotes=[{"code": "600519", "price": 1}])


def test_same_capture_persist_is_idempotent() -> None:
    db = _session()
    try:
        trade_date = date(2026, 8, 23)
        captured_at = datetime(2026, 8, 23, 1, 35, tzinfo=UTC)
        securities = [
            {
                "code": "600519",
                "exchange": "SSE",
                "security_type": "STOCK",
                "listing_date": trade_date - timedelta(days=30),
            }
        ]
        calendar = [
            {"trade_date": trade_date - timedelta(days=offset), "is_open": True}
            for offset in range(30, -1, -1)
        ]
        quotes = [
            {
                "code": "600519",
                "price": 100,
                "prev_close": 99,
                "amount": 100_000,
                "captured_at": captured_at,
                "quality_status": "VALID",
            }
        ]
        engine = MarketEngine(db)
        first = engine.calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            persist=True,
        )
        second = engine.calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            persist=True,
        )

        assert second["snapshot_id"] == first["snapshot_id"]
        assert second["metric_snapshot_id"] == first["metric_snapshot_id"]
        assert db.scalar(select(func.count()).select_from(MarketMetricSnapshot)) == 1
        assert db.scalar(select(func.count()).select_from(MarketScoreSnapshot)) == 1
        # Intraday captures expose a preview only; the official daily row is
        # finalized once the 15:00 CST close has passed.
        assert db.scalar(select(func.count()).select_from(AllAMedianIndexDaily)) == 0

        # This deliberately tiny fixture has insufficient component coverage,
        # so a close-time run remains frozen and must not finalize the series.
        finalized = engine.calculate(
            trade_date=trade_date,
            captured_at=datetime(2026, 8, 23, 7, 35, tzinfo=UTC),
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            persist=True,
        )
        assert finalized["median_index_finalized"] is False
        assert db.scalar(select(func.count()).select_from(AllAMedianIndexDaily)) == 0
    finally:
        db.close()


def test_close_persists_median_index_when_quality_passes() -> None:
    db = _session()
    try:
        trade_date, _, securities, calendar, quotes, history = _coverage_fixture(100, universe_size=8)
        captured_at = datetime(2026, 8, 23, 7, 35, tzinfo=UTC)
        result = MarketEngine(db).calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            history=history,
            persist=True,
        )
        assert result["median_index_finalized"] is True
        assert db.scalar(select(func.count()).select_from(AllAMedianIndexDaily)) == 1
    finally:
        db.close()


def test_degraded_provider_quality_is_not_upgraded_by_full_coverage() -> None:
    db = _session()
    try:
        trade_date = date(2026, 8, 23)
        captured_at = datetime(2026, 8, 23, 1, 35, tzinfo=UTC)
        result = MarketEngine(db).calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=[
                {
                    "code": "600519",
                    "exchange": "SSE",
                    "security_type": "STOCK",
                    "listing_date": trade_date - timedelta(days=30),
                }
            ],
            trading_calendar=[
                {"trade_date": trade_date - timedelta(days=offset), "is_open": True}
                for offset in range(30, -1, -1)
            ],
            quotes={
                "quality_status": "DEGRADED",
                "quotes": [
                    {
                        "code": "600519",
                        "price": 102,
                        "prev_close": 100,
                        "amount": 100_000,
                        "captured_at": captured_at,
                        "quality_status": "VALID",
                    }
                ],
            },
            history=[
                {
                    "code": "600519",
                    "trade_date": trade_date - timedelta(days=offset),
                    "close": 100 + (60 - offset),
                    "adjustment": "QFQ",
                    "quality_status": "VALID",
                    "available_at": captured_at - timedelta(days=offset),
                }
                for offset in range(60, -1, -1)
            ],
            persist=False,
        )

        assert result["quality_status"] == "DEGRADED"
        assert result["is_frozen"] is False
    finally:
        db.close()


def test_component_percentile_uses_prior_daily_snapshots_without_lookahead() -> None:
    db = _session()
    try:
        trade_date = date(2026, 8, 23)
        captured_at = datetime(2026, 8, 23, 1, 35, tzinfo=UTC)

        def metric_row(
            snapshot_id: str,
            row_date: date,
            row_capture: datetime,
            median_return: float,
        ) -> MarketMetricSnapshot:
            return MarketMetricSnapshot(
                snapshot_id=snapshot_id,
                market="CN",
                trade_date=row_date,
                captured_at=row_capture,
                quality_status="VALID",
                metrics_json={"all_a_median_return": median_return},
            )

        db.add_all(
            [
                metric_row("history-1", trade_date - timedelta(days=3), captured_at - timedelta(days=3), 0.01),
                metric_row("history-2", trade_date - timedelta(days=2), captured_at - timedelta(days=2), 0.02),
                # Only the latest capture from this trading day may enter the percentile sample.
                metric_row("history-3-early", trade_date - timedelta(days=1), captured_at - timedelta(days=1, hours=1), -1.0),
                metric_row("history-3-latest", trade_date - timedelta(days=1), captured_at - timedelta(days=1), 0.03),
                # A row recorded after the replay point must never leak into historical normalization.
                metric_row("history-future-capture", trade_date - timedelta(days=4), captured_at + timedelta(hours=1), -1.0),
            ]
        )
        db.commit()

        result = MarketEngine(db).calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=[
                {
                    "code": "600519",
                    "exchange": "SSE",
                    "security_type": "STOCK",
                    "listing_date": trade_date - timedelta(days=30),
                }
            ],
            trading_calendar=[
                {"trade_date": trade_date - timedelta(days=offset), "is_open": True}
                for offset in range(30, -1, -1)
            ],
            quotes=[
                {
                    "code": "600519",
                    "price": 102,
                    "prev_close": 100,
                    "amount": 100_000,
                    "captured_at": captured_at,
                    "quality_status": "VALID",
                }
            ],
            persist=False,
        )

        breadth = result["components"]["breadth"]
        profitability = result["components"]["profitability"]
        # Three prior daily samples are intentionally below the 60-sample
        # threshold, so historical percentile is unavailable and the
        # component uses its deterministic fallback instead.
        assert breadth["normalized_metrics"]["median_return"] is None
        assert breadth["raw_metrics"]["historical_scoring"]["all_a_median_return"]["used_historical_percentile"] is False
        assert profitability["normalized_metrics"]["median_return"] is not None
        assert breadth["historical_sample_count"] == 3
    finally:
        db.close()


def _coverage_fixture(quote_count: int, universe_size: int = 100):
    trade_date = date(2026, 8, 23)
    captured_at = datetime(2026, 8, 23, 1, 35, tzinfo=UTC)
    securities = [
        {
            "code": f"{600000 + index:06d}",
            "exchange": "SSE",
            "security_type": "STOCK",
            "listing_date": trade_date - timedelta(days=400),
        }
        for index in range(universe_size)
    ]
    calendar = [
        {"trade_date": trade_date - timedelta(days=offset), "is_open": True}
        for offset in range(400, -1, -1)
    ]
    quotes = [
        {
            "code": f"{600000 + index:06d}",
            "price": 101 + (index % 5) * 0.1,
            "prev_close": 100,
            "amount": 10_000 + index,
            "captured_at": captured_at,
            "quality_status": "VALID",
        }
        for index in range(quote_count)
    ]
    history = []
    for index in range(universe_size):
        code = f"{600000 + index:06d}"
        history.extend(
            {
                "code": code,
                "trade_date": trade_date - timedelta(days=offset),
                "close": 100 + (65 - offset) * 0.05,
                "adjustment": "QFQ",
                "quality_status": "VALID",
            }
            for offset in range(65, -1, -1)
        )
    return trade_date, captured_at, securities, calendar, quotes, history


def test_coverage_tiers_match_quality_gate() -> None:
    db = _session()
    try:
        db.add(
            MarketScoreSnapshot(
                snapshot_id="reliable-coverage",
                market="CN",
                trade_date=date(2026, 8, 21),
                captured_at=datetime(2026, 8, 21, 7, 35, tzinfo=UTC),
                display_score=68,
                raw_score=68,
                regime="RISK_ON",
                confidence=90,
                quality_status="VALID",
                is_frozen=False,
            )
        )
        db.commit()
        engine = MarketEngine(db)

        valid_date, valid_capture, securities, calendar, quotes, history = _coverage_fixture(99)
        valid = engine.calculate(
            trade_date=valid_date,
            captured_at=valid_capture,
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            history=history,
            persist=False,
        )
        assert valid["quality_status"] == "VALID"
        assert valid["is_frozen"] is False
        assert valid["raw_score"] is not None

        degraded_date, degraded_capture, securities, calendar, quotes, history = _coverage_fixture(96)
        degraded = engine.calculate(
            trade_date=degraded_date,
            captured_at=degraded_capture,
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            history=history,
            persist=False,
        )
        assert degraded["quality_status"] == "DEGRADED"
        assert degraded["is_frozen"] is False
        assert degraded["raw_score"] is not None

        frozen_date, frozen_capture, securities, calendar, quotes, history = _coverage_fixture(94)
        frozen = engine.calculate(
            trade_date=frozen_date,
            captured_at=frozen_capture,
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            history=history,
            persist=False,
        )
        assert frozen["quality_status"] == "FROZEN"
        assert frozen["is_frozen"] is True
        assert frozen["display_score"] == pytest.approx(68)
        assert frozen["raw_score"] is None
        assert frozen["freeze_reason"] == "data_quality"
        assert not db.new
        assert not db.dirty
    finally:
        db.close()


def test_preview_does_not_mutate_session_with_valid_quotes() -> None:
    db = _session()
    try:
        trade_date, captured_at, securities, calendar, quotes, history = _coverage_fixture(100, universe_size=8)
        result = MarketEngine(db).calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            history=history,
            persist=False,
        )
        assert result["is_frozen"] is False
        assert db.scalar(select(func.count()).select_from(MarketMetricSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(MarketScoreSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(AllAMedianIndexDaily)) == 0
        assert not db.new
        assert not db.dirty
    finally:
        db.close()


def test_market_calculate_request_forbids_derived_provenance_fields() -> None:
    with pytest.raises(ValidationError):
        MarketCalculateRequest(coverage=0.01)
    with pytest.raises(ValidationError):
        MarketCalculateRequest(fallback_level=99)
    with pytest.raises(ValidationError):
        MarketCalculateRequest(expected_count=1)
    with pytest.raises(ValidationError):
        MarketCalculateRequest(provider_endpoint="https://fake.example/quotes")
    with pytest.raises(ValidationError):
        MarketCalculateRequest(securities=[{"code": "600519"}])


def test_median_index_preview_ignores_same_day_daily_row() -> None:
    db = _session()
    try:
        day = date(2026, 8, 23)
        db.add_all(
            [
                AllAMedianIndexDaily(
                    market="CN",
                    trade_date=day - timedelta(days=1),
                    median_return=0.01,
                    index_value=1000.0,
                    eligible_count=100,
                    quality_status="VALID",
                    calculation_version="market-engine-v1.1",
                ),
                AllAMedianIndexDaily(
                    market="CN",
                    trade_date=day,
                    median_return=0.01,
                    index_value=1010.0,
                    eligible_count=100,
                    quality_status="VALID",
                    calculation_version="market-engine-v1.1",
                ),
            ]
        )
        db.commit()
        assert _preview_median_index(db, trade_date=day, median_return=0.02) == pytest.approx(1020.0)
    finally:
        db.close()


def test_market_engine_reads_daily_bar_cache_without_provider_fanout() -> None:
    db = _session()
    try:
        trade_date, captured_at, securities, calendar, quotes, history = _coverage_fixture(8, universe_size=8)
        db.add_all(
            [
                DailyBarCache(
                    market="CN",
                    exchange="SSE",
                    code=row["code"],
                    trade_date=row["trade_date"],
                    close=row["close"],
                    adjustment="QFQ",
                    provider="fixture",
                    fetched_at=captured_at,
                    available_at=datetime(
                        row["trade_date"].year,
                        row["trade_date"].month,
                        row["trade_date"].day,
                        7,
                        tzinfo=UTC,
                    ),
                    quality_status="VALID",
                )
                for row in history
                if row["trade_date"] < trade_date
            ]
        )
        db.commit()

        class ExplodingProvider:
            def get_kline(self, *_args, **_kwargs):
                raise AssertionError("calculation must not call the remote provider")

        result = MarketEngine(db, history_provider=ExplodingProvider()).calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=securities,
            trading_calendar=calendar,
            quotes=quotes,
            persist=False,
        )
        assert result["is_frozen"] is False
        assert result["core_metrics"]["ma60_eligible_count"] == 8
    finally:
        db.close()


def test_daily_bar_cache_sync_is_incremental() -> None:
    db = _session()
    try:
        day = date(2026, 8, 23)
        db.add_all(
            [
                TradingCalendar(market="CN", trade_date=day - timedelta(days=offset), is_open=True)
                for offset in range(3)
            ]
        )
        db.add(
            DailyBarCache(
                market="CN",
                exchange="SSE",
                code="600519",
                trade_date=day - timedelta(days=2),
                close=100.0,
                adjustment="QFQ",
                provider="fixture",
                fetched_at=datetime(2026, 8, 21, 8, tzinfo=UTC),
                quality_status="VALID",
            )
        )
        db.commit()

        class Provider:
            name = "fixture"

            def __init__(self):
                self.calls = []

            def get_kline(self, code, *, limit=30):
                self.calls.append((code, limit))
                return [
                    {
                        "code": code,
                        "date": (day - timedelta(days=offset)).isoformat(),
                        "close": 103 - offset,
                        "quality_status": "VALID",
                    }
                    for offset in range(3, -1, -1)
                ]

        provider = Provider()
        result = sync_daily_bar_cache(
            db,
            provider,
            ["600519"],
            as_of=day,
            available_at=datetime(2026, 8, 23, 23, tzinfo=UTC),
        )
        assert provider.calls == [("600519", 4)]
        assert result["persisted_rows"] == 2
        assert db.scalar(select(func.count()).select_from(DailyBarCache)) == 3
    finally:
        db.close()


def test_capture_span_reduces_confidence() -> None:
    def calculate_with_span(span_seconds: int) -> float:
        db = _session()
        try:
            trade_date, captured_at, securities, calendar, quotes, history = _coverage_fixture(8, universe_size=8)
            result = MarketEngine(db).calculate(
                trade_date=trade_date,
                captured_at=captured_at,
                securities=securities,
                trading_calendar=calendar,
                quotes={
                    "quality_status": "VALID",
                    "started_at": captured_at - timedelta(seconds=span_seconds),
                    "completed_at": captured_at,
                    "quotes": quotes,
                },
                history=history,
                persist=False,
            )
            return result["confidence"]
        finally:
            db.close()

    short_confidence = calculate_with_span(15)
    long_confidence = calculate_with_span(60)
    assert long_confidence == pytest.approx(short_confidence / 2, abs=0.1)


def test_persisted_market_score_is_linked_to_quote_snapshot_lineage(monkeypatch) -> None:
    db = _session()
    try:
        trade_date, captured_at, securities, calendar, quotes, history = _coverage_fixture(8, universe_size=8)
        source_time = datetime(2026, 8, 23, 1, 35, tzinfo=UTC)
        raw = build_quote_snapshot(
            {
                "quotes": [
                    dict(row, source_timestamp=source_time, fetched_at=source_time)
                    for row in quotes
                ],
                "provider": "fixture",
                "started_at": source_time - timedelta(seconds=2),
                "completed_at": source_time,
            },
            requested_codes=[row["code"] for row in securities],
            trade_date=trade_date,
        )
        monkeypatch.setattr(
            "app.services.market_engine.get_all_a_share_quote_snapshot",
            lambda _db, **_kwargs: raw,
        )
        result = MarketEngine(db).calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=securities,
            trading_calendar=calendar,
            history=history,
            persist=True,
        )
        assert result["market_snapshot_id"] == result["source_snapshot_id"]
        assert db.scalar(select(func.count()).select_from(MarketSnapshot)) == 1
        assert db.scalar(select(func.count()).select_from(SourceLineage)) >= 1
        metric = db.scalar(select(MarketMetricSnapshot).where(MarketMetricSnapshot.snapshot_id == result["metric_snapshot_id"]))
        score = db.scalar(select(MarketScoreSnapshot).where(MarketScoreSnapshot.snapshot_id == result["snapshot_id"]))
        snapshot = db.scalar(select(MarketSnapshot).where(MarketSnapshot.snapshot_id == result["market_snapshot_id"]))
        assert metric is not None and metric.market_snapshot_id == snapshot.snapshot_id
        assert score is not None and score.metric_snapshot_id == metric.snapshot_id
        assert db.query(SourceLineage).filter_by(entity_type="market_snapshot", entity_key=snapshot.snapshot_id).count() >= 1

        preview_db = _session()
        try:
            preview = MarketEngine(preview_db).calculate(
                trade_date=trade_date,
                captured_at=captured_at,
                securities=securities,
                trading_calendar=calendar,
                history=history,
                persist=False,
            )
            assert preview["source_snapshot_id"] == raw["snapshot_id"]
            assert preview_db.scalar(select(func.count()).select_from(MarketSnapshot)) == 0
            assert preview_db.scalar(select(func.count()).select_from(SourceLineage)) == 0
        finally:
            preview_db.close()
    finally:
        db.close()


def test_1510_provider_close_snapshot_does_not_freeze_or_block_median_finalize(monkeypatch) -> None:
    db = _session()
    try:
        trade_date, _, securities, calendar, quotes, history = _coverage_fixture(8, universe_size=8)
        captured_at = datetime(2026, 8, 23, 7, 10, tzinfo=UTC)
        source_time = datetime(2026, 8, 23, 7, 0, tzinfo=UTC)
        raw = build_quote_snapshot(
            {
                "quotes": [
                    dict(row, source_timestamp=source_time, fetched_at=captured_at)
                    for row in quotes
                ],
                "provider": "fixture",
                "started_at": source_time,
                "completed_at": captured_at,
            },
            requested_codes=[row["code"] for row in securities],
            trade_date=trade_date,
        )
        monkeypatch.setattr(
            "app.services.market_engine.get_all_a_share_quote_snapshot",
            lambda _db, **_kwargs: raw,
        )
        result = MarketEngine(db).calculate(
            trade_date=trade_date,
            captured_at=captured_at,
            securities=securities,
            trading_calendar=calendar,
            history=history,
            persist=True,
        )
        assert result["quality_status"] in {"VALID", "DEGRADED"}
        assert result["is_frozen"] is False
        assert result["median_index_finalized"] is True
        assert db.scalar(select(func.count()).select_from(AllAMedianIndexDaily)) == 1
    finally:
        db.close()
