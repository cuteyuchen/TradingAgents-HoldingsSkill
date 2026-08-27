"""Application-facing orchestration for offline research."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .availability import build_replay_availability_manifest
from .calibration import build_calibration_evidence
from .config import CALIBRATION_ENGINE_VERSION, MAX_BOOTSTRAP_ITERATIONS, normalise_scope
from .models import BacktestRun, CalibrationReport
from .runner import (
    cancel_backtest_run,
    get_backtest_run,
    list_backtest_runs,
    run_backtest,
    load_backtest_rows,
)
from .replay import ReplayDataQualityError


def _replay_capability(manifest: dict[str, Any] | None, scope: str) -> str:
    if not isinstance(manifest, dict):
        return "FULL"
    key = {
        "MARKET": "market_score",
        "CANDIDATE": "candidate_runs",
        "MEMORY_DECISION": "decision_memory",
        "BAR_FACTOR": "daily_bars",
        "PORTFOLIO_DECISION": "portfolio_snapshots",
    }.get(scope)
    item = manifest.get(key) if key else None
    if not isinstance(item, dict):
        return "UNKNOWN"
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        return str(capabilities.get("PRODUCTION_REPLAY") or next(iter(capabilities.values()), "UNKNOWN"))
    return str(item.get("status") or "UNKNOWN")


def serialize_calibration_report(row: CalibrationReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "backtest_run_id": row.backtest_run_id,
        "user_id": row.user_id,
        "portfolio_id": row.portfolio_id,
        "status": row.status,
        "target_parameter": row.target_parameter,
        "current_value": row.current_value_json,
        "challenger_value": row.challenger_value_json,
        "recommendation": row.recommendation,
        "train": row.train_metrics_json,
        "validation": row.validation_metrics_json,
        "test": row.test_metrics_json,
        "robustness": row.robustness_json,
        "sample_counts": row.sample_counts_json,
        "risk_notes": row.risk_notes_json,
        "proposal": row.proposal_json,
        "report": row.report_json,
        "calibration_version": row.calibration_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "no_auto_apply": True,
    }


def _scope_for_parameter(target_parameter: str) -> str:
    value = str(target_parameter).lower()
    if "market" in value or "regime" in value:
        return "MARKET"
    if "fit" in value or "edge" in value:
        return "CANDIDATE"
    if "opportunity" in value or "entry" in value or "rr" in value:
        return "CANDIDATE"
    return "CANDIDATE"


def create_calibration_report(
    db: Session,
    *,
    backtest_run: BacktestRun,
    target_parameter: str,
    parameter_grid: Iterable[Any] | None = None,
    cases: Iterable[dict[str, Any]] | None = None,
    random_seed: int = 0,
    bootstrap_iterations: int = 500,
) -> CalibrationReport:
    if bootstrap_iterations <= 0 or bootstrap_iterations > MAX_BOOTSTRAP_ITERATIONS:
        raise ValueError("invalid_bootstrap_iterations")
    case_rows = list(cases or [])
    evidence = build_calibration_evidence(
        case_rows,
        target_parameter=target_parameter,
        parameter_grid=parameter_grid,
        seed=random_seed,
        bootstrap_iterations=bootstrap_iterations,
        production_config=backtest_run.baseline_config_json,
        quality_status=backtest_run.quality_status,
        leakage_status=backtest_run.leakage_status,
        availability_manifest=backtest_run.data_manifest_json,
        replay_mode=backtest_run.replay_mode,
        replay_capability=_replay_capability(backtest_run.data_manifest_json, backtest_run.scope),
        scope=backtest_run.scope,
        censored_sample=backtest_run.scope == "CANDIDATE" or any(bool(row.get("censored_sample")) for row in case_rows),
    )
    existing = db.execute(select(CalibrationReport).where(
        CalibrationReport.backtest_run_id == backtest_run.id,
        CalibrationReport.target_parameter == target_parameter,
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    row = CalibrationReport(
        backtest_run_id=backtest_run.id,
        user_id=backtest_run.user_id,
        portfolio_id=backtest_run.portfolio_id,
        status="COMPLETED",
        target_parameter=target_parameter,
        current_value_json=evidence.get("current_value"),
        challenger_value_json=evidence.get("challenger_value"),
        recommendation=evidence.get("recommendation", "INSUFFICIENT_EVIDENCE"),
        train_metrics_json=evidence.get("train"),
        validation_metrics_json=evidence.get("validation"),
        test_metrics_json=evidence.get("test"),
        robustness_json=evidence.get("robustness"),
        sample_counts_json=evidence.get("sample_counts"),
        risk_notes_json=evidence.get("known_limitations", []),
        proposal_json={
            "target_parameter": target_parameter,
            "current_value": evidence.get("current_value"),
            "challenger_value": evidence.get("challenger_value"),
            "recommendation": evidence.get("recommendation"),
            "human_review_required": True,
            "apply_supported": False,
        },
        report_json={
            "data_range": {"start_date": backtest_run.start_date.isoformat(), "end_date": backtest_run.end_date.isoformat()},
            "replay_mode": backtest_run.replay_mode,
            "availability_manifest": backtest_run.data_manifest_json,
            "baseline": evidence.get("baseline"),
            "challenger": evidence.get("challenger"),
            "selection_rule": evidence.get("selection_rule"),
            "quality_gate": evidence.get("quality_gate"),
            "folds": evidence.get("folds"),
            "no_auto_apply": True,
        },
        calibration_version=CALIBRATION_ENGINE_VERSION,
    )
    db.add(row)
    db.flush()
    return row


def run_calibration(
    db: Session,
    *,
    target_parameter: str,
    start_date: date,
    end_date: date,
    replay_mode: str = "PRODUCTION_REPLAY",
    scope: str | None = None,
    user_id: int | None = None,
    portfolio_id: int | None = None,
    horizons: Iterable[int] | None = None,
    parameter_grid: Iterable[Any] | None = None,
    cases: Iterable[dict[str, Any]] | None = None,
    random_seed: int = 0,
    bootstrap_iterations: int = 500,
) -> CalibrationReport:
    effective_scope = normalise_scope(scope) if scope is not None else _scope_for_parameter(target_parameter)
    run = run_backtest(
        db,
        scope=effective_scope,
        replay_mode=replay_mode,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        portfolio_id=portfolio_id,
        horizons=horizons,
        experiment_config={"target_parameter": target_parameter, "parameter_grid": list(parameter_grid or [])},
        random_seed=random_seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    effective_cases = list(cases) if cases is not None else []
    if cases is None and run.status not in {"INVALIDATED", "FAILED", "CANCELLED"}:
        try:
            effective_cases = load_backtest_rows(db, run=run)
        except (ReplayDataQualityError, ValueError):
            effective_cases = []
    report = create_calibration_report(
        db,
        backtest_run=run,
        target_parameter=target_parameter,
        parameter_grid=parameter_grid,
        cases=effective_cases,
        random_seed=random_seed,
        bootstrap_iterations=bootstrap_iterations,
    )
    db.commit()
    return report


def list_calibration_reports(db: Session, *, user_id: int | None = None, portfolio_id: int | None = None, limit: int = 50) -> list[CalibrationReport]:
    statement = select(CalibrationReport)
    if user_id is not None:
        statement = statement.where(CalibrationReport.user_id == user_id)
    if portfolio_id is not None:
        statement = statement.where(CalibrationReport.portfolio_id == portfolio_id)
    return list(db.execute(statement.order_by(CalibrationReport.created_at.desc(), CalibrationReport.id.desc()).limit(limit)).scalars())


def get_calibration_report(db: Session, *, report_id: int, user_id: int | None = None) -> CalibrationReport | None:
    row = db.get(CalibrationReport, report_id)
    if row is None or (user_id is not None and row.user_id != user_id):
        return None
    return row


__all__ = [
    "build_replay_availability_manifest",
    "run_backtest",
    "get_backtest_run",
    "list_backtest_runs",
    "cancel_backtest_run",
    "run_calibration",
    "create_calibration_report",
    "list_calibration_reports",
    "get_calibration_report",
    "serialize_calibration_report",
]
