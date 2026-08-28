"""Application-facing orchestration for offline research."""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .availability import build_replay_availability_manifest
from .calibration import build_calibration_evidence
from .config import CALIBRATION_ENGINE_VERSION, MAX_BOOTSTRAP_ITERATIONS
from .models import BacktestRun, CalibrationReport
from .runner import (
    cancel_backtest_run,
    get_backtest_run,
    list_backtest_runs,
    load_backtest_rows_with_sources,
)
from .replay import ReplayDataQualityError, content_hash


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
    if cases is None:
        if backtest_run.status != "COMPLETED":
            raise ValueError("calibration_requires_completed_backtest_run")
        try:
            case_rows, current_source_ids = load_backtest_rows_with_sources(db, run=backtest_run)
        except (ReplayDataQualityError, ValueError):
            case_rows, current_source_ids = [], []
        frozen_source_ids = (backtest_run.data_manifest_json or {}).get("frozen_source_ids")
        if frozen_source_ids is not None and sorted(current_source_ids) != sorted(frozen_source_ids):
            raise ValueError("CALIBRATION_SOURCE_SET_CHANGED")
        frozen_summary = backtest_run.result_summary_json if isinstance(backtest_run.result_summary_json, dict) else {}
        source_lineage = frozen_summary.get("source_lineage") if isinstance(frozen_summary.get("source_lineage"), dict) else {}
        frozen_hash = source_lineage.get("source_set_hash")
        if frozen_hash and content_hash(sorted(current_source_ids)) != frozen_hash:
            raise ValueError("CALIBRATION_SOURCE_SET_CHANGED")
    else:
        case_rows = list(cases)
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
            "global_final_test": evidence.get("global_final_test"),
            "validation_isolation": evidence.get("validation_isolation"),
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
    backtest_run: BacktestRun,
    parameter_grid: Iterable[Any] | None = None,
    cases: Iterable[dict[str, Any]] | None = None,
    random_seed: int = 0,
    bootstrap_iterations: int = 500,
) -> CalibrationReport:
    """Build a report only from an already completed BacktestRun.

    Calibration never starts a Backtest itself; the durable server worker owns
    all backtest execution.
    """

    if backtest_run.status != "COMPLETED":
        raise ValueError("calibration_requires_completed_backtest_run")
    report = create_calibration_report(
        db,
        backtest_run=backtest_run,
        target_parameter=target_parameter,
        parameter_grid=parameter_grid,
        cases=cases,
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
    "get_backtest_run",
    "list_backtest_runs",
    "cancel_backtest_run",
    "run_calibration",
    "create_calibration_report",
    "list_calibration_reports",
    "get_calibration_report",
    "serialize_calibration_report",
]
