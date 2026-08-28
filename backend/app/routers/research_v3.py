"""Phase I offline research API."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..research.config import (
    CALIBRATION_EXPERIMENTS,
    DEFAULT_HORIZONS,
    MAX_BOOTSTRAP_ITERATIONS,
    MAX_GRID_SIZE,
    REPLAY_MODES,
    RESEARCH_SCOPES,
)
from ..research.service import (
    build_replay_availability_manifest,
    create_calibration_report,
    get_backtest_run,
    get_calibration_report,
    list_backtest_runs,
    list_calibration_reports,
    serialize_calibration_report,
)
from ..research.runner import (
    cancel_backtest_run,
    dispatch_queued_backtest_runs,
    enqueue_backtest_run,
    serialize_backtest_run,
)
from ..v2_dependencies import get_current_user
from ..v2_models import Portfolio, User

router = APIRouter(prefix="/api/v3/research", tags=["v3-research"])

_FORBIDDEN_RESEARCH_INPUT_KEYS = {
    "outcome",
    "outcomes",
    "return",
    "forward_return",
    "raw_return",
    "benchmark_return",
    "excess_return",
    "mfe",
    "mae",
    "market_score",
    "candidate_score",
    "transaction_cost_estimate",
    "path_bar_ids",
    "historical_data",
    "source_ids",
}


def _contains_server_owned_input(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_RESEARCH_INPUT_KEYS:
                return True
            if _contains_server_owned_input(item):
                return True
    elif isinstance(value, list):
        return any(_contains_server_owned_input(item) for item in value)
    return False


class BacktestRequest(BaseModel):
    scope: Literal["MARKET", "CANDIDATE", "PORTFOLIO_DECISION", "MEMORY_DECISION", "BAR_FACTOR"]
    replay_mode: Literal["PRODUCTION_REPLAY", "DETERMINISTIC_RECOMPUTE", "BAR_ONLY_DIAGNOSTIC"]
    start_date: date
    end_date: date
    portfolio_id: int | None = Field(default=None, ge=1)
    horizons: list[int] | None = None
    experiment: dict[str, Any] | None = None
    random_seed: int = Field(default=0, ge=0, le=2_147_483_647)
    bootstrap_iterations: int = Field(default=500, ge=1, le=MAX_BOOTSTRAP_ITERATIONS)

    model_config = ConfigDict(extra="forbid")

    @field_validator("horizons")
    @classmethod
    def _validate_horizons(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and (not value or len(value) > len(DEFAULT_HORIZONS) or any(item <= 0 for item in value)):
            raise ValueError("invalid_horizons")
        return value

    @field_validator("experiment")
    @classmethod
    def _validate_experiment(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            grid = value.get("parameter_grid")
            if grid is not None and (not isinstance(grid, list) or len(grid) > MAX_GRID_SIZE):
                raise ValueError("invalid_parameter_grid")
            if _contains_server_owned_input(value):
                raise ValueError("server_owned_research_inputs_forbidden")
        return value


class CalibrationRequest(BaseModel):
    backtest_run_id: int = Field(ge=1)
    target_parameter: str = Field(min_length=1, max_length=128)
    parameter_grid: list[float | int | str] | None = Field(default=None, max_length=MAX_GRID_SIZE)
    random_seed: int = Field(default=0, ge=0, le=2_147_483_647)
    bootstrap_iterations: int = Field(default=500, ge=1, le=MAX_BOOTSTRAP_ITERATIONS)

    model_config = ConfigDict(extra="forbid")


def _portfolio(db: Session, *, user_id: int, portfolio_id: int | None) -> None:
    if portfolio_id is None:
        return
    row = db.execute(select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found.")


def _error(exc: ValueError) -> HTTPException:
    message = str(exc)
    code = status.HTTP_409_CONFLICT if "insufficient" in message.lower() or "unsupported" in message.lower() else status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(status_code=code, detail=message)


@router.get("/replay-availability")
def replay_availability(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    portfolio_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return build_replay_availability_manifest(
        db,
        start_date=start_date,
        end_date=end_date,
        portfolio_id=portfolio_id,
        user_id=current_user.id,
    )


@router.post("/backtests")
def create_backtest(
    payload: BacktestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _portfolio(db, user_id=current_user.id, portfolio_id=payload.portfolio_id)
    try:
        row = enqueue_backtest_run(
            db,
            scope=payload.scope,
            replay_mode=payload.replay_mode,
            start_date=payload.start_date,
            end_date=payload.end_date,
            user_id=current_user.id,
            portfolio_id=payload.portfolio_id,
            horizons=payload.horizons,
            experiment_config=payload.experiment,
            random_seed=payload.random_seed,
            bootstrap_iterations=payload.bootstrap_iterations,
        )
        dispatch_queued_backtest_runs(db, limit=1)
        return serialize_backtest_run(row)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.get("/backtests")
def backtests(
    portfolio_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return [serialize_backtest_run(row) for row in list_backtest_runs(db, user_id=current_user.id, portfolio_id=portfolio_id, limit=limit)]


@router.get("/backtests/{run_id}")
def backtest_detail(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = get_backtest_run(db, run_id=run_id, user_id=current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest run not found.")
    return serialize_backtest_run(row)


@router.post("/backtests/{run_id}/cancel")
def cancel_backtest(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = cancel_backtest_run(db, run_id=run_id, user_id=current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest run not found.")
    db.commit()
    return serialize_backtest_run(row)


@router.post("/calibrations")
def create_calibration(
    payload: CalibrationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    run = get_backtest_run(db, run_id=payload.backtest_run_id, user_id=current_user.id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backtest run not found.")
    if run.status != "COMPLETED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Calibration requires a COMPLETED backtest run.",
        )
    try:
        report = create_calibration_report(
            db,
            backtest_run=run,
            target_parameter=payload.target_parameter,
            parameter_grid=payload.parameter_grid,
            random_seed=payload.random_seed,
            bootstrap_iterations=payload.bootstrap_iterations,
        )
        db.commit()
        return serialize_calibration_report(report)
    except ValueError as exc:
        db.rollback()
        raise _error(exc) from exc


@router.get("/calibrations")
def calibrations(
    portfolio_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _portfolio(db, user_id=current_user.id, portfolio_id=portfolio_id)
    return [serialize_calibration_report(row) for row in list_calibration_reports(db, user_id=current_user.id, portfolio_id=portfolio_id, limit=limit)]


@router.get("/calibrations/{report_id}")
def calibration_detail(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = get_calibration_report(db, report_id=report_id, user_id=current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Calibration report not found.")
    return serialize_calibration_report(row)


__all__ = ["router"]
