"""Database lifecycle operations for deterministic trigger detections."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from ..config import settings
from ..trigger_models import TriggerEvent
from .engine import TriggerDetection


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def apply_detection(db: Session, detection: TriggerDetection, *, now: datetime | None = None) -> tuple[TriggerEvent | None, bool]:
    moment = _aware(now) or datetime.now(UTC)
    cooldown_cutoff = moment - timedelta(seconds=max(0, detection.cooldown_seconds))
    confirmed = (
        db.query(TriggerEvent)
        .filter(
            TriggerEvent.dedupe_key == detection.dedupe_key,
            TriggerEvent.status.in_(["CONFIRMED", "ANALYZING", "RESOLVED"]),
            TriggerEvent.confirmed_at.isnot(None),
            TriggerEvent.confirmed_at >= cooldown_cutoff,
        )
        .order_by(TriggerEvent.confirmed_at.desc())
        .first()
    )
    if confirmed is not None:
        return None, False
    event = (
        db.query(TriggerEvent)
        .filter(TriggerEvent.dedupe_key == detection.dedupe_key, TriggerEvent.status == "DETECTED")
        .order_by(TriggerEvent.detected_at.desc())
        .first()
    )
    if event is not None and event.expires_at and (_aware(event.expires_at) or moment) <= moment:
        event.status = "EXPIRED"
        event.resolution = "EXPIRED"
        event.resolved_at = moment
        event = None
    if event is None:
        event = TriggerEvent(
            trigger_plan_id=detection.trigger_plan_id,
            user_id=detection.user_id,
            portfolio_id=detection.portfolio_id,
            trigger_type=detection.trigger_type,
            target_type=detection.target_type,
            target_key=detection.target_key,
            priority=detection.priority,
            status="DETECTED",
            detected_at=moment,
            first_detected_at=moment,
            consecutive_hits=1,
            expires_at=moment + timedelta(seconds=settings.TRIGGER_DETECTED_EXPIRY_SECONDS),
            metric=detection.metric,
            previous_value=detection.previous_value,
            current_value=detection.current_value,
            threshold=detection.threshold,
            evidence_json=detection.evidence,
            market_snapshot_id=detection.market_snapshot_id,
            market_score_snapshot_id=detection.market_score_snapshot_id,
            portfolio_snapshot_id=detection.portfolio_snapshot_id,
            dedupe_key=detection.dedupe_key,
            rule_id=detection.rule_id,
            rule_version=detection.rule_version,
        )
        db.add(event)
    else:
        event.detected_at = moment
        event.consecutive_hits += 1
        event.priority = detection.priority
        event.previous_value = detection.previous_value
        event.current_value = detection.current_value
        event.threshold = detection.threshold
        event.evidence_json = detection.evidence
        event.market_snapshot_id = detection.market_snapshot_id
        event.market_score_snapshot_id = detection.market_score_snapshot_id
        event.portfolio_snapshot_id = detection.portfolio_snapshot_id
    first = _aware(event.first_detected_at) or moment
    cycle_ready = event.consecutive_hits >= max(1, detection.debounce_cycles)
    time_ready = detection.debounce_seconds > 0 and (moment - first).total_seconds() >= detection.debounce_seconds
    newly_confirmed = cycle_ready or time_ready
    if newly_confirmed:
        event.status = "CONFIRMED"
        event.confirmed_at = moment
    db.flush()
    return event, newly_confirmed


def expire_unmatched_detections(
    db: Session,
    *,
    matched_keys: Iterable[str],
    now: datetime | None = None,
    trigger_types: Iterable[str] | None = None,
) -> int:
    moment = _aware(now) or datetime.now(UTC)
    matched = set(matched_keys)
    query = db.query(TriggerEvent).filter(TriggerEvent.status == "DETECTED")
    if trigger_types is not None:
        query = query.filter(TriggerEvent.trigger_type.in_(list(trigger_types)))
    count = 0
    for event in query.all():
        expired = event.dedupe_key not in matched or (event.expires_at and (_aware(event.expires_at) or moment) <= moment)
        if expired:
            event.status = "EXPIRED"
            event.resolution = "EXPIRED"
            event.resolved_at = moment
            count += 1
    return count
