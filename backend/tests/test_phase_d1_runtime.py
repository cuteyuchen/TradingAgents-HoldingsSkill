from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.decision_contract import has_actionable_portfolio_change
from app.market.models import DataQualityStatus, NormalizedQuote
from app.market_engine_models import MarketScoreSnapshot
from app.services.analysis_admission import AnalysisJobAdmission
from app.services.analysis_engine import _trigger_context
from app.services.realtime_monitor import RealtimeMonitor
from app.services.scheduler import create_scheduled_job
from app.trigger_models import TriggerEvent
from app.triggers.engine import TriggerDetection, evaluate_holding_plan, evaluate_market_scores
from app.triggers.resolution import create_trigger_analysis_job, resolve_trigger_event_from_analysis_run
from app.triggers.service import apply_detection, expire_unmatched_detections
from app.v2_models import AnalysisJob, Portfolio, PortfolioSnapshot, Schedule, User


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _portfolio_fixture(db: Session) -> tuple[User, Portfolio, PortfolioSnapshot]:
    user = User(email="d1@example.com", username="d1", password_hash="hash")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name="D1")
    db.add(portfolio)
    db.flush()
    snapshot = PortfolioSnapshot(user_id=user.id, portfolio_id=portfolio.id, status="confirmed")
    db.add(snapshot)
    db.flush()
    return user, portfolio, snapshot


def _event(user_id: int, portfolio_id: int, snapshot_id: int) -> TriggerEvent:
    return TriggerEvent(
        user_id=user_id,
        portfolio_id=portfolio_id,
        portfolio_snapshot_id=snapshot_id,
        trigger_type="HOLDING",
        target_type="HOLDING",
        target_key="600519",
        priority="P1",
        status="CONFIRMED",
        dedupe_key=f"event:{snapshot_id}:{portfolio_id}",
        rule_version="test",
    )


def _job(user: User, portfolio: Portfolio, snapshot: PortfolioSnapshot, *, status: str = "running") -> AnalysisJob:
    return AnalysisJob(
        user_id=user.id,
        portfolio_id=portfolio.id,
        snapshot_id=snapshot.id,
        trigger_type="manual",
        mode="standard",
        status=status,
        current_stage=status,
        idempotency_key=f"existing:{snapshot.id}:{status}",
    )


def test_trigger_and_scheduler_reuse_active_portfolio_job_without_starting_again():
    db = _db()
    try:
        user, portfolio, snapshot = _portfolio_fixture(db)
        running = _job(user, portfolio, snapshot)
        db.add(running)
        db.flush()
        event = _event(user.id, portfolio.id, snapshot.id)
        db.add(event)
        db.flush()

        trigger_admission = create_trigger_analysis_job(db, event)
        assert trigger_admission is not None
        assert trigger_admission.job.id == running.id
        assert trigger_admission.should_start is False
        assert running.context_json["trigger_contexts"][0]["trigger_event_id"] == event.id

        schedule = Schedule(
            user_id=user.id,
            portfolio_id=portfolio.id,
            name="14:30",
            checkpoint="14:30",
            mode="standard",
            hour=14,
            minute=30,
        )
        db.add(schedule)
        db.flush()
        schedule_admission = create_scheduled_job(db, schedule, with_admission=True)
        assert isinstance(schedule_admission, AnalysisJobAdmission)
        assert schedule_admission.job.id == running.id
        assert schedule_admission.should_start is False
    finally:
        db.close()


def test_monitor_does_not_start_reused_analysis_job(monkeypatch):
    monitor = RealtimeMonitor()
    event = SimpleNamespace(id=7)
    detection = TriggerDetection(
        trigger_type="HOLDING", target_type="HOLDING", target_key="600519", priority="P1",
        metric="price", current_value=99, previous_value=101, threshold=100,
        reason_code="HOLDING_PRICE_BELOW", dedupe_key="holding:test", rule_id="test",
    )
    monkeypatch.setattr("app.services.realtime_monitor.apply_detection", lambda *_args, **_kwargs: (event, True))
    monkeypatch.setattr(
        "app.services.realtime_monitor.create_trigger_analysis_job",
        lambda *_args, **_kwargs: AnalysisJobAdmission(SimpleNamespace(id=22), False, "active_portfolio"),
    )
    summary = {"events": [], "confirmed_events": 0, "analysis_jobs": 0, "reused_analysis_jobs": 0}
    job_ids: set[int] = set()
    monitor._persist_detection(None, detection, now=datetime.now(UTC), summary=summary, analysis_job_ids=job_ids)
    assert job_ids == set()
    assert summary["analysis_jobs"] == 0
    assert summary["reused_analysis_jobs"] == 1


