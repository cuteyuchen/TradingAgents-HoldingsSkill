"""Focused Phase J forward-observation and evidence-governance tests."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.evaluation.forward import (
    audit_episode_integrity,
    build_daily_observation_coverage,
    campaign_coverage,
    create_daily_evidence_seal,
    create_observation_campaign,
    forward_summary,
    mature_campaign_outcomes,
    transition_campaign,
)
from app.evaluation.models import DailyEvidenceSeal, ObservationCampaign
from app.evaluation.service import PAPER_OBSERVATION_MODE, capture_decision_episode
from app.market_models import TradingCalendar
from app.memory.models import DecisionMemory
from app.v2_models import AnalysisJob, AnalysisRun, Portfolio, PortfolioSnapshot, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _db_no_autoflush() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine, autoflush=False)


def _fixture(db: Session, *, user_email: str = "phase-j@example.com") -> tuple[User, Portfolio, DecisionMemory]:
    user = User(email=user_email, username=user_email.split("@")[0], password_hash="hash")
    db.add(user); db.flush()
    portfolio = Portfolio(user_id=user.id, name="Phase J")
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
        analysis_mode="deep", decision_type="NO_ACTION", quality_status="A", confidence=0.8,
        portfolio_context_json={"portfolio_gate_result": "ALLOW_NO_ACTION"},
        candidate_decisions_json=[{"code": "600000", "stage": "READY", "action": "watch"}],
        no_action_context_json={"reason": "risk budget"},
    )
    db.add(memory); db.commit()
    return user, portfolio, memory


def _calendar(db: Session) -> None:
    days = [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24)]
    for index, day in enumerate(days):
        db.add(TradingCalendar(
            market="CN", trade_date=day, is_open=True,
            previous_trade_date=days[index - 1] if index else None,
            next_trade_date=days[index + 1] if index + 1 < len(days) else None,
        ))
    db.commit()


def test_campaign_lifecycle_and_ownership_are_scoped() -> None:
    db = _db()
    try:
        _calendar(db)
        user, portfolio, _ = _fixture(db)
        campaign = create_observation_campaign(db, user_id=user.id, portfolio_id=portfolio.id, start_date=date(2026, 8, 20), end_date=date(2026, 8, 24))
        assert campaign["status"] == "PLANNED"
        assert campaign["expected_trading_days"] == 3
        assert transition_campaign(db, campaign_id=campaign["campaign_id"], user_id=user.id, action="start")["status"] == "ACTIVE"
        assert transition_campaign(db, campaign_id=campaign["campaign_id"], user_id=user.id, action="pause")["status"] == "PAUSED"
        assert transition_campaign(db, campaign_id=campaign["campaign_id"], user_id=user.id, action="resume")["status"] == "ACTIVE"
        assert transition_campaign(db, campaign_id=campaign["campaign_id"], user_id=user.id, action="complete")["status"] == "COMPLETED"
        other, other_portfolio, _ = _fixture(db, user_email="other@example.com")
        with pytest.raises(ValueError, match="campaign_not_found"):
            transition_campaign(db, campaign_id=campaign["campaign_id"], user_id=other.id, action="start")
        with pytest.raises(ValueError, match="campaign_not_found"):
            transition_campaign(db, campaign_id=campaign["campaign_id"], user_id=user.id, portfolio_id=other_portfolio.id, action="start")
    finally:
        db.close()


def test_forward_only_summary_excludes_historical_replay() -> None:
    db = _db()
    try:
        _calendar(db)
        user, portfolio, memory = _fixture(db)
        historical = capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id, source_mode="FACT_REPLAY")
        realtime = capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id + 1, source_mode=PAPER_OBSERVATION_MODE)
        # The historical episode remains outside the forward query by contract.
        campaign = create_observation_campaign(db, user_id=user.id, portfolio_id=portfolio.id, start_date=date(2026, 8, 20), end_date=date(2026, 8, 24))
        summary = forward_summary(db, campaign_id=campaign["campaign_id"], user_id=user.id, portfolio_id=portfolio.id)
        assert summary["source"] == "FORWARD_ONLY"
        assert summary["episodes"] == 0
        assert historical is not None
        assert realtime is None
    finally:
        db.close()


def test_coverage_records_missed_capture_and_seal_is_idempotent_and_immutable() -> None:
    db = _db()
    try:
        _calendar(db)
        user, portfolio, _ = _fixture(db)
        campaign = create_observation_campaign(db, user_id=user.id, portfolio_id=portfolio.id, start_date=date(2026, 8, 20), end_date=date(2026, 8, 24))
        campaign_row = db.query(ObservationCampaign).filter_by(campaign_id=campaign["campaign_id"]).one()
        coverage = build_daily_observation_coverage(db, campaign=campaign_row, trading_date=date(2026, 8, 20))
        assert coverage["status"] == "MISSED"
        assert "MISSED_DECISION_CAPTURE" in coverage["missing_reasons"]
        first = create_daily_evidence_seal(db, campaign_id=campaign["campaign_id"], user_id=user.id, portfolio_id=portfolio.id, trading_date=date(2026, 8, 20))
        second = create_daily_evidence_seal(db, campaign_id=campaign["campaign_id"], user_id=user.id, portfolio_id=portfolio.id, trading_date=date(2026, 8, 20))
        assert first["seal_id"] == second["seal_id"]
        seal = db.query(DailyEvidenceSeal).one()
        seal.status = "BROKEN"
        with pytest.raises(RuntimeError, match="evaluation_episode_is_immutable"):
            db.flush()
    finally:
        db.close()


def test_integrity_auditor_and_maturity_are_forward_only_and_restart_safe() -> None:
    db = _db()
    try:
        _calendar(db)
        user, portfolio, memory = _fixture(db)
        episode = capture_decision_episode(db, user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=memory.analysis_run_id, source_mode=PAPER_OBSERVATION_MODE)
        assert episode is not None
        audit = audit_episode_integrity(db, episode_id=episode.episode_id, user_id=user.id, portfolio_id=portfolio.id)
        assert audit["status"] == "PASS"
        campaign = create_observation_campaign(db, user_id=user.id, portfolio_id=portfolio.id, start_date=date(2026, 8, 20), end_date=date(2026, 8, 24))
        first = mature_campaign_outcomes(db, campaign_id=campaign["campaign_id"], user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 20, 8, tzinfo=UTC))
        second = mature_campaign_outcomes(db, campaign_id=campaign["campaign_id"], user_id=user.id, portfolio_id=portfolio.id, as_of=datetime(2026, 8, 20, 8, tzinfo=UTC))
        assert first["source"] == "FORWARD_ONLY" and second["source"] == "FORWARD_ONLY"
        assert second["computed"] == 0
        assert campaign_coverage(db, campaign_id=campaign["campaign_id"], user_id=user.id, portfolio_id=portfolio.id)["source"] == "FORWARD_ONLY"
    finally:
        db.close()


def test_campaign_close_is_safe_with_scheduler_session_autoflush_disabled() -> None:
    db = _db_no_autoflush()
    try:
        from app.evaluation.forward import process_campaign_close

        _calendar(db)
        user, portfolio, _ = _fixture(db)
        campaign = create_observation_campaign(db, user_id=user.id, portfolio_id=portfolio.id, start_date=date(2026, 8, 20), end_date=date(2026, 8, 24))
        transition_campaign(db, campaign_id=campaign["campaign_id"], user_id=user.id, action="start")
        result = process_campaign_close(db, user_id=user.id, portfolio_id=portfolio.id, trading_date=date(2026, 8, 20), now=datetime(2026, 8, 20, 8, tzinfo=UTC))
        assert len(result["campaigns"]) == 1
        assert db.query(DailyEvidenceSeal).count() == 1
    finally:
        db.close()
