"""Phase L point-in-time historical data foundation contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.database import Base
from app.history.availability import history_manifest_items, pit_recompute_gate
from app.history.coverage import historical_data_coverage
from app.history.models import (
    EtfMetadataHistory,
    FundamentalReport,
    HistoricalDataSyncRun,
    PriceBasisMetadata,
    SecurityClassificationDaily,
    SecurityLifecycleEvent,
    SecurityTradingStatusDaily,
    SecurityValuationDaily,
)
from app.history.sync import (
    claim_history_sync_run,
    complete_history_sync_run,
    enqueue_history_sync,
    execute_history_sync_run,
    import_historical_facts,
    reclaim_stale_history_sync_runs,
    run_history_sync,
)
from app.history.universe import (
    resolve_equity_universe,
    resolve_etf_metadata,
    resolve_fundamental,
    resolve_historical_holdings,
    resolve_security_state,
    resolve_special_treatment,
    resolve_valuation,
)
from app.market_models import TradingCalendar
from app.research.availability import build_replay_availability_manifest
from app.research.replay import ReplayDataQualityError, load_replay_facts
from app.v2_models import HoldingItem, Portfolio, PortfolioSnapshot, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    import app.candidates.models  # noqa: F401
    import app.governance.models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.portfolio_models  # noqa: F401
    import app.trigger_models  # noqa: F401
    import app.research.models  # noqa: F401
    import app.history.models  # noqa: F401

    Base.metadata.create_all(engine)
    session = Session(engine)
    session._engine = engine  # type: ignore[attr-defined]
    return session


def _calendar(db: Session, days: list[date]) -> None:
    for index, day in enumerate(days):
        db.add(TradingCalendar(
            market="CN",
            trade_date=day,
            is_open=True,
            previous_trade_date=days[index - 1] if index else None,
            next_trade_date=days[index + 1] if index + 1 < len(days) else None,
        ))
    db.flush()


def _business_days(start: date, count: int) -> list[date]:
    result: list[date] = []
    day = start
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day += timedelta(days=1)
    return result


def _lifecycle(
    db: Session,
    code: str,
    event_type: str,
    effective_date: date,
    *,
    security_type: str = "STOCK",
    exchange: str | None = None,
    source_ref: str | None = None,
) -> None:
    db.add(SecurityLifecycleEvent(
        market="CN",
        exchange=exchange,
        code=code,
        security_type=security_type,
        event_type=event_type,
        effective_date=effective_date,
        source="operator-import",
        source_ref=source_ref or f"lifecycle-{code}-{event_type}-{effective_date}",
        source_available_at=datetime(effective_date.year, effective_date.month, effective_date.day, 9, 0),
        quality_status="VALID",
    ))
    db.flush()


def _classification(
    db: Session,
    code: str,
    trade_date: date,
    classification: str,
    *,
    source_ref: str | None = None,
) -> None:
    db.add(SecurityClassificationDaily(
        market="CN",
        code=code,
        trade_date=trade_date,
        classification=classification,
        source="operator-import",
        source_ref=source_ref or f"class-{code}-{trade_date}-{classification}",
        source_available_at=datetime(trade_date.year, trade_date.month, trade_date.day, 18, 0),
        quality_status="VALID",
    ))
    db.flush()


def _trading_status(
    db: Session,
    code: str,
    trade_date: date,
    status: str,
    *,
    source_ref: str | None = None,
) -> None:
    db.add(SecurityTradingStatusDaily(
        market="CN",
        code=code,
        trade_date=trade_date,
        status=status,
        source="operator-import",
        source_ref=source_ref or f"status-{code}-{trade_date}-{status}",
        source_available_at=datetime(trade_date.year, trade_date.month, trade_date.day, 18, 0),
        quality_status="VALID",
    ))
    db.flush()


def _seed_stock(
    db: Session,
    *,
    code: str,
    day: date,
    classification: str = "NORMAL",
    trading_status: str = "TRADING",
    exchange: str | None = None,
    security_type: str = "STOCK",
    listed_date: date | None = None,
) -> None:
    listed = listed_date or day - timedelta(days=400)
    _lifecycle(
        db,
        code,
        "LISTED",
        listed,
        security_type=security_type,
        exchange=exchange,
    )
    _classification(db, code, day, classification)
    _trading_status(db, code, day, trading_status)


def test_security_lifecycle_pit() -> None:
    db = _db()
    try:
        _lifecycle(db, "600001", "LISTED", date(2025, 1, 1), exchange="SSE")
        _lifecycle(db, "600001", "DELISTED", date(2026, 1, 1), exchange="SSE")
        db.commit()
        active = resolve_security_state(db, "600001", as_of=date(2025, 6, 1))
        assert active["status"] == "ACTIVE"
        assert active["listed_date"] == date(2025, 1, 1)
        delisted = resolve_security_state(db, "600001", as_of=date(2026, 2, 1))
        assert delisted["status"] == "DELISTED"
        assert delisted["delisted_date"] == date(2026, 1, 1)
    finally:
        db.close()


def test_no_current_backfill_for_st() -> None:
    db = _db()
    try:
        day = date(2025, 1, 2)
        _seed_stock(db, code="600000", day=day)
        # The only historical fact is NORMAL; a current ST flag must not leak.
        db.commit()
        treatment = resolve_special_treatment(db, "600000", trade_date=day)
        assert treatment["classification"] == "NORMAL"
        assert treatment["known"] is True
    finally:
        db.close()


def test_unknown_st_lowers_universe_coverage() -> None:
    db = _db()
    try:
        day = date(2025, 1, 2)
        _calendar(db, [day])
        for code in ("600001", "600002"):
            _seed_stock(db, code=code, day=day, classification="NORMAL")
        result = resolve_equity_universe(db, as_of=day, purpose="MARKET_SCORE")
        assert result.total_count == 2
        assert result.known_count == 2
        assert result.status == "FULL"
        # Remove ST facts from one security: UNKNOWN, never treated as NORMAL.
        db.query(SecurityClassificationDaily).filter(
            SecurityClassificationDaily.code == "600002"
        ).delete()
        db.commit()
        partial = resolve_equity_universe(db, as_of=day, purpose="MARKET_SCORE")
        assert partial.unknown_count == 1
        assert partial.status == "PARTIAL"
        assert partial.coverage == 0.5
        assert "600002" not in partial.eligible_codes
    finally:
        db.close()


def test_suspension_excludes_day_and_recovers_next_day() -> None:
    db = _db()
    try:
        days = _business_days(date(2025, 1, 2), 25)
        _calendar(db, days)
        for code in ("600001", "600002"):
            _lifecycle(db, code, "LISTED", days[0])
            for day in days:
                _classification(db, code, day, "NORMAL")
                _trading_status(db, code, day, "TRADING")
        _trading_status(db, "600002", days[20], "SUSPENDED")
        db.commit()
        suspended = resolve_equity_universe(db, as_of=days[20], purpose="MARKET_SCORE")
        assert "600002" not in suspended.eligible_codes
        assert "UNIVERSE_SUSPENDED" in suspended.exclusions["600002"]
        recovered = resolve_equity_universe(db, as_of=days[21], purpose="MARKET_SCORE")
        assert "600002" in recovered.eligible_codes
    finally:
        db.close()


def test_listing_age_uses_trading_days() -> None:
    db = _db()
    try:
        days = _business_days(date(2025, 1, 2), 65)
        _calendar(db, days)
        _lifecycle(db, "600001", "LISTED", days[0], exchange="SSE")
        for day in days:
            _classification(db, "600001", day, "NORMAL")
            _trading_status(db, "600001", day, "TRADING")
        db.commit()
        candidate_59 = resolve_equity_universe(
            db, as_of=days[58], purpose="CANDIDATE_STOCK"
        )
        assert "600001" not in candidate_59.eligible_codes
        assert "UNIVERSE_NEW_LISTING" in candidate_59.exclusions["600001"]
        candidate_60 = resolve_equity_universe(db, as_of=days[59], purpose="CANDIDATE_STOCK")
        assert "600001" in candidate_60.eligible_codes
        market_19 = resolve_equity_universe(db, as_of=days[18], purpose="MARKET_SCORE")
        assert "600001" not in market_19.eligible_codes
        market_20 = resolve_equity_universe(db, as_of=days[19], purpose="MARKET_SCORE")
        assert "600001" in market_20.eligible_codes
    finally:
        db.close()


def test_delisted_security_stays_in_pre_delisting_universe() -> None:
    db = _db()
    try:
        days = _business_days(date(2025, 1, 2), 30)
        _calendar(db, days)
        _lifecycle(db, "600001", "LISTED", days[0], exchange="SSE")
        for day in days:
            _classification(db, "600001", day, "NORMAL")
            _trading_status(db, "600001", day, "TRADING")
        _lifecycle(db, "600001", "DELISTED", days[25], exchange="SSE")
        db.commit()
        before = resolve_equity_universe(db, as_of=days[24], purpose="MARKET_SCORE")
        assert "600001" in before.eligible_codes
        after = resolve_equity_universe(db, as_of=days[26], purpose="MARKET_SCORE")
        assert "600001" not in after.eligible_codes
        assert "UNIVERSE_DELISTED" in after.exclusions["600001"]
    finally:
        db.close()


def test_fundamental_publication_time_gate() -> None:
    db = _db()
    try:
        db.add(FundamentalReport(
            market="CN",
            code="600000",
            report_period=date(2025, 12, 31),
            report_type="ANNUAL",
            published_at=datetime(2026, 3, 20, 8, 0),
            revision_number=0,
            source="operator-import",
            source_ref="fund-2025-annual-v0",
            net_profit=100.0,
            quality_status="VALID",
        ))
        db.commit()
        hidden = resolve_fundamental(db, "600000", as_of=date(2026, 2, 1))
        assert hidden["available"] is False
        visible = resolve_fundamental(db, "600000", as_of=date(2026, 3, 21))
        assert visible["available"] is True
        assert visible["net_profit"] == 100.0
    finally:
        db.close()


def test_missing_publication_time_not_available() -> None:
    db = _db()
    try:
        db.add(FundamentalReport(
            market="CN",
            code="600000",
            report_period=date(2025, 12, 31),
            report_type="ANNUAL",
            published_at=None,
            revision_number=0,
            source="operator-import",
            source_ref="fund-missing-pub",
            net_profit=99.0,
            quality_status="VALID",
        ))
        db.commit()
        result = resolve_fundamental(db, "600000", as_of=date(2026, 6, 1))
        assert result["available"] is False
        assert result["reason"] == "MISSING_PUBLICATION_TIME"
    finally:
        db.close()


def test_restatement_keeps_historical_versions() -> None:
    db = _db()
    try:
        for revision, published_at, net_profit, ref in (
            (0, datetime(2026, 3, 20, 8, 0), 100.0, "fund-v0"),
            (1, datetime(2026, 5, 1, 8, 0), 120.0, "fund-v1"),
        ):
            db.add(FundamentalReport(
                market="CN",
                code="600000",
                report_period=date(2025, 12, 31),
                report_type="ANNUAL",
                published_at=published_at,
                revision_number=revision,
                is_restatement=revision > 0,
                source="operator-import",
                source_ref=ref,
                net_profit=net_profit,
                quality_status="VALID",
            ))
        db.commit()
        v1 = resolve_fundamental(db, "600000", as_of=date(2026, 4, 1))
        assert v1["available"] is True
        assert v1["net_profit"] == 100.0
        assert v1["revision_number"] == 0
        v2 = resolve_fundamental(db, "600000", as_of=date(2026, 5, 2))
        assert v2["net_profit"] == 120.0
        assert v2["revision_number"] == 1
    finally:
        db.close()


def test_valuation_pit_and_missing() -> None:
    db = _db()
    try:
        db.add(SecurityValuationDaily(
            market="CN",
            code="600000",
            trade_date=date(2025, 6, 1),
            pe_ttm=20.0,
            pb=2.0,
            source="operator-import",
            source_ref="val-2025",
            source_available_at=datetime(2025, 6, 1, 18, 0),
            quality_status="VALID",
        ))
        db.add(SecurityValuationDaily(
            market="CN",
            code="600000",
            trade_date=date(2026, 6, 1),
            pe_ttm=40.0,
            pb=4.0,
            source="operator-import",
            source_ref="val-2026",
            source_available_at=datetime(2026, 6, 1, 18, 0),
            quality_status="VALID",
        ))
        db.commit()
        old = resolve_valuation(db, "600000", trade_date=date(2025, 12, 31))
        assert old["pe_ttm"] == 20.0
        new = resolve_valuation(db, "600000", trade_date=date(2026, 12, 31))
        assert new["pe_ttm"] == 40.0
        missing = resolve_valuation(db, "600001", trade_date=date(2026, 12, 31))
        assert missing["available"] is False
        assert missing["pe_ttm"] is None
    finally:
        db.close()


def test_etf_metadata_no_current_backfill() -> None:
    db = _db()
    try:
        db.add(EtfMetadataHistory(
            market="CN",
            code="510300",
            effective_date=date(2026, 6, 1),
            category="INDEX",
            source="operator-import",
            source_ref="etf-2026",
            source_available_at=datetime(2026, 6, 1, 18, 0),
            quality_status="VALID",
        ))
        db.commit()
        old = resolve_etf_metadata(db, "510300", as_of=date(2025, 6, 1))
        assert old["available"] is False
        current = resolve_etf_metadata(db, "510300", as_of=date(2026, 7, 1))
        assert current["available"] is True
        assert current["category"] == "INDEX"
    finally:
        db.close()


def test_price_basis_compatibility_and_metadata() -> None:
    from app.research.outcomes import price_basis_compatible

    assert price_basis_compatible("QFQ", "QFQ") is True
    assert price_basis_compatible("RAW", "QFQ") is False
    db = _db()
    try:
        db.add(PriceBasisMetadata(
            market="CN",
            code="600000",
            trade_date=date(2025, 6, 1),
            basis="QFQ",
            source="operator-import",
            source_ref="basis-2025",
            source_available_at=datetime(2025, 6, 1, 18, 0),
            quality_status="VALID",
        ))
        db.commit()
        coverage = historical_data_coverage(
            db, start_date=date(2025, 6, 1), end_date=date(2025, 6, 1)
        )
        price_basis = next(item for item in coverage["items"] if item["data_type"] == "price_basis")
        assert price_basis["row_count"] == 1
    finally:
        db.close()


def test_market_universe_mixed_states() -> None:
    db = _db()
    try:
        days = _business_days(date(2025, 1, 2), 30)
        _calendar(db, days)
        as_of = days[-1]
        _seed_stock(db, code="600001", day=as_of, listed_date=days[0], exchange="SSE")
        _seed_stock(db, code="000001", day=as_of, listed_date=days[0], exchange="SZSE")
        _seed_stock(db, code="600002", day=as_of, classification="ST", listed_date=days[0], exchange="SSE")
        _seed_stock(db, code="000002", day=as_of, trading_status="SUSPENDED", listed_date=days[0], exchange="SZSE")
        _seed_stock(db, code="600003", day=as_of, listed_date=days[0], exchange="SSE")
        _lifecycle(db, "600003", "DELISTED", days[10], exchange="SSE")
        _seed_stock(db, code="600004", day=as_of, listed_date=days[15], exchange="SSE")
        _seed_stock(db, code="920001", day=as_of, listed_date=days[0], exchange="BSE", security_type="STOCK")
        _seed_stock(db, code="510300", day=as_of, listed_date=days[0], exchange="SSE", security_type="ETF")
        db.commit()
        result = resolve_equity_universe(db, as_of=as_of, purpose="MARKET_SCORE")
        assert set(result.eligible_codes) == {"600001", "000001"}
        assert "UNIVERSE_ST" in result.exclusions["600002"]
        assert "UNIVERSE_SUSPENDED" in result.exclusions["000002"]
        assert "UNIVERSE_DELISTED" in result.exclusions["600003"]
        assert "UNIVERSE_NEW_LISTING" in result.exclusions["600004"]
        assert "UNIVERSE_BSE" in result.exclusions["920001"]
        assert "UNIVERSE_NON_STOCK" in result.exclusions["510300"]
    finally:
        db.close()


def test_candidate_universe_uses_historical_holdings() -> None:
    db = _db()
    try:
        days = _business_days(date(2024, 1, 2), 70)
        _calendar(db, days)
        listed_date = days[0] - timedelta(days=700)
        for code in ("600001", "000001"):
            _seed_stock(db, code=code, day=days[-1], listed_date=listed_date)
        user = User(email="history@example.com", password_hash="x", timezone="Asia/Shanghai")
        portfolio = Portfolio(user=user, name="PIT")
        db.add(user)
        db.flush()
        db.add(portfolio)
        db.flush()
        snapshot = PortfolioSnapshot(
            user_id=user.id,
            portfolio_id=portfolio.id,
            snapshot_time=datetime(2024, 2, 1, 12, 0),
            status="confirmed",
        )
        db.add(snapshot)
        db.flush()
        db.add(HoldingItem(snapshot_id=snapshot.id, code="600001", name="held"))
        db.commit()
        result = resolve_equity_universe(
            db,
            as_of=days[-1],
            purpose="CANDIDATE_STOCK",
            portfolio_id=portfolio.id,
            user_id=user.id,
        )
        assert "600001" not in result.eligible_codes
        assert "UNIVERSE_HELD" in result.exclusions["600001"]
        assert "000001" in result.eligible_codes
        held_set = resolve_historical_holdings(
            db, portfolio_id=portfolio.id, as_of=days[-1], user_id=user.id
        )
        assert "600001" in held_set
    finally:
        db.close()


def test_coverage_known_total_is_real() -> None:
    db = _db()
    try:
        day = date(2025, 6, 2)
        _calendar(db, [day])
        for index in range(100):
            code = f"{600000 + index:06d}"
            _seed_stock(
                db,
                code=code,
                day=day,
                listed_date=day - timedelta(days=500),
            )
        # Delete classification for 10 securities -> UNKNOWN state.
        codes = [f"{600000 + index:06d}" for index in range(90, 100)]
        db.query(SecurityClassificationDaily).filter(
            SecurityClassificationDaily.code.in_(codes)
        ).delete()
        db.commit()
        result = resolve_equity_universe(db, as_of=day, purpose="MARKET_SCORE")
        assert result.total_count == 100
        assert result.known_count == 90
        assert result.unknown_count == 10
        assert result.coverage == pytest.approx(0.90)
        assert result.status == "PARTIAL"
    finally:
        db.close()


def test_availability_manifest_partial_for_half_range() -> None:
    db = _db()
    try:
        first = date(2025, 1, 2)
        second = date(2025, 1, 3)
        _calendar(db, [first, second])
        _lifecycle(db, "600001", "LISTED", first, exchange="SSE")
        for day in (first, second):
            _classification(db, "600001", day, "NORMAL")
            _trading_status(db, "600001", day, "TRADING")
        db.query(SecurityTradingStatusDaily).filter(
            SecurityTradingStatusDaily.trade_date == second
        ).delete()
        db.commit()
        manifest = build_replay_availability_manifest(
            db, start_date=first, end_date=second
        )
        assert manifest["historical_trading_status"]["status"] == "PARTIAL"
        assert manifest["historical_trading_status"]["coverage"] == pytest.approx(0.5)
        assert manifest["point_in_time_universe"]["status"] == "PARTIAL"
    finally:
        db.close()


def test_sync_idempotent_and_revision_preserved() -> None:
    db = _db()
    try:
        day = date(2025, 6, 2)
        rows = [
            {
                "market": "CN",
                "code": "600001",
                "effective_date": day,
                "event_type": "LISTED",
                "security_type": "STOCK",
                "exchange": "SSE",
                "source": "operator-import",
                "source_ref": "lifecycle-v1",
            }
        ]
        first = run_history_sync(
            db,
            data_type="security_lifecycle",
            start_date=day,
            end_date=day,
            source="operator-import",
            rows=rows,
        )
        assert first["status"] == "COMPLETED"
        assert first["inserted_count"] == 1
        second = run_history_sync(
            db,
            data_type="security_lifecycle",
            start_date=day,
            end_date=day,
            source="operator-import",
            rows=rows,
        )
        assert second["status"] == "COMPLETED"
        assert second["inserted_count"] == 0
        assert second["skipped_count"] == 1
        assert db.query(SecurityLifecycleEvent).count() == 1
        # A revised source row keeps the historical version and adds a revision.
        revised = dict(rows[0], source_ref="lifecycle-v2", security_name="Revised")
        third = run_history_sync(
            db,
            data_type="security_lifecycle",
            start_date=day,
            end_date=day,
            source="operator-import",
            rows=[revised],
        )
        assert third["inserted_count"] == 1
        assert db.query(SecurityLifecycleEvent).count() == 2
    finally:
        db.close()


def test_sync_unsupported_provider_without_rows() -> None:
    db = _db()
    try:
        result = run_history_sync(
            db,
            data_type="valuation",
            start_date=date(2025, 1, 2),
            end_date=date(2025, 1, 3),
            provider="EASTMONEY",
        )
        assert result["status"] == "UNSUPPORTED"
        assert "no_historical_adapter" in (result["error_summary"] or "")
    finally:
        db.close()


def test_history_sync_blocks_when_disk_is_critical(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.system.health.disk_status",
        lambda: {"status": "BLOCKED", "free_ratio": 0.01},
    )
    db = _db()
    try:
        with pytest.raises(RuntimeError, match="DISK_CRITICAL_HISTORY_BACKFILL_BLOCKED"):
            run_history_sync(db, data_type="valuation")
    finally:
        db.close()


def test_sync_reclaim_uses_generation_cas() -> None:
    db = _db()
    try:
        run = enqueue_history_sync(db, data_type="price_basis")
        db.commit()
        claimed = claim_history_sync_run(db, run.id, generation=1)
        assert claimed is not None and claimed.status == "RUNNING"
        # Simulate worker A losing its lease and worker B reclaiming.
        claimed.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
        db.flush()
        reclaimed = reclaim_stale_history_sync_runs(db)
        assert reclaimed and reclaimed[0].attempt_count == 2
        db.commit()
        # Worker A's stale generation can no longer complete the run.
        stale_write_ok = complete_history_sync_run(
            db,
            db.get(HistoricalDataSyncRun, run.id),
            generation=1,
            summary={},
        )
        assert stale_write_ok is False
        db.rollback()
        fresh = db.get(HistoricalDataSyncRun, run.id)
        assert fresh.status == "QUEUED"
        assert fresh.attempt_count == 2
    finally:
        db.close()


def test_startup_recovery_report_counts_stale_history_syncs() -> None:
    from app.system.startup import collect_startup_recovery_report

    db = _db()
    try:
        db.add(HistoricalDataSyncRun(
            data_type="valuation",
            status="RUNNING",
            lease_expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
            attempt_count=1,
        ))
        db.commit()
        report = collect_startup_recovery_report(db)
        assert report["counts"]["stale_history_syncs"] == 1
        assert report["errors"] == []
    finally:
        db.close()


def test_deterministic_recompute_fails_without_pit_data() -> None:
    db = _db()
    try:
        with pytest.raises(ReplayDataQualityError, match="DETERMINISTIC_RECOMPUTE_DATA_GAP"):
            load_replay_facts(
                db,
                scope="MARKET",
                replay_mode="DETERMINISTIC_RECOMPUTE",
                start_date=date(2025, 1, 2),
                end_date=date(2025, 1, 3),
            )
    finally:
        db.close()


def test_deterministic_recompute_unlocks_with_complete_history() -> None:
    db = _db()
    try:
        day = date(2025, 1, 2)
        _calendar(db, [day])
        _seed_stock(db, code="600001", day=day, listed_date=day - timedelta(days=400))
        db.add(SecurityValuationDaily(
            market="CN", code="600001", trade_date=day, pe_ttm=15.0,
            source="operator-import", source_ref="val-1",
            source_available_at=datetime(2025, 1, 2, 18, 0), quality_status="VALID",
        ))
        db.add(PriceBasisMetadata(
            market="CN", code="600001", trade_date=day, basis="QFQ",
            source="operator-import", source_ref="basis-1",
            source_available_at=datetime(2025, 1, 2, 18, 0), quality_status="VALID",
        ))
        db.add(FundamentalReport(
            market="CN", code="600001", report_period=date(2024, 12, 31),
            report_type="ANNUAL", published_at=datetime(2024, 3, 1, 8, 0),
            revision_number=0, source="operator-import", source_ref="fund-1",
            net_profit=10.0, quality_status="VALID",
        ))
        db.commit()
        gate = pit_recompute_gate(
            db, scope="CANDIDATE", start_date=day, end_date=day
        )
        assert gate["status"] == "FULL"
        facts = load_replay_facts(
            db,
            scope="CANDIDATE",
            replay_mode="DETERMINISTIC_RECOMPUTE",
            start_date=day,
            end_date=day,
        )
        assert "candidate_runs" in facts
    finally:
        db.close()


def test_bulk_resolve_uses_constant_queries() -> None:
    db = _db()
    try:
        days = _business_days(date(2025, 1, 2), 30)
        _calendar(db, days)
        as_of = days[-1]
        for index in range(20):
            code = f"{600000 + index:06d}"
            _seed_stock(db, code=code, day=as_of, listed_date=days[0])
        db.commit()
        queries: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            text = str(statement).lower()
            if text.lstrip().startswith("select"):
                queries.append(text)

        event.listen(db._engine, "before_cursor_execute", _count)  # type: ignore[attr-defined]
        try:
            result = resolve_equity_universe(db, as_of=as_of, purpose="MARKET_SCORE")
            assert len(result.eligible_codes) == 20
            assert len(queries) <= 8
        finally:
            event.remove(db._engine, "before_cursor_execute", _count)  # type: ignore[attr-defined]
    finally:
        db.close()


__all__: list[str] = []
