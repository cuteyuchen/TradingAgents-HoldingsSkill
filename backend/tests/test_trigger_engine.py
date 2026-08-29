from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.trigger_models import TriggerEvent, TriggerPlan

from app.triggers.engine import evaluate_market_scores
from app.triggers.service import apply_detection
from app.config import settings


def _score(value, regime="NEUTRAL", frozen=False, quality="VALID"):
    return SimpleNamespace(
        display_score=value,
        regime=regime,
        is_frozen=frozen,
        quality_status=quality,
        freeze_reason=None,
        snapshot_id=f"s{value}",
    )


def test_market_score_soft_delta_is_deterministic():
    rows = evaluate_market_scores(_score(61), _score(70))
    assert len(rows) == 1
    assert rows[0].reason_code == "MARKET_SCORE_DELTA_SOFT"
    assert rows[0].priority == "P1"
    assert rows[0].evidence["window_minutes"] == settings.TRIGGER_MARKET_SCORE_WINDOW_MINUTES


def test_frozen_transition_is_quality_event_not_score_crash():
    rows = evaluate_market_scores(_score(None, frozen=True, quality="FROZEN"), _score(70))
    assert len(rows) == 1
    assert rows[0].trigger_type == "DATA_QUALITY"
    assert rows[0].current_value is None


def test_debounce_confirms_on_second_hit():
    engine = create_engine("sqlite:///:memory:")
    TriggerPlan.__table__.create(engine)
    TriggerEvent.__table__.create(engine)
    db_session = Session(engine)
    detection = SimpleNamespace(
        trigger_plan_id=None, user_id=1, portfolio_id=1, trigger_type="HOLDING", target_type="HOLDING", target_key="600519",
        priority="P1", metric="price", previous_value=110.0, current_value=99.0, threshold=100.0,
        evidence={}, market_snapshot_id=None, market_score_snapshot_id=None, portfolio_snapshot_id=None,
        dedupe_key="x", rule_id="r", rule_version="v1", debounce_cycles=2, debounce_seconds=9999, cooldown_seconds=60,
    )
    now = datetime.now(UTC)
    event, confirmed = apply_detection(db_session, detection, now=now)
    assert event is not None and not confirmed
    event, confirmed = apply_detection(db_session, detection, now=now + timedelta(seconds=1))
    assert event is not None and confirmed
    assert event.status == "CONFIRMED"
    db_session.close()
