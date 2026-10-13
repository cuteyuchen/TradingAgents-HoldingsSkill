"""Application-facing orchestration for offline research."""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .availability import build_replay_availability_manifest
from .calibration import build_calibration_evidence, build_parameter_grid, current_parameter_value, normalise_parameter
from .config import CALIBRATION_ENGINE_VERSION, MAX_BOOTSTRAP_ITERATIONS, METRICS_ENGINE_VERSION, current_production_config
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
    recompute = manifest.get("recompute_capability")
    if isinstance(recompute, dict) and recompute.get("capability"):
        return str(recompute["capability"])
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
    report = row.report_json if isinstance(row.report_json, dict) else {}
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
        "experiment_spec": report.get("experiment_spec"),
        "experiment_spec_hash": report.get("experiment_spec_hash"),
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


def _existing_calibration_for_parameter(
    db: Session, *, backtest_run_id: int, target_parameter: str,
) -> CalibrationReport | None:
    # Legacy rows may use aliases or surrounding whitespace. Do not rewrite
    # immutable evidence or let a new canonical key reopen those experiments.
    rows = db.scalars(select(CalibrationReport).where(
        CalibrationReport.backtest_run_id == backtest_run_id,
    )).all()
    matches = [row for row in rows if normalise_parameter(row.target_parameter) == target_parameter]
    if len(matches) > 1:
        raise ValueError("CALIBRATION_SPEC_CONFLICT:multiple_reports_for_effective_parameter")
    return matches[0] if matches else None


