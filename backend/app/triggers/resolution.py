"""Bridge confirmed TriggerEvents to the existing AnalysisJob lifecycle."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..decision_contract import canonicalize_analysis_mode
from ..trigger_models import TriggerEvent
from ..v2_models import AnalysisJob, AnalysisRun, PortfolioSnapshot


def _latest_snapshot(db: Session, event: TriggerEvent) -> PortfolioSnapshot | None:
    query = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.status == "confirmed")
    if event.portfolio_id is not None:
        query = query.filter(PortfolioSnapshot.portfolio_id == event.portfolio_id)
    if event.user_id is not None:
        query = query.filter(PortfolioSnapshot.user_id == event.user_id)
    return query.order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).first()


def create_trigger_analysis_job(db: Session, event: TriggerEvent) -> AnalysisJob | None:
    """Create or reuse one fast job for a confirmed non-quality event."""
    if event.status not in {"CONFIRMED", "ANALYZING"} or event.trigger_type == "DATA_QUALITY":
        return None
    key = f"trigger:{event.id}"
    existing = db.query(AnalysisJob).filter(AnalysisJob.idempotency_key == key).first()
    if existing is not None:
        event.analysis_job_id = existing.id
        context = dict(existing.context_json or {})
        event_ids = list(context.get("trigger_event_ids") or [])
        if event.id not in event_ids:
            event_ids.append(event.id)
        context["trigger_event_ids"] = event_ids
        existing.context_json = context
        event.status = "ANALYZING" if existing.status in {"queued", "running", "retrying"} else event.status
        db.flush()
        return existing
    snapshot = _latest_snapshot(db, event)
    if snapshot is None:
        return None
    active = db.query(AnalysisJob).filter(
        AnalysisJob.user_id == snapshot.user_id,
        AnalysisJob.portfolio_id == snapshot.portfolio_id,
        AnalysisJob.status.in_(["queued", "running", "retrying"]),
    ).order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc()).first()
    if active is not None:
        event.analysis_job_id = active.id
        context = dict(active.context_json or {})
        event_ids = list(context.get("trigger_event_ids") or [])
        if event.id not in event_ids:
            event_ids.append(event.id)
        context["trigger_event_ids"] = event_ids
        active.context_json = context
        event.status = "ANALYZING"
        db.flush()
        return active
    job = AnalysisJob(
        user_id=snapshot.user_id, portfolio_id=snapshot.portfolio_id, snapshot_id=snapshot.id,
        trigger_type="realtime_trigger", checkpoint="realtime",
        mode=canonicalize_analysis_mode("fast"), status="queued",
        current_stage="queued", progress_percent=0, notify=True, idempotency_key=key,
        context_json={
            "trigger_event_id": event.id,
            "trigger_event_ids": [event.id],
            "trigger_reason": event.resolution or event.trigger_type,
            "trigger_evidence": event.evidence_json or {},
            "created_by_monitor": True,
        },
    )
    db.add(job)
    db.flush()
    event.analysis_job_id = job.id
    event.status = "ANALYZING"
    db.flush()
    return job


def _has_action(result: dict[str, Any]) -> bool:
    actions = result.get("holdings") or []
    for row in actions:
        if isinstance(row, dict) and str(row.get("action") or "").lower() in {"add", "conditional_add", "reduce", "sell"}:
            return True
    for row in result.get("today_actions") or []:
        if isinstance(row, dict) and str(row.get("action") or row.get("type") or "").lower() in {"add", "conditional_add", "reduce", "sell"}:
            return True
    return False


def resolve_trigger_event_from_analysis_run(db: Session, run: AnalysisRun) -> TriggerEvent | None:
    job = db.get(AnalysisJob, run.job_id)
    context = (job.context_json or {}) if job else {}
    event_ids = list(context.get("trigger_event_ids") or [])
    if not event_ids and context.get("trigger_event_id"):
        event_ids = [context.get("trigger_event_id")]
    if not event_ids:
        return None
    result = (run.structured_result_json or {}).get("result", {})
    rating = str(run.final_rating or result.get("final_rating") or "").lower()
    if rating == "no_action":
        resolution = "NO_ACTION"
    elif _has_action(result):
        resolution = "ACTION"
    elif rating == "watch_only" or str(run.data_quality_grade or "").upper() in {"D", "F"}:
        resolution = "DISMISSED_DATA_ERROR"
    else:
        resolution = "NO_ACTION"
    first_event = None
    for event_id in event_ids:
        event = db.get(TriggerEvent, int(event_id))
        if event is None:
            continue
        if first_event is None:
            first_event = event
        event.analysis_run_id = run.id
        event.status = "RESOLVED"
        event.resolution = resolution
        event.resolved_at = datetime.now(UTC)
    db.flush()
    return first_event


__all__ = ["create_trigger_analysis_job", "resolve_trigger_event_from_analysis_run"]
