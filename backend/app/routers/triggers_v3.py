from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..market.codes import normalize_security_code
from ..trigger_models import TriggerEvent, TriggerPlan
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, User
from ..v3_trigger_schemas import DailyPlanFillLink, DailyPlanRefresh, TriggerPlanCreate, TriggerPlanUpdate

router = APIRouter(prefix="/api/v3/triggers", tags=["v3-triggers"])


@router.get("/daily-plan")
def get_daily_plan(portfolio_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..triggers.daily_actions import daily_plan_view

    _portfolio(db, current_user.id, portfolio_id)
    try:
        return daily_plan_view(db, user_id=current_user.id, portfolio_id=portfolio_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/daily-plan/refresh")
def refresh_daily_plan(payload: DailyPlanRefresh, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..analysis_workflow.constants import RunStatus
    from ..triggers.daily_actions import daily_plan_view, latest_plan_run, refresh_daily_action_plans
    from ..v2_models import AnalysisJob, AnalysisRun

    _portfolio(db, current_user.id, payload.portfolio_id)
    run = latest_plan_run(db, user_id=current_user.id, portfolio_id=payload.portfolio_id)
    if payload.analysis_run_id is not None:
        run = db.query(AnalysisRun).join(AnalysisJob).filter(
            AnalysisRun.id == payload.analysis_run_id, AnalysisRun.user_id == current_user.id,
            AnalysisJob.portfolio_id == payload.portfolio_id, AnalysisRun.status.in_(RunStatus.REPORTABLE),
        ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="reportable_analysis_not_found")
    try:
        refresh_daily_action_plans(db, run)
        db.commit()
        return daily_plan_view(db, user_id=current_user.id, portfolio_id=payload.portfolio_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _daily_plan(db: Session, user_id: int, plan_id: int) -> TriggerPlan:
    from ..triggers.daily_actions import SOURCE

    plan = db.query(TriggerPlan).filter(TriggerPlan.id == plan_id, TriggerPlan.user_id == user_id, TriggerPlan.source_type == SOURCE).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="daily_plan_not_found")
    return plan


@router.post("/plans/{plan_id}/fills")
def associate_daily_plan_fill(plan_id: int, payload: DailyPlanFillLink, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..triggers.daily_actions import link_plan_fill

    plan = _daily_plan(db, current_user.id, plan_id)
    try:
        link_plan_fill(db, plan, ledger_entry_id=payload.ledger_entry_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"plan_id": plan.id, "fill_entry_ids": plan.metadata_json.get("fill_entry_ids", []), "review_required": True}


@router.post("/plans/{plan_id}/recheck")
def recheck_action_plan(plan_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..triggers.daily_actions import recheck_daily_plan

    plan = _daily_plan(db, current_user.id, plan_id)
    try:
        result = recheck_daily_plan(db, plan)
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _portfolio(db: Session, user_id: int, portfolio_id: int) -> Portfolio:
    row = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return row


def _plan_payload(row: TriggerPlan) -> dict:
    return {"id": row.id, "user_id": row.user_id, "portfolio_id": row.portfolio_id, "scope": row.scope,
            "target_type": row.target_type, "target_key": row.target_key, "trigger_type": row.trigger_type,
            "metric": row.metric, "operator": row.operator, "threshold": row.threshold,
            "secondary_threshold": row.secondary_threshold, "priority": row.priority,
            "debounce_cycles": row.debounce_cycles, "debounce_seconds": row.debounce_seconds,
            "cooldown_seconds": row.cooldown_seconds, "valid_from": row.valid_from, "expires_at": row.expires_at,
            "enabled": row.enabled, "source_type": row.source_type, "source_id": row.source_id,
            "metadata": row.metadata_json or {}, "created_at": row.created_at, "updated_at": row.updated_at}


def _event_payload(row: TriggerEvent) -> dict:
    return {"id": row.id, "trigger_type": row.trigger_type, "target_type": row.target_type, "target_key": row.target_key,
            "priority": row.priority, "status": row.status, "resolution": row.resolution,
            "detected_at": row.detected_at, "confirmed_at": row.confirmed_at, "resolved_at": row.resolved_at,
            "current_value": row.current_value, "previous_value": row.previous_value, "threshold": row.threshold,
            "analysis_job_id": row.analysis_job_id, "analysis_run_id": row.analysis_run_id,
            "portfolio_id": row.portfolio_id, "evidence": row.evidence_json or {}}


@router.get("/plans")
def list_plans(portfolio_id: int | None = Query(default=None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(TriggerPlan).filter(TriggerPlan.user_id == current_user.id)
    if portfolio_id is not None:
        _portfolio(db, current_user.id, portfolio_id)
        query = query.filter(TriggerPlan.portfolio_id == portfolio_id)
    return [_plan_payload(row) for row in query.order_by(TriggerPlan.updated_at.desc(), TriggerPlan.id.desc()).all()]


@router.post("/plans", status_code=201)
def create_plan(payload: TriggerPlanCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _portfolio(db, current_user.id, payload.portfolio_id)
    key = normalize_security_code(payload.target_key) if payload.target_type == "HOLDING" else payload.target_key.strip()
    if payload.target_type == "HOLDING" and not key:
        raise HTTPException(status_code=422, detail="invalid_security_code")
    row = TriggerPlan(user_id=current_user.id, portfolio_id=payload.portfolio_id, scope="PORTFOLIO",
                      target_type=payload.target_type, target_key=key, trigger_type=payload.trigger_type.upper(),
                      metric=payload.metric, operator=payload.operator, threshold=payload.threshold,
                      secondary_threshold=payload.secondary_threshold, priority=payload.priority,
                      debounce_cycles=payload.debounce_cycles, debounce_seconds=payload.debounce_seconds,
                      cooldown_seconds=payload.cooldown_seconds, valid_from=payload.valid_from, expires_at=payload.expires_at,
                      metadata_json=payload.metadata, source_type="MANUAL", enabled=True)
    db.add(row); db.commit(); db.refresh(row)
    return _plan_payload(row)


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, payload: TriggerPlanUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.query(TriggerPlan).filter(TriggerPlan.id == plan_id, TriggerPlan.user_id == current_user.id).first()
    if row is None: raise HTTPException(status_code=404, detail="Trigger plan not found.")
    if row.source_type == "DAILY_ANALYSIS" and payload.model_dump(exclude_unset=True) != {"enabled": False}:
        raise HTTPException(status_code=409, detail="daily_plan_changes_require_new_analysis")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit(); db.refresh(row)
    return _plan_payload(row)


@router.delete("/plans/{plan_id}")
def disable_plan(plan_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.query(TriggerPlan).filter(TriggerPlan.id == plan_id, TriggerPlan.user_id == current_user.id).first()
    if row is None: raise HTTPException(status_code=404, detail="Trigger plan not found.")
    row.enabled = False; db.commit()
    return {"status": "disabled", "id": row.id}


@router.get("/events")
def list_events(portfolio_id: int | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(TriggerEvent).filter((TriggerEvent.user_id == current_user.id) | (TriggerEvent.user_id.is_(None)))
    if portfolio_id is not None:
        _portfolio(db, current_user.id, portfolio_id); query = query.filter((TriggerEvent.portfolio_id == portfolio_id) | (TriggerEvent.portfolio_id.is_(None)))
    return [_event_payload(row) for row in query.order_by(TriggerEvent.detected_at.desc(), TriggerEvent.id.desc()).limit(limit).all()]


@router.get("/events/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.query(TriggerEvent).filter(TriggerEvent.id == event_id, (TriggerEvent.user_id == current_user.id) | (TriggerEvent.user_id.is_(None))).first()
    if row is None: raise HTTPException(status_code=404, detail="Trigger event not found.")
    return _event_payload(row)