def test_market_baseline_requires_same_trade_date_and_time_window():
    db = _db()
    try:
        now = datetime(2026, 8, 24, 1, 35, tzinfo=UTC)
        db.add_all([
            MarketScoreSnapshot(snapshot_id="yesterday", market="CN", trade_date=datetime(2026, 8, 23).date(), captured_at=now - timedelta(hours=18), display_score=70, quality_status="VALID", is_frozen=False),
            MarketScoreSnapshot(snapshot_id="current", market="CN", trade_date=datetime(2026, 8, 24).date(), captured_at=now, display_score=61, quality_status="VALID", is_frozen=False),
        ])
        db.commit()
        monitor = RealtimeMonitor()
        current, baseline, _quality_previous = monitor._market_score_pair(db, now)
        assert current.snapshot_id == "current"
        assert baseline is None

        db.add(MarketScoreSnapshot(snapshot_id="same-day", market="CN", trade_date=datetime(2026, 8, 24).date(), captured_at=now - timedelta(minutes=15), display_score=70, quality_status="VALID", is_frozen=False))
        db.commit()
        _current, baseline, _quality_previous = monitor._market_score_pair(db, now)
        assert baseline.snapshot_id == "same-day"
    finally:
        db.close()


def test_market_detection_expires_when_the_condition_recovers_between_hits():
    db = _db()
    try:
        current = SimpleNamespace(display_score=61, regime="NEUTRAL", is_frozen=False, quality_status="VALID", freeze_reason=None, snapshot_id="now")
        previous = SimpleNamespace(display_score=70, regime="NEUTRAL", is_frozen=False, quality_status="VALID", freeze_reason=None, snapshot_id="then")
        detection = evaluate_market_scores(current, previous)[0]
        moment = datetime.now(UTC)
        first, confirmed = apply_detection(db, detection, now=moment)
        assert first is not None and not confirmed and first.consecutive_hits == 1
        expire_unmatched_detections(db, matched_keys=set(), now=moment + timedelta(minutes=1), trigger_types=["MARKET"])
        second, confirmed = apply_detection(db, detection, now=moment + timedelta(minutes=2))
        assert second is not None and not confirmed and second.consecutive_hits == 1
    finally:
        db.close()


def test_unmatched_expiry_is_scoped_to_the_monitor_request_user():
    db = _db()
    try:
        first = TriggerEvent(
            user_id=1, portfolio_id=1, trigger_type="MARKET", target_type="MARKET", target_key="CN",
            priority="P1", status="DETECTED", dedupe_key="user-one", rule_version="test",
        )
        second = TriggerEvent(
            user_id=2, portfolio_id=2, trigger_type="MARKET", target_type="MARKET", target_key="CN",
            priority="P1", status="DETECTED", dedupe_key="user-two", rule_version="test",
        )
        db.add_all([first, second])
        db.flush()
        expire_unmatched_detections(
            db,
            matched_keys=set(),
            trigger_types=["MARKET"],
            user_id=1,
        )
        assert first.status == "EXPIRED"
        assert second.status == "DETECTED"
    finally:
        db.close()


def test_cross_below_uses_persisted_previous_observation_and_confirms_once():
    plan = SimpleNamespace(
        id=1, user_id=1, portfolio_id=1, target_key="600519", trigger_type="PRICE_BELOW",
        metric="price", operator="CROSS_BELOW", threshold=100, priority="P1",
        debounce_cycles=2, debounce_seconds=180, cooldown_seconds=1800,
        metadata_json={"last_observation": {"metric": "price", "value": 101}},
    )
    monitor = RealtimeMonitor()
    quote = NormalizedQuote(code="600519", price=99, prev_close=101, quality_status=DataQualityStatus.VALID)
    detection = evaluate_holding_plan(plan, quote, previous_value=monitor._last_observed_value(plan), portfolio_snapshot_id=1)
    assert detection is not None
    assert detection.previous_value == 101
    assert detection.debounce_cycles == 1


def test_cross_above_is_also_evaluated_from_the_previous_observation():
    plan = SimpleNamespace(
        id=2, user_id=1, portfolio_id=1, target_key="600519", trigger_type="PRICE_ABOVE",
        metric="price", operator="CROSS_ABOVE", threshold=100, priority="P1",
        debounce_cycles=3, debounce_seconds=180, cooldown_seconds=1800,
        metadata_json={"last_observation": {"metric": "price", "value": 99}},
    )
    monitor = RealtimeMonitor()
    quote = NormalizedQuote(code="600519", price=101, prev_close=99, quality_status=DataQualityStatus.VALID)
    detection = evaluate_holding_plan(plan, quote, previous_value=monitor._last_observed_value(plan), portfolio_snapshot_id=1)
    assert detection is not None
    assert detection.debounce_cycles == 1