def _check_frozen_calibration_spec(
    row: CalibrationReport, spec: dict[str, Any], *, current_value: Any,
) -> None:
    report = row.report_json if isinstance(row.report_json, dict) else {}
    frozen_hash = report.get("experiment_spec_hash")
    frozen_spec = report.get("experiment_spec")
    if not frozen_hash or not isinstance(frozen_spec, dict):
        raise ValueError("CALIBRATION_SPEC_UNAVAILABLE:legacy_report_has_no_frozen_experiment_spec")
    if content_hash(frozen_spec) != frozen_hash:
        raise ValueError("CALIBRATION_SPEC_UNAVAILABLE:frozen_experiment_spec_hash_mismatch")
    # Verify the original hash first; canonicalization is comparison-only and
    # must not rewrite already frozen audit evidence.
    frozen_grid = frozen_spec.get("parameter_grid")
    if not isinstance(frozen_grid, list):
        raise ValueError("CALIBRATION_SPEC_UNAVAILABLE:invalid_frozen_parameter_grid")
    try:
        canonical_grid = build_parameter_grid(
            spec["target_parameter"], current_value=current_value, requested=frozen_grid,
        )
    except ValueError as exc:
        raise ValueError("CALIBRATION_SPEC_UNAVAILABLE:invalid_frozen_parameter_grid") from exc
    canonical_spec = {
        **frozen_spec,
        "target_parameter": normalise_parameter(frozen_spec.get("target_parameter")),
        "parameter_grid": [float(value) for value in canonical_grid],
    }
    if content_hash(canonical_spec) != content_hash(spec):
        raise ValueError(f"CALIBRATION_SPEC_CONFLICT:experiment_is_frozen:{frozen_hash}")
    if row.status != "COMPLETED":
        raise ValueError("CALIBRATION_SPEC_PENDING:experiment_is_in_progress")


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
    if cases is None and backtest_run.status != "COMPLETED":
        raise ValueError("calibration_requires_completed_backtest_run")
    target_parameter = normalise_parameter(target_parameter)
    config = backtest_run.baseline_config_json or current_production_config()
    current_value = current_parameter_value(target_parameter, config)
    grid = build_parameter_grid(
        target_parameter,
        current_value=current_value,
        requested=parameter_grid,
    )
    explicit_cases = list(cases) if cases is not None else None
    summary = backtest_run.result_summary_json if isinstance(backtest_run.result_summary_json, dict) else {}
    source_lineage = summary.get("source_lineage") if isinstance(summary.get("source_lineage"), dict) else {}
    manifest = backtest_run.data_manifest_json if isinstance(backtest_run.data_manifest_json, dict) else {}
    source_set_hash = source_lineage.get("source_set_hash") or manifest.get("frozen_source_set_hash")
    if not source_set_hash and manifest.get("frozen_source_ids") is not None:
        source_set_hash = content_hash(sorted(manifest["frozen_source_ids"]))
    spec = {
        "backtest_run_id": backtest_run.id,
        "target_parameter": target_parameter,
        # Numeric aliases/order/duplicates are the same experiment, not new
        # opportunities to re-open the fixed final holdout.
        "parameter_grid": [float(value) for value in grid],
        "random_seed": random_seed,
        "bootstrap_iterations": bootstrap_iterations,
        "baseline_config_hash": content_hash(config),
        "data_hash": backtest_run.data_hash,
        "source_set_hash": source_set_hash,
        "backtest_engine_version": backtest_run.engine_version,
        "config_version": backtest_run.config_version,
        "parameter_set_hash": backtest_run.parameter_set_hash,
        "metrics_engine_version": METRICS_ENGINE_VERSION,
        "calibration_version": CALIBRATION_ENGINE_VERSION,
        "explicit_cases_hash": content_hash(explicit_cases) if explicit_cases is not None else None,
    }
    spec_hash = content_hash(spec)
    existing = _existing_calibration_for_parameter(
        db, backtest_run_id=backtest_run.id, target_parameter=target_parameter,
    )
    if existing is not None:
        _check_frozen_calibration_spec(existing, spec, current_value=current_value)
        return existing
    row = CalibrationReport(
        backtest_run_id=backtest_run.id,
        user_id=backtest_run.user_id,
        portfolio_id=backtest_run.portfolio_id,
        status="RUNNING",
        target_parameter=target_parameter,
        recommendation="INSUFFICIENT_EVIDENCE",
        report_json={"experiment_spec": spec, "experiment_spec_hash": spec_hash, "no_auto_apply": True},
        calibration_version=CALIBRATION_ENGINE_VERSION,
    )
    # Claim the existing unique experiment key before reading outcome rows.
    # A concurrent first request cannot evaluate a second final holdout.
    reservation = db.begin_nested()
    try:
        db.add(row)
        db.flush()
    except IntegrityError:
        reservation.rollback()
        existing = _existing_calibration_for_parameter(
            db, backtest_run_id=backtest_run.id, target_parameter=target_parameter,
        )
        if existing is None:
            raise
        _check_frozen_calibration_spec(existing, spec, current_value=current_value)
        return existing
    except BaseException:
        reservation.rollback()
        raise
    try:
        if cases is None:
            try:
                case_rows, current_source_ids = load_backtest_rows_with_sources(db, run=backtest_run)
            except (ReplayDataQualityError, ValueError):
                case_rows, current_source_ids = [], []
            frozen_source_ids = manifest.get("frozen_source_ids")
            if frozen_source_ids is not None and sorted(current_source_ids) != sorted(frozen_source_ids):
                raise ValueError("CALIBRATION_SOURCE_SET_CHANGED")
            frozen_hash = source_lineage.get("source_set_hash")
            if frozen_hash and content_hash(sorted(current_source_ids)) != frozen_hash:
                raise ValueError("CALIBRATION_SOURCE_SET_CHANGED")
        else:
            case_rows = explicit_cases
        evidence = build_calibration_evidence(
            case_rows,
            target_parameter=target_parameter,
            parameter_grid=spec["parameter_grid"],
            seed=random_seed,
            bootstrap_iterations=bootstrap_iterations,
            production_config=config,
            quality_status=backtest_run.quality_status,
            leakage_status=backtest_run.leakage_status,
            availability_manifest=backtest_run.data_manifest_json,
            replay_mode=backtest_run.replay_mode,
            replay_capability=_replay_capability(backtest_run.data_manifest_json, backtest_run.scope),
            scope=backtest_run.scope,
            censored_sample=(
                any(bool(item.get("censored_sample")) for item in case_rows)
                if backtest_run.replay_mode == "DETERMINISTIC_RECOMPUTE"
                else backtest_run.scope == "CANDIDATE" or any(bool(item.get("censored_sample")) for item in case_rows)
            ),
        )
        row.current_value_json = evidence.get("current_value")
        row.challenger_value_json = evidence.get("challenger_value")
        row.recommendation = evidence.get("recommendation", "INSUFFICIENT_EVIDENCE")
        row.train_metrics_json = evidence.get("train")
        row.validation_metrics_json = evidence.get("validation")
        row.test_metrics_json = evidence.get("test")
        row.robustness_json = evidence.get("robustness")
        row.sample_counts_json = evidence.get("sample_counts")
        row.risk_notes_json = evidence.get("known_limitations", [])
        row.proposal_json = {
            "target_parameter": target_parameter,
            "current_value": evidence.get("current_value"),
            "challenger_value": evidence.get("challenger_value"),
            "recommendation": evidence.get("recommendation"),
            "human_review_required": True,
            "apply_supported": False,
        }
        row.report_json = {
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
            "experiment_spec": spec,
            "experiment_spec_hash": spec_hash,
            "no_auto_apply": True,
        }
        row.status = "COMPLETED"
        row._allow_research_update = True
        db.flush()
        reservation.commit()
    except BaseException:
        reservation.rollback()
        raise
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
