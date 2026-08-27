"""Focused Phase I evaluation evidence tests."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.evaluation.models import DecisionEvaluationOutcome, EvaluationSnapshot
from app.evaluation.service import (
    EvaluationDataQualityError,
    HistoricalReplayNetworkPolicy,
    ReplayNetworkBlockedError,
    capture_decision_episode,
    content_hash,
    evaluation_summary,
    observe_episode_outcomes,
    replay_episode,
    validate_point_in_time,
    verify_snapshot_hashes,
)
from app.market_engine_models import DailyBarCache
from app.market_models import TradingCalendar
from app.memory.models import DecisionMemory
from app.v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _fixture(db: Session, *, decision_type: str = "NO_ACTION", candidate_stage: str = "READY") -> tuple[User, Portfolio, DecisionMemory]:
    user = User(email="phase-i@example.com", username="phase-i", password_hash="hash")
    db.add(user); db.flush()
    portfolio = Portfolio(user_id=user.id, name="Phase I")
    db.add(portfolio); db.flush()
    snapshot = PortfolioSnapshot(
        user_id=user.id, portfolio_id=portfolio.id, snapshot_time=datetime(2026, 8, 20, 0, 50),
        status="confirmed", total_assets=100000, total_market_value=80000,
    )
    db.add(snapshot); db.flush()
    job = AnalysisJob(
        user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        trigger_type="manual", mode="deep", status="succeeded", current_stage="completed",
    )
    db.add(job); db.flush()
    run = AnalysisRun(
        job_id=job.id, user_id=user.id, portfolio_snapshot_id=snapshot.id,
        markdown_text="decision", final_rating="NO_ACTION", structured_result_json={"ok": True},
        created_at=datetime(2026, 8, 20, 1, 0),
    )
    db.add(run); db.flush()
    memory = DecisionMemory(
        user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=run.id, analysis_job_id=job.id,
        portfolio_snapshot_id=snapshot.id, trade_date=date(2026, 8, 20),
        decision_at=datetime(2026, 8, 20, 1, 5), available_at=datetime(2026, 8, 20, 1, 5),
        analysis_mode="deep", decision_type=decision_type, quality_status="A", confidence=0.8,
        portfolio_context_json={"portfolio_gate_result": "ALLOW_NO_ACTION"},
        candidate_decisions_json=[{"code": "600000", "stage": candidate_stage, "action": "watch"}],
        no_action_context_json={"reason": "risk budget"},
    )
    db.add(memory); db.commit(); return user, portfolio, memory


def _calendar(db: Session, days: list[date]) -> None:
    for index, day in enumerate(days):
        db.add(TradingCalendar(
            market="CN", trade_date=day, is_open=True,
            previous_trade_date=days[index - 1] if index else None,
            next_trade_date=days[index + 1] if index + 1 < len(days) else None,
        ))
    db.commit()


def test_episode_creation_is_idempotent_and_manifest_hash_is_stable() -> None:
    db = _db()
    try:
        user, portfolio, memory = _fixture(db)
        first = capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id)
        second = capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id)
        assert first is not None and second is not None
        assert first.id == second.id
        assert first.manifest_hash == second.manifest_hash
        assert first.decision_type == "NO_ACTION"
    finally:
        db.close()


def test_episode_and_snapshot_are_immutable() -> None:
    db = _db()
    try:
        user, portfolio, memory = _fixture(db)
        episode = capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id)
        assert episode is not None
        snapshot = db.query(EvaluationSnapshot).filter_by(episode_id=episode.id).first()
        assert snapshot is not None
        episode.decision_type = "PORTFOLIO_ACTION"
        with pytest.raises(RuntimeError, match="evaluation_episode_is_immutable"):
            db.flush()
        db.rollback()
        snapshot.payload_json = {"changed": True}
        with pytest.raises(RuntimeError, match="evaluation_episode_is_immutable"):
            db.flush()
    finally:
        db.close()


def test_available_at_violation_blocks_lookahead() -> None:
    with pytest.raises(EvaluationDataQualityError, match="LOOKAHEAD_DETECTED"):
        validate_point_in_time([
            {"input_type": "news", "timestamp": "2026-08-20T00:00:00Z", "available_at": "2026-08-20T02:00:00Z"}
        ], datetime(2026, 8, 20, 1, 0))

    with pytest.raises(EvaluationDataQualityError, match="TIMESTAMP_INVERSION"):
        validate_point_in_time([
            {"input_type": "news", "timestamp": "2026-08-20T02:00:00Z", "available_at": "2026-08-20T01:00:00Z"}
        ], datetime(2026, 8, 20, 3, 0))


def test_replay_policy_blocks_external_io_and_fact_replay_is_labeled() -> None:
    with HistoricalReplayNetworkPolicy():
        with pytest.raises(ReplayNetworkBlockedError):
            from app.evaluation.service import assert_external_io_allowed
            assert_external_io_allowed()
    db = _db()
    try:
        user, portfolio, memory = _fixture(db)
        episode = capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id)
        result = replay_episode(db, episode_id=episode.episode_id, user_id=user.id, portfolio_id=portfolio.id, mode="FACT_REPLAY")
        assert result["replay_label"] == "INSUFFICIENT_HISTORICAL_EVIDENCE"
        assert result["eligible_for_historical_metrics"] is True
        recomputed = replay_episode(db, episode_id=episode.episode_id, user_id=user.id, portfolio_id=portfolio.id, mode="MODEL_RECOMPUTE")
        assert recomputed["replay_label"] == "RECOMPUTED_WITH_CURRENT_MODEL"
        assert recomputed["eligible_for_historical_metrics"] is False
    finally:
        db.close()


def test_outcomes_use_trading_days_and_are_idempotent_with_mfe_mae_drawdown() -> None:
    db = _db()
    try:
        user, portfolio, memory = _fixture(db, decision_type="PORTFOLIO_ACTION", candidate_stage="ACTION")
        episode = capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id)
        days = [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26)]
        _calendar(db, days)
        for idx, day in enumerate(days):
            db.add(DailyBarCache(
                market="CN", code="600000", trade_date=day, adjustment="QFQ", close=100 + idx,
                high=105 + idx, low=95 if idx == 1 else 99 + idx, available_at=datetime(2026, 8, 27), quality_status="VALID",
            ))
        db.commit()
        outcomes = observe_episode_outcomes(db, episode=episode, as_of=datetime(2026, 8, 27), horizons=[1, 3])
        assert [row.target_trade_date for row in outcomes] == [date(2026, 8, 21), date(2026, 8, 25)]
        assert outcomes[0].observation_complete is True
        assert outcomes[0].mfe > 0 and outcomes[0].mae < 0 and outcomes[0].max_drawdown < 0
        again = observe_episode_outcomes(db, episode=episode, as_of=datetime(2026, 8, 27), horizons=[1, 3])
        assert len(db.query(DecisionEvaluationOutcome).all()) == 2
        assert again[0].id == outcomes[0].id
    finally:
        db.close()


def test_summary_keeps_no_action_and_marks_low_sample() -> None:
    db = _db()
    try:
        user, portfolio, memory = _fixture(db)
        capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id)
        summary = evaluation_summary(db, user_id=user.id, portfolio_id=portfolio.id)
        assert summary["no_action_count"] == 1
        assert summary["horizons"]["1"]["status"] == "INSUFFICIENT_SAMPLE"
    finally:
        db.close()


def test_content_hash_is_canonical() -> None:
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})


def test_snapshot_hash_verification_is_read_only() -> None:
    db = _db()
    try:
        user, portfolio, memory = _fixture(db)
        capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id)
        result = verify_snapshot_hashes(db, user_id=user.id, portfolio_id=portfolio.id)
        assert result["status"] == "PASS"
        assert result["checked"] > 0
    finally:
        db.close()