def test_expired_trigger_plan_is_not_effective():
    monitor = RealtimeMonitor()
    now = datetime.now(UTC)
    plan = SimpleNamespace(valid_from=None, expires_at=now - timedelta(seconds=1))
    assert monitor._plan_expired(plan, now)


def test_dual_source_conflict_becomes_data_quality(monkeypatch):
    class Provider:
        def __init__(self, quote): self.quote = quote
        def get_quote(self, _code): return self.quote

    quotes = iter([
        Provider(NormalizedQuote(code="600519", price=98, prev_close=100, quality_status=DataQualityStatus.VALID)),
        Provider(NormalizedQuote(code="600519", price=103, prev_close=100, quality_status=DataQualityStatus.VALID)),
    ])
    monkeypatch.setattr("app.services.realtime_monitor.create_quote_provider", lambda _name: next(quotes))
    verified, evidence = RealtimeMonitor()._verify_holding_quote("600519")
    assert verified.quality_status == DataQualityStatus.CONFLICT
    assert evidence["comparison"]["price_diff_pct"] > 0.5


def test_dual_source_conflict_cannot_start_a_fast_analysis(monkeypatch):
    monitor = RealtimeMonitor()
    detection = TriggerDetection(
        trigger_type="DATA_QUALITY", target_type="DATA_QUALITY", target_key="600519", priority="P1",
        metric="quote_quality", current_value=None, previous_value=None, threshold=None,
        reason_code="HOLDING_QUOTE_CONFLICT", dedupe_key="quality:conflict", rule_id="test",
    )
    monkeypatch.setattr("app.services.realtime_monitor.apply_detection", lambda *_args, **_kwargs: (SimpleNamespace(id=8), True))
    started: list[object] = []
    monkeypatch.setattr("app.services.realtime_monitor.create_trigger_analysis_job", lambda *_args, **_kwargs: started.append(True))
    summary = {"events": [], "confirmed_events": 0, "analysis_jobs": 0, "reused_analysis_jobs": 0}
    monitor._persist_detection(None, detection, now=datetime.now(UTC), summary=summary, analysis_job_ids=set())
    assert started == []
    assert summary["analysis_jobs"] == 0


def test_trigger_context_is_explicit_analysis_context_not_an_order():
    context = _trigger_context(SimpleNamespace(context_json={
        "trigger_event_ids": [3],
        "trigger_reason": "MARKET_SCORE_DELTA_SOFT",
        "trigger_evidence": {"delta": -9},
    }))
    assert context is not None
    assert context["trigger_event_ids"] == [3]
    assert context["events"][0]["trigger_event_id"] == 3
    assert "不是交易指令" in context["interpretation"]


@pytest.mark.parametrize(
    ("result", "rating", "grade", "expected"),
    [
        ({"holdings": [{"action": "hold"}], "candidates": []}, "no_action", "A", "NO_ACTION"),
        ({"holdings": [{"action": "watch"}], "candidates": []}, "watch_only", "D", "DISMISSED_DATA_ERROR"),
        ({"holdings": [{"action": "hold"}], "candidates": [{"candidate_type": "new_position", "buyable": True}]}, "hold", "A", "ACTION"),
    ],
)
def test_trigger_resolution_uses_shared_action_contract(result, rating, grade, expected):
    event = SimpleNamespace(analysis_run_id=None, status="ANALYZING", resolution=None, resolved_at=None)
    job = SimpleNamespace(context_json={"trigger_event_ids": [1]})

    class FakeDB:
        def get(self, model, key):
            if model is AnalysisJob:
                return job
            if model is TriggerEvent and key == 1:
                return event
            return None
        def flush(self): pass

    run = SimpleNamespace(job_id=10, final_rating=rating, data_quality_grade=grade, structured_result_json={"result": result}, id=11)
    resolve_trigger_event_from_analysis_run(FakeDB(), run)
    assert event.status == "RESOLVED"
    assert event.resolution == expected


def test_candidate_and_rating_are_actionable_in_shared_contract():
    assert has_actionable_portfolio_change({"final_rating": "rotate"})
    assert has_actionable_portfolio_change({"candidates": [{"action": "new_position"}]})
