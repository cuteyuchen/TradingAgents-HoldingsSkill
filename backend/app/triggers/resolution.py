"""Bridge confirmed TriggerEvents to the existing AnalysisJob lifecycle."""
from __future__ import annotations

from datetime import UTC, datetime
from sqlalchemy.orm import Session

from ..decision_contract import canonicalize_analysis_mode, has_actionable_portfolio_change
from ..services.analysis_admission import AnalysisJobAdmission, active_portfolio_analysis
from ..trigger_models import TriggerEvent
from ..v2_models import AnalysisJob, AnalysisRun, PortfolioSnapshot


def _latest_snapshot(db: Session, event: TriggerEvent) -> PortfolioSnapshot | None:
    query = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.status == "confirmed")
    if event.portfolio_id is not None:
        query = query.filter(PortfolioSnapshot.portfolio_id == event.portfolio_id)
    if event.user_id is not None:
        query = query.filter(PortfolioSnapshot.user_id == event.user_id)
    return query.order_by(PortfolioSnapshot.snapshot_time.desc(), PortfolioSnapshot.id.desc()).first()


def _append_trigger_context(context: dict, event: TriggerEvent) -> dict:
    event_ids = list(context.get("trigger_event_ids") or [])
    if event.id not in event_ids:
        event_ids.append(event.id)
    evidence = dict(event.evidence_json or {})
    reason = evidence.get("reason_code") or event.trigger_type
    contexts = list(context.get("trigger_contexts") or [])
    if not any(item.get("trigger_event_id") == event.id for item in contexts if isinstance(item, dict)):
        contexts.append({
            "trigger_event_id": event.id,
            "trigger_reason": reason,
            "trigger_evidence": evidence,
        })
    context["trigger_event_ids"] = event_ids
    context["trigger_contexts"] = contexts
    context["trigger_event_id"] = event.id
    context["trigger_reason"] = reason
    context["trigger_evidence"] = evidence
    return context


def create_trigger_analysis_job(db: Session, event: TriggerEvent) -> AnalysisJobAdmission | None:
    """Create or reuse one fast job for a confirmed non-quality event.

    Only a newly created job belongs to the monitor's execution start.  Reused
    Scheduler, manual, or idempotent trigger jobs must never be run again.
    """
    if event.status not in {"CONFIRMED", "ANALYZING"} or event.trigger_type == "DATA_QUALITY":
        return None
    key = f"trigger:{event.id}"
    existing = db.query(AnalysisJob).filter(AnalysisJob.idempotency_key == key).first()
    if existing is not None:
        event.analysis_job_id = existing.id
        existing.context_json = _append_trigger_context(dict(existing.context_json or {}), event)
        event.status = "ANALYZING" if existing.status in {"queued", "running", "retrying"} else event.status
        db.flush()
        return AnalysisJobAdmission(existing, should_start=False, source="idempotency")
    snapshot = _latest_snapshot(db, event)
    if snapshot is None:
        return None
    active = active_portfolio_analysis(
        db,
        user_id=snapshot.user_id,
        portfolio_id=snapshot.portfolio_id,
    )
    if active is not None:
        event.analysis_job_id = active.id
        active.context_json = _append_trigger_context(dict(active.context_json or {}), event)
        event.status = "ANALYZING"
        db.flush()
        return AnalysisJobAdmission(active, should_start=False, source="active_portfolio")
    job = AnalysisJob(
        user_id=snapshot.user_id, portfolio_id=snapshot.portfolio_id, snapshot_id=snapshot.id,
        trigger_type="realtime_trigger", checkpoint="realtime",
        mode=canonicalize_analysis_mode("fast"), status="queued",
        current_stage="queued", progress_percent=0, notify=False, idempotency_key=key,
        context_json=_append_trigger_context({"created_by_monitor": True}, event),
    )
    db.add(job)
    db.flush()
    event.analysis_job_id = job.id
    event.status = "ANALYZING"
    db.flush()
    return AnalysisJobAdmission(job, should_start=True, source="created")


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
    elif has_actionable_portfolio_change(result):
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
