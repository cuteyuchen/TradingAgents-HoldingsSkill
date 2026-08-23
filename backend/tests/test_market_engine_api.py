"""Authenticated Market Engine API contract tests."""
from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("ADVISOR_TOKEN", "test_token_xxx")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("SCHEDULER_ENABLED", "false")


def _engine_session_factory():
    from app.market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot, MarketScoreSnapshot
    from app.market_models import SecurityMaster, TradingCalendar

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    for model in (
        SecurityMaster,
        TradingCalendar,
        MarketMetricSnapshot,
        MarketScoreSnapshot,
        AllAMedianIndexDaily,
    ):
        model.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_identity(db: Session, *, trade_date: date) -> None:
    from app.market_models import SecurityMaster, TradingCalendar

    db.add(
        SecurityMaster(
            market="CN",
            exchange="SSE",
            code="600519",
            security_type="STOCK",
            listing_date=trade_date - timedelta(days=400),
            status="ACTIVE",
        )
    )
    db.add_all(
        [
            TradingCalendar(
                market="CN",
                trade_date=trade_date - timedelta(days=offset),
                is_open=True,
            )
            for offset in range(400, -1, -1)
        ]
    )
    db.commit()


def test_market_engine_api_requires_auth() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/api/v3/market/state").status_code == 401
    assert client.get("/api/v3/market/metrics").status_code == 401
    assert client.get("/api/v3/market/median-index").status_code == 401
    assert client.get("/api/v3/market/state/history").status_code == 401
    assert client.post("/api/v3/market/calculate", json={}).status_code == 401


def test_market_engine_api_calculate_and_read_paths(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.market_engine_models import AllAMedianIndexDaily, MarketMetricSnapshot, MarketScoreSnapshot
    from pydantic import ValidationError

    from app.routers.market_engine_v3 import MarketCalculateRequest
    from app.services import market_engine as market_engine_service
    from app.v2_dependencies import get_current_user

    SessionLocal = _engine_session_factory()
    trade_date = date(2026, 8, 23)
    captured_at = datetime(2026, 8, 23, 1, 35, tzinfo=UTC)
    with SessionLocal() as db:
        _seed_identity(db, trade_date=trade_date)

    def fake_snapshot(_db, **_kwargs):
        return {
            "quality_status": "VALID",
            "expected_count": 1,
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
        }

    def fake_kline(self, code, *, limit=30):
        return [
            {
                "date": (trade_date - timedelta(days=offset)).isoformat(),
                "open": 100,
                "close": 100 + (65 - offset) * 0.1,
                "high": 101,
                "low": 99,
                "volume": 1000,
            }
            for offset in range(65, -1, -1)
        ]

    monkeypatch.setattr(market_engine_service, "get_all_a_share_quote_snapshot", fake_snapshot)
    monkeypatch.setattr(
        "app.market.engine.history.LegacyMarketDataHistoryProvider.get_kline",
        fake_kline,
    )

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: object()
    client = TestClient(app)
    try:
        empty = client.get("/api/v3/market/state")
        assert empty.status_code == 404

        rejected = client.post(
            "/api/v3/market/calculate",
            json={
                "quotes": [{"code": "600519", "price": 1}],
                "coverage": 0.01,
                "fallback_level": 99,
                "expected_count": 1,
                "provider_endpoint": "https://fake.example/quotes",
            },
        )
        assert rejected.status_code == 422

        with pytest.raises(ValidationError):
            MarketCalculateRequest(quotes=[{"code": "600519"}])

        created = client.post(
            "/api/v3/market/calculate",
            json={"trade_date": trade_date.isoformat(), "captured_at": captured_at.isoformat(), "persist": True},
        )
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["universe"]["included_count"] == 1
        assert payload["quality_status"] in {"VALID", "DEGRADED"}
        assert payload["raw_score"] is not None

        state = client.get("/api/v3/market/state")
        assert state.status_code == 200, state.text
        assert state.json()["snapshot_id"] == payload["snapshot_id"]

        metrics = client.get("/api/v3/market/metrics")
        assert metrics.status_code == 200, metrics.text
        assert metrics.json()["universe"]["included"] == 1

        history = client.get("/api/v3/market/state/history")
        assert history.status_code == 200
        assert history.json()[0]["snapshot_id"] == payload["snapshot_id"]

        median = client.get("/api/v3/market/median-index")
        assert median.status_code == 200
        assert median.json()[0]["trade_date"] == trade_date.isoformat()

        preview = client.post(
            "/api/v3/market/calculate",
            json={
                "trade_date": trade_date.isoformat(),
                "captured_at": datetime(2026, 8, 23, 2, 35, tzinfo=UTC).isoformat(),
                "persist": False,
            },
        )
        assert preview.status_code == 200, preview.text

        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(MarketScoreSnapshot)) == 1
            assert db.scalar(select(func.count()).select_from(MarketMetricSnapshot)) == 1
            assert db.scalar(select(func.count()).select_from(AllAMedianIndexDaily)) == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
