from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import v2_models  # noqa: F401 - register trigger foreign-key targets
from app.trigger_models import TriggerEvent, TriggerPlan

from app.triggers.engine import TriggerDetection, evaluate_holding_plan, evaluate_market_scores
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


@pytest.mark.parametrize("source_timestamp,expected", [
    (datetime(2026, 10, 9, 10, 30, 15, 123456, tzinfo=timezone(timedelta(hours=8))), "2026-10-09T10:30:15.123456+08:00"),
    (datetime(2026, 10, 9, 10, 30, 15), "2026-10-09T10:30:15"),
    (date(2026, 10, 9), "2026-10-09"),
    ("2026-10-09T10:30:15+08:00", "2026-10-09T10:30:15+08:00"),
    (None, None),
])
def test_holding_evidence_serializes_source_timestamp(source_timestamp, expected):
    plan = SimpleNamespace(
        id=1, user_id=1, portfolio_id=1, target_key="600519", metric="price",
        threshold=100.0, operator="LT", trigger_type="PRICE_BELOW", priority="P1",
        debounce_cycles=2, debounce_seconds=180, cooldown_seconds=60,
    )
    quote = SimpleNamespace(
        price=99.0, quality_status="VALID", provider="test", source_timestamp=source_timestamp,
    )

    detection = evaluate_holding_plan(plan, quote)

    assert detection is not None
    assert detection.evidence["source_timestamp"] == expected
    assert quote.source_timestamp == source_timestamp


@pytest.mark.parametrize("existing_event", [False, True], ids=["insert", "update"])
def test_apply_detection_persists_nested_datetime_and_date_evidence(existing_event):
    engine = create_engine("sqlite:///:memory:")
    TriggerPlan.__table__.create(engine)
    TriggerEvent.__table__.create(engine)
    timestamp = datetime(2026, 10, 9, 10, 30, 15, 123456, tzinfo=timezone(timedelta(hours=8)))
    naive_timestamp = datetime(2026, 10, 9, 10, 30, 15)
    trade_date = date(2026, 10, 9)
    evidence = {
        "source_timestamp": timestamp,
        "trade_date": trade_date,
        "nested": {"observations": [{"observed_at": naive_timestamp}, trade_date]},
        "history": (timestamp, {"trade_date": trade_date}),
        "values": [None, True, 42, 99.5, "unchanged"],
    }
    original_evidence = deepcopy(evidence)
    expected = {
        "source_timestamp": "2026-10-09T10:30:15.123456+08:00",
        "trade_date": "2026-10-09",
        "nested": {"observations": [{"observed_at": "2026-10-09T10:30:15"}, "2026-10-09"]},
        "history": ["2026-10-09T10:30:15.123456+08:00", {"trade_date": "2026-10-09"}],
        "values": [None, True, 42, 99.5, "unchanged"],
    }
    detection = TriggerDetection(
        trigger_type="HOLDING", target_type="HOLDING", target_key="600519", priority="P1",
        metric="price", current_value=99.0, previous_value=110.0, threshold=100.0,
        reason_code="HOLDING_PRICE_BELOW", dedupe_key="datetime-evidence", rule_id="test",
        evidence=evidence, debounce_cycles=2, debounce_seconds=180,
    )
    now = datetime(2026, 10, 9, 2, 30, tzinfo=UTC)
    try:
        with Session(engine) as db:
            previous_id = None
            if existing_event:
                event, confirmed = apply_detection(db, replace(detection, evidence={"initial": True}), now=now)
                assert event is not None and not confirmed
                previous_id = event.id
                db.commit()

            event, confirmed = apply_detection(db, detection, now=now + timedelta(seconds=1))
            assert event is not None
            assert confirmed is existing_event
            if existing_event:
                assert event.id == previous_id
            assert event.evidence_json == expected
            event_id = event.id
            db.commit()

        with Session(engine) as db:
            stored = db.get(TriggerEvent, event_id)
            assert stored is not None
            assert stored.evidence_json == expected
            assert db.query(TriggerEvent).count() == 1
        assert detection.evidence == original_evidence
    finally:
        engine.dispose()
