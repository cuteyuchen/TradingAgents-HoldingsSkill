"""Phase I historical replay, backtest, and calibration contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.candidates.models import CandidateRun, CandidateScore
from app.database import Base
from app.market_engine_models import AllAMedianIndexDaily, DailyBarCache, MarketScoreSnapshot
from app.market_models import SecurityMaster, TradingCalendar
from app.portfolio_models import TradeLedgerEntry
from app.research.availability import build_replay_availability_manifest
from app.research.bootstrap import date_block_bootstrap
from app.research.calibration import (
    assess_robustness,
    build_calibration_evidence,
    one_factor_weight_perturbations,
    recommend_calibration,
)
from app.research.models import BacktestMetricSlice, BacktestRun, CalibrationReport
from app.research.outcomes import calculate_forward_outcome, price_basis_compatible
from app.research.replay import ReplayDataQualityError, content_hash, load_replay_facts, replay_candidate_cases, validate_point_in_time
from app.research.runner import (
    cancel_backtest_run,
    create_backtest_run,
    execute_backtest_run,
    heartbeat_backtest_run,
    reclaim_stale_backtest_runs,
    run_backtest,
)
from app.v2_models import Portfolio, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.trigger_models  # noqa: F401
    import app.v2_models  # noqa: F401

    Base.metadata.create_all(engine)
    return Session(engine)


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


def test_manifest_marks_current_lifecycle_and_missing_pit_factors_conservatively():
    db = _db()
    try:
        day = date(2026, 6, 1)
        _calendar(db, [day])
        db.add(SecurityMaster(
            market="CN", exchange="SSE", code="600000", security_type="STOCK",
            listing_date=date(2020, 1, 1), status="ACTIVE", is_st=False, is_suspended=False,
        ))
        db.add(MarketScoreSnapshot(
            snapshot_id="score-1", market="CN", trade_date=day,
            captured_at=datetime(2026, 6, 1, 10), display_score=80, raw_score=80,
            regime="RISK_ON", quality_status="VALID",
        ))
        db.add(DailyBarCache(
            market="CN", code="600000", trade_date=day, adjustment="QFQ",
            close=100, high=101, low=99, available_at=datetime(2026, 6, 1, 16),
        ))
        db.commit()
        manifest = build_replay_availability_manifest(db, start_date=day, end_date=day)
        assert manifest["market_score"]["row_count"] == 1
        assert manifest["market_score"]["capabilities"]["PRODUCTION_REPLAY"] == "FULL"
        assert manifest["daily_bars"]["status"] == "DIAGNOSTIC_ONLY"
        assert manifest["security_lifecycle"]["status"] == "LEAKAGE_BLOCKED"
        assert manifest["fundamentals"]["status"] == "UNSUPPORTED"
        assert manifest["survivorship"]["survivorship_status"] == "CURRENT_UNIVERSE_ONLY"
        assert manifest["data_hash"] == build_replay_availability_manifest(db, start_date=day, end_date=day)["data_hash"]
    finally:
        db.close()


def test_point_in_time_rejects_future_availability_and_inverted_timestamps():
    cutoff = datetime(2026, 6, 1, 10)
    with pytest.raises(ReplayDataQualityError, match="LOOKAHEAD_DETECTED"):
        validate_point_in_time([{"timestamp": "2026-06-01T09:00:00", "available_at": "2026-06-01T11:00:00"}], cutoff)
    with pytest.raises(ReplayDataQualityError, match="TIMESTAMP_INVERSION"):
        validate_point_in_time([{"timestamp": "2026-06-01T12:00:00", "available_at": "2026-06-01T11:00:00"}], cutoff)


def test_market_backtest_uses_future_all_a_index_without_live_io():
    db = _db()
    try:
        days = [date(2026, 6, index) for index in (1, 2, 3, 4, 5)]
        _calendar(db, days)
        db.add(MarketScoreSnapshot(
            snapshot_id="score-80", market="CN", trade_date=days[0],
            # Persisted timestamps are UTC-naive in the application. 06:55 UTC
            # is the 14:55 Shanghai close-window candidate.
            captured_at=datetime(2026, 6, 1, 6, 55, tzinfo=UTC), display_score=80, raw_score=80,
            regime="RISK_ON", quality_status="VALID",
        ))
        for index, day in enumerate(days):
            db.add(AllAMedianIndexDaily(
                market="CN", trade_date=day, index_value=100 + index * 2,
                median_return=0.02, eligible_count=100, available_at=datetime(2026, 6, day.day, 16),
            ))
        db.commit()
        run = run_backtest(
            db, scope="MARKET", replay_mode="PRODUCTION_REPLAY",
            start_date=days[0], end_date=days[0], horizons=[1, 5], user_id=None,
        )
        assert run.status == "COMPLETED"
        assert run.sample_count == 1
        assert run.result_summary_json["no_production_write"] is True
        assert db.query(TradeLedgerEntry).count() == 0
        slices = db.execute(select(BacktestMetricSlice).where(BacktestMetricSlice.run_id == run.id)).scalars().all()
        assert slices
        assert any(item.metrics_json.get("median") is not None for item in slices)
    finally:
        db.close()


def test_candidate_replay_is_censored_and_uses_server_owned_qfq_price():
    db = _db()
    try:
        days = [date(2026, 6, index) for index in (1, 2, 3)]
        _calendar(db, days)
        user = User(email="research@example.com", username="research", password_hash="x")
        db.add(user)
        db.flush()
        portfolio = Portfolio(user_id=user.id, name="Research")
        db.add(portfolio)
        db.flush()
        run = CandidateRun(
            user_id=user.id, portfolio_id=portfolio.id, calculation_key="candidate-research-1",
            trade_date=days[0], as_of=datetime(2026, 6, 1, 15), captured_at=datetime(2026, 6, 1, 15),
            quote_snapshot_id="quote-server-1", quality_status="VALID",
        )
        db.add(run)
        db.flush()
        db.add(CandidateScore(
            candidate_run_id=run.id, code="600000", security_type="STOCK", stage="ACTION", rank=1,
            score=88, opportunity_score=88, entry_score=80, portfolio_fit_score=75,
            decision_edge=6, risk_reward_ratio=1.7, data_coverage=0.9, quality_status="VALID",
            lineage_json={
                "quote_snapshot_id": "quote-server-1",
                "quote_price": 100,
                "quote_price_basis": "QFQ",
                "quote_provider": "server",
            },
            entry_json={"entry_price": 90},
        ))
        for index, day in enumerate(days):
            db.add(DailyBarCache(
                market="CN", code="600000", trade_date=day, adjustment="QFQ",
                open=101 + index, high=103 + index, low=99 + index, close=102 + index,
                prev_close=100 + index, available_at=datetime(2026, 6, day.day, 16),
                quality_status="VALID",
            ))
            db.add(AllAMedianIndexDaily(
                market="CN", trade_date=day, index_value=100 + index,
                median_return=0.01, eligible_count=100, available_at=datetime(2026, 6, day.day, 16),
            ))
        db.commit()
        result = run_backtest(
            db, scope="CANDIDATE", replay_mode="PRODUCTION_REPLAY",
            start_date=days[0], end_date=days[0], user_id=user.id, portfolio_id=portfolio.id,
            horizons=[1],
        )
        assert result.status == "COMPLETED"
        assert any(item.startswith("CENSORED_PRODUCTION_SAMPLE") for item in result.known_limitations_json)
        assert result.leakage_status == "PASS"
        assert any(any(text.startswith("CENSORED_PRODUCTION_SAMPLE") for text in (item.limitations_json or [])) for item in result.metric_slices)
    finally:
        db.close()


def test_forward_outcome_blocks_basis_mismatch_and_limit_up_proxy():
    bars = [
        {"id": 1, "trade_date": date(2026, 6, 2), "open": 110, "high": 110, "low": 110, "close": 110, "prev_close": 100, "adjustment": "QFQ", "metadata_json": {"locked_limit_up": True}},
        {"id": 2, "trade_date": date(2026, 6, 3), "open": 110, "high": 112, "low": 108, "close": 111, "prev_close": 110, "adjustment": "QFQ"},
    ]
    blocked = calculate_forward_outcome(
        decision_date=date(2026, 6, 1), horizon=1, reference_price=100,
        reference_price_basis="RAW", bars=bars,
        target_dates=[date(2026, 6, 2)], execution_basis="NEXT_OPEN_PROXY",
    )
    assert blocked["reason_codes"] == ["PRICE_BASIS_MISMATCH"]
    non_executable = calculate_forward_outcome(
        decision_date=date(2026, 6, 1), horizon=1, reference_price=100,
        reference_price_basis="QFQ", bars=bars,
        target_dates=[date(2026, 6, 2)], execution_basis="NEXT_OPEN_PROXY",
    )
    assert non_executable["execution_status"] == "NON_EXECUTABLE"
    assert price_basis_compatible("QFQ", "QFQ") is True
    assert price_basis_compatible("RAW", "QFQ") is False


def test_date_block_bootstrap_is_reproducible_and_groups_cross_sectional_rows():
    rows = [
        {"trade_date": date(2026, 6, 1), "excess_return": 0.01},
        {"trade_date": date(2026, 6, 1), "excess_return": 0.03},
        {"trade_date": date(2026, 6, 2), "excess_return": -0.02},
    ]
    first = date_block_bootstrap(rows, iterations=100, seed=7)
    second = date_block_bootstrap(rows, iterations=100, seed=7)
    assert first == second
    assert first["block_count"] == 2


def test_robustness_and_weight_perturbation_are_conservative():
    assert assess_robustness({4: {"median": 0.04}, 5: {"median": 0.05}, 6: {"median": 0.049}})["status"] == "ROBUST_PLATEAU"
    assert assess_robustness({4: {"median": 0.02}, 5: {"median": 0.08}, 6: {"median": 0.01}})["status"] == "FRAGILE_PEAK"
    variants = one_factor_weight_perturbations({"trend": 0.2, "risk": 0.1, "flow": 0.7}, "trend", [0.15, 0.2, 0.25])
    assert all(sum(item.values()) == pytest.approx(1.0) for item in variants)
    assert variants[-1]["trend"] == pytest.approx(0.25)


def test_calibration_test_set_cannot_change_selected_challenger_and_low_sample_fails_closed():
    cases = [
        {"trade_date": date(2026, 1, 1) + timedelta(days=index), "decision_edge": 5 + index % 3, "excess_return": 0.01 + (index % 2) * 0.01}
        for index in range(5)
    ]
    evidence = build_calibration_evidence(cases, target_parameter="decision_edge_threshold", parameter_grid=[4, 5, 6], bootstrap_iterations=20)
    assert evidence["recommendation"] == "INSUFFICIENT_EVIDENCE"
    assert "test" in evidence and "challenger_value" in evidence
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})
    assert recommend_calibration(
        baseline={"median": 0.01}, challenger={"median": 0.02},
        train={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        validation={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        test={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        robustness={"status": "ROBUST_PLATEAU"},
        sample_counts={"case_count": 5, "trade_date_count": 5},
    ) == "INSUFFICIENT_EVIDENCE"


def test_backtest_key_is_stable_and_source_set_change_invalidates_run():
    db = _db()
    try:
        days = [date(2026, 6, index) for index in (1, 2, 3)]
        _calendar(db, days)
        db.add(MarketScoreSnapshot(
            snapshot_id="stable-score-1", market="CN", trade_date=days[0],
            captured_at=datetime(2026, 6, 1, 10), display_score=80, raw_score=80,
            regime="RISK_ON", quality_status="VALID",
        ))
        for index, day in enumerate(days):
            db.add(AllAMedianIndexDaily(
                market="CN", trade_date=day, index_value=100 + index,
                median_return=0.01, eligible_count=100,
                available_at=datetime(2026, 6, day.day, 16),
            ))
        db.commit()
        first = create_backtest_run(
            db, scope="MARKET", replay_mode="PRODUCTION_REPLAY",
            start_date=days[0], end_date=days[0], horizons=[1], bootstrap_iterations=20,
        )
        db.commit()
        second = create_backtest_run(
            db, scope="MARKET", replay_mode="PRODUCTION_REPLAY",
            start_date=days[0], end_date=days[0], horizons=[1], bootstrap_iterations=20,
        )
        assert second.id == first.id
        assert first.data_manifest_json["frozen_source_ids"]
        assert first.data_hash

        db.add(MarketScoreSnapshot(
            snapshot_id="stable-score-added-later", market="CN", trade_date=days[0],
            captured_at=datetime(2026, 6, 1, 11), display_score=81, raw_score=81,
            regime="RISK_ON", quality_status="VALID",
        ))
        db.commit()
        execute_backtest_run(db, run=first)
        assert first.status == "INVALIDATED"
        assert first.error_code == "SOURCE_SET_CHANGED"
    finally:
        db.close()


def test_backtest_lease_heartbeat_reclaim_cancel_and_same_run_recovery():
    db = _db()
    try:
        day = date(2026, 6, 1)
        _calendar(db, [day])
        run = create_backtest_run(
            db, scope="MARKET", replay_mode="PRODUCTION_REPLAY",
            start_date=day, end_date=day, horizons=[1], bootstrap_iterations=20,
        )
        db.commit()
        run.status = "RUNNING"
        run.lease_expires_at = datetime(2026, 6, 1, 9)
        run.last_heartbeat_at = datetime(2026, 6, 1, 8)
        db.commit()
        heartbeat_backtest_run(db, run_id=run.id)
        assert run.lease_expires_at > datetime(2026, 6, 1, 9)
        db.commit()

        run.lease_expires_at = datetime(2026, 6, 1, 9)
        db.commit()
        reclaimed = reclaim_stale_backtest_runs(db, now=datetime(2026, 6, 1, 10))
        assert [item.id for item in reclaimed] == [run.id]
        assert run.status == "QUEUED"
        assert run.attempt_count == 2
        db.commit()

        cancelled = cancel_backtest_run(db, run_id=run.id)
        assert cancelled is not None and cancelled.status == "CANCELLED"
        db.commit()
        assert execute_backtest_run(db, run=run).status == "CANCELLED"
    finally:
        db.close()


def test_replay_applies_explicit_cutoff_and_candidate_price_requires_trusted_owner():
    db = _db()
    try:
        days = [date(2026, 6, 1), date(2026, 6, 2)]
        _calendar(db, days)
        db.add(MarketScoreSnapshot(
            snapshot_id="pit-score-1", market="CN", trade_date=days[0],
            captured_at=datetime(2026, 6, 1, 10), display_score=70, raw_score=70,
            regime="NEUTRAL", quality_status="VALID",
        ))
        db.add(MarketScoreSnapshot(
            snapshot_id="pit-score-future", market="CN", trade_date=days[1],
            captured_at=datetime(2026, 6, 2, 10), display_score=90, raw_score=90,
            regime="RISK_ON", quality_status="VALID",
        ))
        db.commit()
        facts = load_replay_facts(
            db, scope="MARKET", replay_mode="PRODUCTION_REPLAY",
            start_date=days[0], end_date=days[1], as_of=datetime(2026, 6, 1, 12),
        )
        assert [item.snapshot_id for item in facts["market_scores"]] == ["pit-score-1"]

        from types import SimpleNamespace

        candidate_run = SimpleNamespace(
            id=1, trade_date=days[0], as_of=datetime(2026, 6, 1, 10),
            quote_snapshot_id="quote-1", metadata_json={}, quality_status="VALID",
        )
        score = SimpleNamespace(
            id=2, candidate_run_id=1, code="600000", name="Fixture", security_type="STOCK",
            etf_category=None, stage="ACTION", score=80, opportunity_score=80, entry_score=70,
            portfolio_fit_score=60, action_score=75, decision_edge=6, risk_reward_ratio=1.6,
            data_coverage=1.0, confidence=0.9, quality_status="VALID", lineage_json={}, entry_json={"price": 90},
        )
        cases = replay_candidate_cases(
            {"candidate_runs": [candidate_run], "candidate_scores": [score], "benchmarks": []},
            replay_mode="PRODUCTION_REPLAY",
        )
        assert cases[0].facts["reference_price"] is None
    finally:
        db.close()


def test_research_api_enforces_owner_input_and_run_controls():
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from app.database import Base, get_db
    from app.main import app
    from app.v2_dependencies import get_current_user

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    day = date(2026, 8, 26)
    with Session(engine) as db:
        user_a = User(email="research-api-a@example.com", username="research-api-a", password_hash="hash")
        user_b = User(email="research-api-b@example.com", username="research-api-b", password_hash="hash")
        db.add_all([user_a, user_b])
        db.flush()
        portfolio_a = Portfolio(user_id=user_a.id, name="Research API A")
        portfolio_b = Portfolio(user_id=user_b.id, name="Research API B")
        db.add_all([portfolio_a, portfolio_b])
        db.flush()
        run_a = BacktestRun(
            user_id=user_a.id,
            portfolio_id=portfolio_a.id,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=day,
            end_date=day,
            status="RUNNING",
            data_hash="a" * 64,
            calculation_key="research-api-a",
            last_heartbeat_at=datetime(2026, 8, 26, 9),
            lease_expires_at=datetime(2026, 8, 26, 10),
        )
        run_b = BacktestRun(
            user_id=user_b.id,
            portfolio_id=portfolio_b.id,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=day,
            end_date=day,
            status="COMPLETED",
            data_hash="b" * 64,
            calculation_key="research-api-b",
        )
        db.add_all([run_a, run_b])
        db.flush()
        report_b = CalibrationReport(
            backtest_run_id=run_b.id,
            user_id=user_b.id,
            portfolio_id=portfolio_b.id,
            target_parameter="decision_edge_threshold",
            recommendation="KEEP_CURRENT",
        )
        db.add(report_b)
        db.commit()
        user_a_id = user_a.id
        portfolio_a_id = portfolio_a.id
        portfolio_b_id = portfolio_b.id
        run_a_id = run_a.id
        run_b_id = run_b.id
        report_b_id = report_b.id

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_a_id, status="active")
    client = TestClient(app)
    try:
        listed = client.get("/api/v3/research/backtests")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [run_a_id]
        assert client.get(f"/api/v3/research/backtests/{run_b_id}").status_code == 404
        assert client.get(f"/api/v3/research/calibrations/{report_b_id}").status_code == 404
        assert client.get(f"/api/v3/research/replay-availability?portfolio_id={portfolio_b_id}").status_code == 404

        base_payload = {
            "scope": "MARKET",
            "replay_mode": "PRODUCTION_REPLAY",
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "bootstrap_iterations": 1,
        }
        forged_nested = {**base_payload, "experiment": {"raw_return": 0.5}}
        assert client.post("/api/v3/research/backtests", json=forged_nested).status_code == 422
        forged_top_level = {**base_payload, "outcome": {"excess_return": 0.5}}
        assert client.post("/api/v3/research/backtests", json=forged_top_level).status_code == 422
        foreign_portfolio = {**base_payload, "scope": "CANDIDATE", "portfolio_id": portfolio_b_id}
        assert client.post("/api/v3/research/backtests", json=foreign_portfolio).status_code == 404

        assert client.post(f"/api/v3/research/backtests/{run_a_id}/heartbeat").status_code in {404, 405}
        cancelled = client.post(f"/api/v3/research/backtests/{run_a_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_calibration_api_requires_completed_run_and_never_starts_a_backtest():
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from app.database import Base, get_db
    from app.main import app
    from app.v2_dependencies import get_current_user

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    day = date(2026, 8, 26)
    with Session(engine) as db:
        user = User(email="calibration-api@example.com", username="calibration-api", password_hash="hash")
        db.add(user)
        db.flush()
        portfolio = Portfolio(user_id=user.id, name="Calibration API")
        db.add(portfolio)
        db.flush()
        running = BacktestRun(
            user_id=user.id,
            portfolio_id=portfolio.id,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=day,
            end_date=day,
            status="RUNNING",
            data_hash="r" * 64,
            calculation_key="calibration-api-running",
            lease_expires_at=datetime(2026, 8, 26, 10),
        )
        completed = BacktestRun(
            user_id=user.id,
            portfolio_id=portfolio.id,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=day,
            end_date=day,
            status="COMPLETED",
            quality_status="FULL",
            leakage_status="PASS",
            data_hash="c" * 64,
            calculation_key="calibration-api-completed",
            horizons_json=[1],
        )
        db.add_all([running, completed])
        db.commit()
        user_id = user.id
        running_id = running.id
        completed_id = completed.id
        run_count = db.query(BacktestRun).count()

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, status="active")
    client = TestClient(app)
    try:
        payload = {
            "backtest_run_id": running_id,
            "target_parameter": "candidate.action_entry_min",
            "bootstrap_iterations": 1,
        }
        assert client.post("/api/v3/research/calibrations", json=payload).status_code == 409

        completed_payload = {
            "backtest_run_id": completed_id,
            "target_parameter": "candidate.action_entry_min",
            "parameter_grid": [60, 65, 70],
            "bootstrap_iterations": 1,
        }
        response = client.post("/api/v3/research/calibrations", json=completed_payload)
        assert response.status_code == 200
        assert response.json()["backtest_run_id"] == completed_id
        with Session(engine) as db:
            assert db.query(BacktestRun).count() == run_count
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
