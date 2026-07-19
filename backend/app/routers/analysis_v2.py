"""V2 analysis job lifecycle, progress stream, reports, and comparisons."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..services.analysis_engine import run_analysis_job
from ..v2_dependencies import get_current_user
from ..v2_models import AnalysisJob, AnalysisRun, PortfolioSnapshot, User
from ..v2_schemas import AnalysisJobCreate, AnalysisJobResponse, AnalysisRunDetail, AnalysisRunSummary

router = APIRouter(prefix="/api/v2/analysis", tags=["v2-analysis"])


def _get_job(db: Session, user_id: int, job_id: int) -> AnalysisJob:
    row = db.query(AnalysisJob).filter(AnalysisJob.id == job_id, AnalysisJob.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    return row


def _get_run(db: Session, user_id: int, run_id: int) -> AnalysisRun:
    row = db.query(AnalysisRun).filter(AnalysisRun.id == run_id, AnalysisRun.user_id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis run not found.")
    return row


def _job_response(row: AnalysisJob) -> AnalysisJobResponse:
    return AnalysisJobResponse(
        id=row.id,
        portfolio_id=row.portfolio_id,
        snapshot_id=row.snapshot_id,
        trigger_type=row.trigger_type,
        checkpoint=row.checkpoint,
        mode=row.mode,
        status=row.status,
        progress_percent=row.progress_percent,
        current_stage=row.current_stage,
        notify=row.notify,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error_code=row.error_code,
        error_message=row.error_message,
        retry_count=row.retry_count,
        run_id=row.run.id if row.run else None,
        created_at=row.created_at,
    )


def _run_summary(row: AnalysisRun) -> AnalysisRunSummary:
    return AnalysisRunSummary(
        id=row.id,
        job_id=row.job_id,
        portfolio_snapshot_id=row.portfolio_snapshot_id,
        data_quality_grade=row.data_quality_grade,
        summary=row.summary,
        final_rating=row.final_rating,
        cash_target=row.cash_target,
        confidence=row.confidence,
        created_at=row.created_at,
    )


def _run_detail(row: AnalysisRun) -> AnalysisRunDetail:
    return AnalysisRunDetail(
        **_run_summary(row).model_dump(),
        structured_result=row.structured_result_json or {},
        markdown=row.markdown_text,
    )


@router.post("/jobs", response_model=AnalysisJobResponse, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    payload: AnalysisJobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalysisJobResponse:
    snapshot = (
        db.query(PortfolioSnapshot)
        .filter(
            PortfolioSnapshot.id == payload.snapshot_id,
            PortfolioSnapshot.user_id == current_user.id,
            PortfolioSnapshot.status == "confirmed",
        )
        .first()
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Confirmed snapshot not found.")
    running = (
        db.query(AnalysisJob)
        .filter(
            AnalysisJob.user_id == current_user.id,
            AnalysisJob.snapshot_id == snapshot.id,
            AnalysisJob.status.in_(["queued", "running", "retrying"]),
        )
        .first()
    )
    if running:
        return _job_response(running)
    row = AnalysisJob(
        user_id=current_user.id,
        portfolio_id=snapshot.portfolio_id,
        snapshot_id=snapshot.id,
        trigger_type="manual",
        checkpoint=payload.checkpoint,
        mode=payload.mode,
        notify=payload.notify,
        status="queued",
        current_stage="queued",
        progress_percent=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    background_tasks.add_task(run_analysis_job, row.id)
    return _job_response(row)


@router.get("/jobs/{job_id}", response_model=AnalysisJobResponse)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalysisJobResponse:
    return _job_response(_get_job(db, current_user.id, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=AnalysisJobResponse)
def cancel_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalysisJobResponse:
    row = _get_job(db, current_user.id, job_id)
    if row.status not in {"queued", "running", "retrying"}:
        raise HTTPException(status_code=409, detail="Only active jobs can be cancelled.")
    row.status = "cancelled"
    row.current_stage = "cancelled"
    db.commit()
    db.refresh(row)
    return _job_response(row)


@router.post("/jobs/{job_id}/retry", response_model=AnalysisJobResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalysisJobResponse:
    row = _get_job(db, current_user.id, job_id)
    if row.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried.")
    row.status = "retrying"
    row.current_stage = "queued"
    row.progress_percent = 0
    row.started_at = None
    row.finished_at = None
    row.error_code = None
    row.error_message = None
    row.retry_count += 1
    db.commit()
    db.refresh(row)
    background_tasks.add_task(run_analysis_job, row.id)
    return _job_response(row)


@router.get("/jobs/{job_id}/events")
def job_events(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_job(db, current_user.id, job_id)
    user_id = current_user.id

    async def stream() -> AsyncIterator[str]:
        last_payload = ""
        for _ in range(1800):
            session = SessionLocal()
            try:
                row = session.query(AnalysisJob).filter(AnalysisJob.id == job_id, AnalysisJob.user_id == user_id).first()
                if row is None:
                    yield "event: error\ndata: {\"message\":\"job not found\"}\n\n"
                    return
                payload = json.dumps(_job_response(row).model_dump(mode="json"), ensure_ascii=False)
                if payload != last_payload:
                    yield f"event: progress\ndata: {payload}\n\n"
                    last_payload = payload
                if row.status in {"succeeded", "failed", "cancelled"}:
                    yield "event: done\ndata: {}\n\n"
                    return
            finally:
                session.close()
            await asyncio.sleep(1)
        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/runs", response_model=list[AnalysisRunSummary])
def list_runs(
    portfolio_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AnalysisRunSummary]:
    query = db.query(AnalysisRun).join(AnalysisJob, AnalysisRun.job_id == AnalysisJob.id).filter(AnalysisRun.user_id == current_user.id)
    if portfolio_id is not None:
        query = query.filter(AnalysisJob.portfolio_id == portfolio_id)
    rows = query.order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc()).limit(limit).all()
    return [_run_summary(row) for row in rows]


@router.get("/runs/{run_id}", response_model=AnalysisRunDetail)
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalysisRunDetail:
    return _run_detail(_get_run(db, current_user.id, run_id))


@router.get("/runs/{run_id}/markdown", response_class=PlainTextResponse)
def get_run_markdown(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> str:
    return _get_run(db, current_user.id, run_id).markdown_text


@router.get("/runs/{run_id}/comparison")
def compare_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    current = _get_run(db, current_user.id, run_id)
    current_job = current.job
    previous = (
        db.query(AnalysisRun)
        .join(AnalysisJob, AnalysisRun.job_id == AnalysisJob.id)
        .filter(
            AnalysisRun.user_id == current_user.id,
            AnalysisJob.portfolio_id == current_job.portfolio_id,
            AnalysisRun.created_at < current.created_at,
        )
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .first()
    )
    if previous is None:
        return {"current": _run_summary(current).model_dump(mode="json"), "previous": None, "changes": []}
    current_rows = {item.get("code"): item for item in ((current.structured_result_json or {}).get("result", {}).get("holdings", []))}
    previous_rows = {item.get("code"): item for item in ((previous.structured_result_json or {}).get("result", {}).get("holdings", []))}
    changes = []
    for code in sorted(set(current_rows) | set(previous_rows)):
        before = previous_rows.get(code)
        after = current_rows.get(code)
        if before != after:
            changes.append({"code": code, "before": before, "after": after})
    return {
        "current": _run_summary(current).model_dump(mode="json"),
        "previous": _run_summary(previous).model_dump(mode="json"),
        "changes": changes,
    }
