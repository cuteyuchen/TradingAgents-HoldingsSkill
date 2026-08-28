"""Phase I.1 backtest and calibration integrity regressions."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.market_engine_models import MarketScoreSnapshot
from app.memory.models import DecisionMemory, DecisionOutcome
from app.research.calibration import (
    apply_parameter_variant,
    build_parameter_grid,
    build_calibration_evidence,
    evaluate_threshold_variants,
    recommend_calibration,
    parameter_field,
)
from app.research.replay import (
    canonical_market_score_rows,
    load_replay_facts,
    replay_candidate_cases,
    replay_market_cases,
)
from app.research.runner import (
    _candidate_outcome_rows,
    create_backtest_run,
    dispatch_queued_backtest_runs,
    heartbeat_backtest_run,
)
from app.research.splits import ResearchSplit
from app.research.splits import chronological_splits


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    import app.candidates.models  # noqa: F401
    import app.market_models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.portfolio_models  # noqa: F401
    import app.research.models  # noqa: F401
    import app.trigger_models  # noqa: F401
    import app.v2_models  # noqa: F401

    Base.metadata.create_all(engine)
    return Session(engine)


def _candidate_row(day: date, *, opportunity: float = 80.0, entry: float = 70.0) -> dict:
    return {
        "trade_date": day,
        "entity_id": day.isoformat(),
        "opportunity_score": opportunity,
        "entry_score": entry,
        "portfolio_fit_score": 70.0,
        "risk_reward_ratio": 2.0,
        "action_score": 74.5,
        "decision_edge": 10.0,
        "coverage": 0.9,
        "confidence": 90.0,
        "quality_status": "VALID",
        "market_available": True,
        "market_quality": "VALID",
        "market_frozen": False,
        "quote_quality": "VALID",
        "funding_mode": "CASH_FUNDED",
        "quote_is_proxy": False,
        "limit_up": False,
        "excess_return": 0.02,
    }


def test_walk_forward_uses_all_folds_and_reads_global_test_after_fixed_selection():
    start = date(2024, 1, 1)
    rows = [_candidate_row(start + timedelta(days=index)) for index in range(600)]
    splits = chronological_splits([start + timedelta(days=index) for index in range(600)])
    final_dates = set(splits[0].global_final_test_dates)
    assert len(splits) >= 2
    assert len(final_dates) == 63
    for split in splits:
        assert split.test_dates == ()
        assert not (set(split.train_dates) & final_dates)
        assert not (set(split.validation_dates) & final_dates)
    evidence = build_calibration_evidence(
        rows,
        target_parameter="candidate.action_entry_min",
        parameter_grid=[65, 70, 75],
        bootstrap_iterations=1,
    )

    assert evidence["sample_counts"]["fold_count"] >= 2
    assert evidence["sample_counts"]["tested_fold_count"] == 1
    assert evidence["sample_counts"]["global_final_test_trade_date_count"] == 63
    assert len(evidence["folds"]) == evidence["sample_counts"]["fold_count"]
    assert len(evidence["fold_directions"]) == evidence["sample_counts"]["fold_count"]
    assert all(item["test"] is None for item in evidence["folds"])
    assert evidence["global_final_test"] is not None
    assert evidence["selection_rule"].startswith("GLOBAL_FINAL_HOLDOUT")
    assert evidence["validation_isolation"]["train_overlap_dates"] == []
    assert evidence["validation_isolation"]["validation_overlap_dates"] == []
    assert evidence["validation_isolation"]["selection_overlap_dates"] == []


def test_each_fold_selects_local_challenger_from_train_before_validation_evidence():
    days = [date(2026, 1, index) for index in range(1, 5)]
    split = ResearchSplit(
        fold=0,
        train_dates=(days[0], days[1]),
        validation_dates=(days[2],),
        test_dates=(days[3],),
    )
    rows = [
        {**_candidate_row(days[0]), "entry_score": 70.0, "excess_return": 0.02},
        {**_candidate_row(days[1]), "entry_score": 80.0, "excess_return": 0.01},
        {**_candidate_row(days[2]), "entry_score": 70.0, "excess_return": -0.10},
        {**_candidate_row(days[2]), "entry_score": 80.0, "excess_return": 0.03},
        {**_candidate_row(days[3]), "entry_score": 80.0, "excess_return": 0.03},
    ]
    evidence = build_calibration_evidence(
        rows,
        target_parameter="candidate.action_entry_min",
        parameter_grid=[65, 75],
        splits=[split],
        bootstrap_iterations=1,
    )

    fold = evidence["folds"][0]
    assert fold["selection_source"] == "TRAIN"
    assert fold["selected_challenger"] == 65
    assert evidence["challenger_value"] == 75


def test_entry_challenger_keeps_the_other_production_gates():
    rows = [
        _candidate_row(date(2026, 1, 1), opportunity=55.0, entry=99.0),
        _candidate_row(date(2026, 1, 2), opportunity=80.0, entry=99.0),
    ]
    variants = evaluate_threshold_variants(
        rows,
        target_parameter="candidate.action_entry_min",
        variants=[65],
        bootstrap_iterations=1,
    )

    assert variants[0]["selected_case_count"] == 1
    assert variants[0]["eligible_case_ids"] == ["2026-01-02"]
    assert variants[0]["gate_predicate"].startswith("CURRENT_PRODUCTION_GATE")


def test_censored_candidate_weight_calibration_cannot_recommend_change():
    start = date(2024, 1, 1)
    rows = []
    for index in range(252):
        row = _candidate_row(start + timedelta(days=index))
        row["components"] = {
            "trend": {"score": 80.0},
            "momentum": {"score": 80.0},
            "fundamental": {"score": 80.0},
            "valuation": {"score": 80.0},
            "flow": {"score": 80.0},
            "industry": {"score": 80.0},
            "risk": {"score": 80.0},
        }
        rows.append(row)
    evidence = build_calibration_evidence(
        rows,
        target_parameter="candidate.stock_factor_weights.trend",
        parameter_grid=[0.0, 0.2, 0.4],
        censored_sample=True,
        scope="CANDIDATE",
        bootstrap_iterations=1,
    )

    assert evidence["experiment"] == "WEIGHT_PERTURBATION"
    assert evidence["quality_gate"]["censored_sample"] is True
    assert evidence["recommendation"] != "CONSIDER_CHANGE"


def test_weight_grid_is_bounded_and_censored_opportunity_calibration_fails_closed():
    grid = build_parameter_grid("candidate.stock_factor_weights.trend")
    assert grid == sorted(grid)
    assert all(0.0 <= float(value) <= 1.0 for value in grid)

    recommendation = recommend_calibration(
        baseline={"median": 0.01},
        challenger={"median": 0.02},
        train={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        validation={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        test={"baseline": {"median": 0.01}, "challenger": {"median": 0.02}},
        robustness={"status": "ROBUST_PLATEAU"},
        sample_counts={"case_count": 252, "trade_date_count": 252},
        target_parameter="candidate.action_opportunity_min",
        censored_sample=True,
    )
    assert recommendation == "INSUFFICIENT_EVIDENCE"


def test_market_regime_threshold_maps_to_score_and_moves_hysteresis_pair():
    assert parameter_field("market.regime_lower_bounds.RISK_ON") == "market_score"
    config = {
        "market": {
            "regime_lower_bounds": {"NEUTRAL": 41.0, "RISK_ON": 61.0},
            "regime_hysteresis": {
                "NEUTRAL": {"down": 38.0, "up": 58.0},
                "RISK_ON": {"down": 58.0, "up": 83.0},
            },
        }
    }
    changed = apply_parameter_variant(config, "market.regime_lower_bounds.RISK_ON", 65.0)
    assert changed["market"]["regime_lower_bounds"]["RISK_ON"] == 65.0
    assert changed["market"]["regime_hysteresis"]["NEUTRAL"]["up"] == 62.0
    assert changed["market"]["regime_hysteresis"]["RISK_ON"] == {"down": 62.0, "up": 83.0}


def test_market_regime_calibration_replays_hysteresis_state_not_raw_score_cut():
    config = {
        "market": {
            "regime_lower_bounds": {
                "STRONG_RISK_OFF": 0.0,
                "RISK_OFF": 21.0,
                "NEUTRAL": 41.0,
                "RISK_ON": 61.0,
                "STRONG_RISK_ON": 81.0,
            },
            "regime_hysteresis": {
                "STRONG_RISK_OFF": {"up": 23.0},
                "RISK_OFF": {"down": 18.0, "up": 43.0},
                "NEUTRAL": {"down": 38.0, "up": 63.0},
                "RISK_ON": {"down": 58.0, "up": 83.0},
                "STRONG_RISK_ON": {"down": 78.0},
            },
        }
    }
    rows = [
        {"trade_date": date(2026, 1, 1), "entity_id": "2026-01-01", "market_score": 70.0, "excess_return": 0.02, "market_quality": "VALID"},
        {"trade_date": date(2026, 1, 2), "entity_id": "2026-01-02", "market_score": 60.0, "excess_return": 0.03, "market_quality": "VALID"},
        {"trade_date": date(2026, 1, 3), "entity_id": "2026-01-03", "market_score": 55.0, "excess_return": 0.01, "market_quality": "VALID"},
    ]
    variants = evaluate_threshold_variants(
        rows,
        target_parameter="market.regime_lower_bounds.RISK_ON",
        variants=[61, 65],
        production_config=config,
        bootstrap_iterations=1,
    )

    # Under the production state machine the 60-score day stays RISK_ON at the
    # current boundary (down=58), but the raised boundary moves it to NEUTRAL.
    assert variants[0]["eligible_case_ids"] == ["2026-01-01", "2026-01-02"]
    assert variants[1]["eligible_case_ids"] == ["2026-01-01"]


def test_cost_model_reads_phase_e_settings_and_does_not_add_slippage():
    from app.research.outcomes import estimate_transaction_cost

    old = (
        settings.PORTFOLIO_BROKER_COMMISSION_BPS,
        settings.PORTFOLIO_MINIMUM_COMMISSION,
        settings.PORTFOLIO_SELL_TAX_BPS,
    )
    try:
        settings.PORTFOLIO_BROKER_COMMISSION_BPS = 1.0
        settings.PORTFOLIO_MINIMUM_COMMISSION = 1.0
        settings.PORTFOLIO_SELL_TAX_BPS = 5.0
        cost = estimate_transaction_cost(price=100.0, quantity=1.0, action="BUY")
        assert cost["total_cost"] == 1.0
        assert cost["slippage"] is None
        assert cost["slippage_not_modeled"] is True
    finally:
        settings.PORTFOLIO_BROKER_COMMISSION_BPS, settings.PORTFOLIO_MINIMUM_COMMISSION, settings.PORTFOLIO_SELL_TAX_BPS = old


def test_broker_cost_snapshot_changes_backtest_calculation_key():
    db = _db()
    old = (
        settings.PORTFOLIO_BROKER_COMMISSION_BPS,
        settings.PORTFOLIO_MINIMUM_COMMISSION,
        settings.PORTFOLIO_SELL_TAX_BPS,
    )
    try:
        settings.PORTFOLIO_BROKER_COMMISSION_BPS = 1.0
        settings.PORTFOLIO_MINIMUM_COMMISSION = 1.0
        settings.PORTFOLIO_SELL_TAX_BPS = 5.0
        first = create_backtest_run(
            db,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            horizons=[1],
            bootstrap_iterations=1,
        )
        db.commit()

        settings.PORTFOLIO_BROKER_COMMISSION_BPS = 2.0
        second = create_backtest_run(
            db,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            horizons=[1],
            bootstrap_iterations=1,
        )
        db.commit()

        assert second.id != first.id
        assert second.calculation_key != first.calculation_key
        assert first.experiment_config_json["transaction_cost_model"]["commission_bps"] == 1.0
        assert second.experiment_config_json["transaction_cost_model"]["commission_bps"] == 2.0
    finally:
        settings.PORTFOLIO_BROKER_COMMISSION_BPS, settings.PORTFOLIO_MINIMUM_COMMISSION, settings.PORTFOLIO_SELL_TAX_BPS = old
        db.close()


def test_memory_replay_separates_decision_visibility_from_outcome_maturity():
    db = _db()
    try:
        memory = DecisionMemory(
            user_id=1,
            portfolio_id=1,
            analysis_run_id=1,
            analysis_job_id=1,
            trade_date=date(2026, 1, 31),
            decision_at=datetime(2026, 1, 31, 10),
            available_at=datetime(2026, 1, 31, 11),
            analysis_mode="deep",
            decision_type="portfolio",
            quality_status="VALID",
            decision_features_json={"market_regime": "NEUTRAL"},
        )
        db.add(memory)
        db.flush()
        db.add(DecisionOutcome(
            decision_memory_id=memory.id,
            target_type="SECURITY",
            target_key="600000",
            recommended_action="BUY",
            horizon_trading_days=20,
            reference_trade_date=date(2026, 1, 31),
            reference_at=datetime(2026, 1, 31, 10),
            reference_price=100.0,
            reference_price_basis="QFQ",
            target_trade_date=date(2026, 2, 27),
            raw_return=0.1,
            quality_status="VALID",
            available_at=datetime(2026, 2, 27, 16),
        ))
        db.commit()
        facts = load_replay_facts(
            db,
            scope="MEMORY_DECISION",
            replay_mode="PRODUCTION_REPLAY",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            decision_feature_cutoff=datetime(2026, 1, 31, 23, 59),
            outcome_evaluation_cutoff=datetime(2026, 3, 1),
        )
        assert [item.id for item in facts["decision_memories"]] == [memory.id]
        assert len(facts["decision_outcomes"]) == 1
    finally:
        db.close()


def test_server_owned_backtest_dispatch_reclaims_same_run_without_duplicate():
    db = _db()
    try:
        run = create_backtest_run(
            db,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            horizons=[1],
            bootstrap_iterations=1,
        )
        db.commit()
        dispatched = dispatch_queued_backtest_runs(db, start_workers=False)
        assert dispatched == [run.id]
        assert run.status == "RUNNING"

        run.lease_expires_at = datetime(2026, 6, 1, 9)
        db.commit()
        reclaimed_and_redispatched = dispatch_queued_backtest_runs(db, start_workers=False)
        assert reclaimed_and_redispatched == [run.id]
        assert run.status == "RUNNING"
        assert run.attempt_count == 2
        assert db.query(type(run)).count() == 1
    finally:
        db.close()


def test_running_backtest_heartbeat_prevents_stale_reclaim():
    db = _db()
    try:
        run = create_backtest_run(
            db,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            horizons=[1],
            bootstrap_iterations=1,
        )
        db.commit()
        dispatch_queued_backtest_runs(db, start_workers=False)
        before = run.lease_expires_at
        heartbeat_backtest_run(db, run_id=run.id, generation=1)
        db.commit()
        assert run.lease_expires_at > before
    finally:
        db.close()


def test_old_worker_generation_cannot_renew_a_reclaimed_lease():
    db = _db()
    try:
        run = create_backtest_run(
            db,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            horizons=[1],
            bootstrap_iterations=1,
        )
        db.commit()
        dispatch_queued_backtest_runs(db, start_workers=False)
        assert run.attempt_count == 1
        run.lease_expires_at = datetime(2026, 6, 1, 9)
        db.commit()
        dispatch_queued_backtest_runs(db, start_workers=False)
        assert run.status == "RUNNING"
        assert run.attempt_count == 2
        lease_before = run.lease_expires_at

        heartbeat_backtest_run(db, run_id=run.id, generation=1)
        db.commit()
        db.refresh(run)
        assert run.attempt_count == 2
        assert run.lease_expires_at == lease_before
    finally:
        db.close()


def test_market_daily_replay_uses_one_close_canonical_observation_per_day():
    day = date(2026, 6, 1)
    rows = [
        SimpleNamespace(id=1, snapshot_id="morning", trade_date=day, captured_at=datetime(2026, 6, 1, 1, 35, tzinfo=UTC), raw_score=60, display_score=60, regime="NEUTRAL", confidence=80, quality_status="VALID", score_config_version="v", calculation_version="v"),
        SimpleNamespace(id=2, snapshot_id="close", trade_date=day, captured_at=datetime(2026, 6, 1, 6, 55, tzinfo=UTC), raw_score=70, display_score=70, regime="RISK_ON", confidence=80, quality_status="VALID", score_config_version="v", calculation_version="v"),
        SimpleNamespace(id=3, snapshot_id="after-close", trade_date=day, captured_at=datetime(2026, 6, 1, 7, 30, tzinfo=UTC), raw_score=90, display_score=90, regime="STRONG_RISK_ON", confidence=80, quality_status="VALID", score_config_version="v", calculation_version="v"),
    ]
    canonical = canonical_market_score_rows(rows)
    cases = replay_market_cases({"market_scores": rows, "benchmarks": []}, replay_mode="PRODUCTION_REPLAY")
    assert [row.snapshot_id for row in canonical] == ["close"]
    assert len(cases) == 1
    assert cases[0].entity_id == "close"


def test_market_daily_canonical_close_never_falls_back_to_morning_snapshot():
    day = date(2026, 6, 1)
    rows = [
        SimpleNamespace(id=1, snapshot_id="morning", trade_date=day, captured_at=datetime(2026, 6, 1, 1, 35, tzinfo=UTC), raw_score=60, display_score=60, regime="NEUTRAL", confidence=80, quality_status="VALID", score_config_version="v", calculation_version="v"),
        SimpleNamespace(id=2, snapshot_id="after-close", trade_date=day, captured_at=datetime(2026, 6, 1, 7, 30, tzinfo=UTC), raw_score=90, display_score=90, regime="STRONG_RISK_ON", confidence=80, quality_status="VALID", score_config_version="v", calculation_version="v"),
    ]
    assert canonical_market_score_rows(rows) == []


def test_intraday_candidate_h1_uses_close_and_defers_extremes_to_next_session():
    day = date(2026, 6, 1)
    next_day = date(2026, 6, 2)
    run = SimpleNamespace(
        id=1,
        trade_date=day,
        as_of=datetime(2026, 6, 1, 6, 30),  # 14:30 Asia/Shanghai, UTC-naive storage
        quote_snapshot_id="quote-1",
        metadata_json={"market": {"available": True, "quality_status": "VALID", "is_frozen": False}},
        quality_status="VALID",
    )
    score = SimpleNamespace(
        id=2,
        candidate_run_id=1,
        code="600000",
        name="Fixture",
        security_type="STOCK",
        etf_category=None,
        stage="ACTION",
        score=80,
        opportunity_score=80,
        entry_score=70,
        portfolio_fit_score=70,
        action_score=75,
        decision_edge=6,
        risk_reward_ratio=1.6,
        data_coverage=1.0,
        confidence=0.9,
        quality_status="VALID",
        lineage_json={
            "quote_snapshot_id": "quote-1",
            "quote_price": 100,
            "quote_price_basis": "QFQ",
            "quote_quality": "VALID",
        },
        entry_json={"entry_price": 90},
    )
    case = replay_candidate_cases(
        {"candidate_runs": [run], "candidate_scores": [score], "benchmarks": []},
        replay_mode="PRODUCTION_REPLAY",
    )[0]
    assert case.facts["intraday"] is True
    bars = [
        SimpleNamespace(code="600000", id=10, trade_date=day, open=100, high=120, low=98, close=101, prev_close=100, adjustment="QFQ", available_at=None, quality_status="VALID", metadata_json={}),
        SimpleNamespace(code="600000", id=11, trade_date=next_day, open=102, high=104, low=100, close=103, prev_close=101, adjustment="QFQ", available_at=None, quality_status="VALID", metadata_json={}),
    ]
    rows = _candidate_outcome_rows([case], bars, [], (1, 2), [day, next_day], None)

    h1, h2 = rows
    assert h1["target_trade_date"] == day
    assert h1["raw_return"] == pytest.approx(0.01)
    assert h1["mfe"] is None and h1["mae"] is None
    assert "PARTIAL_INTRADAY_PATH" in h1["reason_codes"]
    assert "INTRADAY_BENCHMARK_UNAVAILABLE" in h1["reason_codes"]
    assert h2["target_trade_date"] == next_day
    assert h2["mfe"] == pytest.approx(0.04)
    assert h2["mfe"] != 0.20
