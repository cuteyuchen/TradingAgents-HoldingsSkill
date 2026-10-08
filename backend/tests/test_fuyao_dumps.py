"""Fuyao dump and corporate-action import contracts."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.history.models import SecurityLifecycleEvent
from app.market.providers.fuyao_client import FuyaoResponse
from app.market_engine_models import DailyBarCache
from app.services.fuyao_market_dumps import (
    DumpValidationError,
    import_daily_k_dump,
    persist_fuyao_corporate_actions,
    stage_market_dump,
)


def daily_rows(_path):
    return iter([
        {
            "thscode": "600519.SH",
            "date_ms": 1784160000000,
            "open_price": 1500,
            "high_price": 1600,
            "low_price": 1490,
            "close_price": 1580,
            "volume": 10,
            "turnover": 20,
        }
    ])


class DownloadClient:
    configured = True

    def get(self, _path, *, params=None, capability=None):
        return FuyaoResponse(
            code=0,
            message="success",
            request_id="req-download-1",
            endpoint="/api/dump/market-dumps/daily-k/download-url",
            latency_ms=1.0,
            attempts=1,
            data={"download_url": "https://example.invalid/daily.parquet"},
        )


def test_dump_is_validated_before_atomic_replace(tmp_path):
    target = tmp_path / "daily.parquet"

    def downloader(_url, path, _timeout):
        path.write_bytes(b"PAR1fixturePAR1")

    manifest = stage_market_dump("daily_k", target, client=DownloadClient(), downloader=downloader, reader=daily_rows)

    assert target.read_bytes() == b"PAR1fixturePAR1"
    assert manifest["row_count"] == 1
    assert manifest["dump_id"] == "a_share_daily_k_1d_none_10y"
    assert not list(tmp_path.glob("*.part"))


def test_failed_dump_validation_keeps_previous_file(tmp_path):
    target = tmp_path / "daily.parquet"
    target.write_bytes(b"previous")

    def downloader(_url, path, _timeout):
        path.write_bytes(b"PAR1partialPAR1")

    def invalid_reader(_path):
        return iter([{"thscode": "600519.SH", "date_ms": 1784160000000}])

    with pytest.raises(DumpValidationError, match="schema_missing"):
        stage_market_dump("daily_k", target, client=DownloadClient(), downloader=downloader, reader=invalid_reader)

    assert target.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.part"))


def test_daily_dump_reuses_existing_daily_bar_cache_without_claiming_qfq(tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    DailyBarCache.__table__.create(engine)
    db = sessionmaker(bind=engine, future=True)()
    path = tmp_path / "daily.parquet"
    path.write_bytes(b"fixture")

    result = import_daily_k_dump(db, path, reader=daily_rows)
    row = db.query(DailyBarCache).one()

    assert result["imported_rows"] == 1
    assert row.provider == "fuyao_dump"
    assert row.adjustment == "NONE"
    assert row.available_at is not None
    assert row.metadata_json["dump_id"] == "a_share_daily_k_1d_none_10y"


def test_corporate_actions_are_idempotent_raw_events_with_unknown_pit():
    engine = create_engine("sqlite:///:memory:", future=True)
    SecurityLifecycleEvent.__table__.create(engine)
    db = sessionmaker(bind=engine, future=True)()
    response = FuyaoResponse(
        code=0,
        message="success",
        request_id="req-action-1",
        endpoint="/api/a-share/corporate-actions/adjustment-factors",
        latency_ms=1.0,
        attempts=1,
        data={
            "item": [{
                "thscode": "600519.SH",
                "ticker": "600519",
                "ex_date_ms": 1784160000000,
                "dividend_per_share": 1.0,
                "per_share_bonus": 0.1,
                "allotment_ratio": None,
                "allotment_price": None,
            }]
        },
    )

    first = persist_fuyao_corporate_actions(db, response, captured_at=datetime(2026, 7, 20, tzinfo=UTC))
    second = persist_fuyao_corporate_actions(db, response, captured_at=datetime(2026, 7, 21, tzinfo=UTC))
    row = db.query(SecurityLifecycleEvent).one()

    assert first == {"inserted": 1, "updated": 0, "skipped": 0}
    assert second == {"inserted": 0, "updated": 1, "skipped": 0}
    assert row.source == "fuyao"
    assert row.source_lineage_json["request_id"] == "req-action-1"
    assert row.source_lineage_json["pit_status"] == "HISTORICAL_PIT_UNKNOWN"
