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

from app.market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot, MarketScoreSnapshot
from app.market_models import SecurityMaster, TradingCalendar
from app.routers.market_engine_v3 import MarketCalculateRequest
from app.services.market_engine import MarketEngine


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    for model in (
        SecurityMaster,
        TradingCalendar,
        MarketMetricSnapshot,
        MarketScoreSnapshot,
        AllAMedianIndexDaily,
    ):
        model.__table__.create(engine)
    return Session(engine)


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
        assert breadth["normalized_metrics"]["median_return"] == pytest.approx(2 / 3 * 100)
        assert profitability["normalized_metrics"]["median_return"] == pytest.approx(2 / 3 * 100)
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
