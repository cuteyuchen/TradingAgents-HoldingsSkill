"""Equivalent parameter names must share one immutable calibration experiment."""
from __future__ import annotations

from datetime import date

import pytest

from app.research.calibration import apply_parameter_variant, build_parameter_grid, normalise_parameter
from app.research.config import CALIBRATION_ENGINE_VERSION, METRICS_ENGINE_VERSION, current_production_config
from app.research.models import BacktestRun, CalibrationReport
from app.research.replay import content_hash
from app.research.service import create_calibration_report
from test_research_phase_i import _db


CANONICAL_PARAMETER = "candidate.action_entry_min"


@pytest.fixture
def db():
    session = _db()
    try:
        yield session
    finally:
        session.close()


def _run(db):
    row = BacktestRun(
        scope="CANDIDATE", replay_mode="PRODUCTION_REPLAY",
        start_date=date(2026, 8, 20), end_date=date(2026, 8, 20),
        status="COMPLETED", quality_status="FULL", leakage_status="PASS",
        baseline_config_json=current_production_config(),
        data_hash="c" * 64, calculation_key="calibration-identity",
    )
    db.add(row)
    db.flush()
    return row


def _legacy_report(
    db, run, *, parameter=" entry ", status="COMPLETED", frozen=True,
    version=None, corrupt_hash=False, grid=None,
):
    # Model pre-fix persisted aliases without modifying a completed audit row.
    spec = {
        "backtest_run_id": run.id,
        "target_parameter": parameter,
        "parameter_grid": [65.0] if grid is None else grid,
        "random_seed": 0,
        "bootstrap_iterations": 1,
        "baseline_config_hash": content_hash(run.baseline_config_json),
        "data_hash": run.data_hash,
        "source_set_hash": None,
        "backtest_engine_version": run.engine_version,
        "config_version": run.config_version,
        "parameter_set_hash": run.parameter_set_hash,
        "metrics_engine_version": METRICS_ENGINE_VERSION,
        "calibration_version": version or CALIBRATION_ENGINE_VERSION,
        "explicit_cases_hash": content_hash([]),
    }
    report = {"no_auto_apply": True}
    if frozen:
        report.update(
            experiment_spec=spec,
            experiment_spec_hash="invalid-hash" if corrupt_hash else content_hash(spec),
        )
    row = CalibrationReport(
        backtest_run_id=run.id, target_parameter=parameter,
        status=status, recommendation="INSUFFICIENT_EVIDENCE",
        report_json=report, calibration_version=spec["calibration_version"],
    )
    db.add(row)
    db.flush()
    return row


def _request(db, run, parameter=CANONICAL_PARAMETER, **changes):
    options = {"parameter_grid": [65], "bootstrap_iterations": 1, "cases": [], **changes}
    return create_calibration_report(
        db, backtest_run=run, target_parameter=parameter, **options,
    )


def _no_repeat_holdout(*args, **kwargs):
    pytest.fail("Equivalent experiment identities must not evaluate a second holdout.")


@pytest.mark.parametrize("first_parameter", ["entry", " entry_threshold ", " candidate.action_entry_min "])
def test_first_calibration_alias_claims_canonical_key(db, monkeypatch, first_parameter):
    run = _run(db)
    first = _request(db, run, first_parameter)
    assert first.target_parameter == CANONICAL_PARAMETER
    assert first.report_json["experiment_spec"]["target_parameter"] == CANONICAL_PARAMETER
    assert first.proposal_json["target_parameter"] == CANONICAL_PARAMETER
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    for alias in (CANONICAL_PARAMETER, "entry", "entry_threshold", " entry "):
        assert _request(db, run, alias).id == first.id
        with pytest.raises(ValueError, match="CALIBRATION_SPEC_CONFLICT"):
            _request(db, run, alias, random_seed=1)
    assert db.query(CalibrationReport).count() == 1


def test_existing_alias_report_is_reused_without_rewriting_frozen_evidence(db, monkeypatch):
    run = _run(db)
    legacy = _legacy_report(db, run)
    old_report = dict(legacy.report_json)
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    assert _request(db, run).id == legacy.id
    assert legacy.target_parameter == " entry "
    assert legacy.report_json == old_report
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_CONFLICT"):
        _request(db, run, "entry_threshold", random_seed=1)
    assert db.query(CalibrationReport).count() == 1


@pytest.mark.parametrize("parameter", [CANONICAL_PARAMETER, "entry_threshold"])
def test_legacy_alias_without_frozen_spec_blocks_equivalent_new_experiment(db, parameter):
    run = _run(db)
    _legacy_report(db, run, frozen=False)
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_UNAVAILABLE"):
        _request(db, run, parameter)
    assert db.query(CalibrationReport).count() == 1


def test_pending_legacy_alias_is_not_reopened_by_canonical_request(db, monkeypatch):
    run = _run(db)
    _legacy_report(db, run, status="RUNNING")
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_PENDING"):
        _request(db, run)
    assert db.query(CalibrationReport).count() == 1


def test_duplicate_legacy_effective_parameters_require_review_not_new_holdout(db, monkeypatch):
    run = _run(db)
    _legacy_report(db, run, parameter="entry")
    _legacy_report(db, run, parameter=CANONICAL_PARAMETER)
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_CONFLICT"):
        _request(db, run, "entry_threshold")
    assert db.query(CalibrationReport).count() == 2


def test_old_calibration_version_cannot_be_reopened_using_an_alias(db, monkeypatch):
    run = _run(db)
    _legacy_report(db, run, version="calibration-v1")
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_CONFLICT"):
        _request(db, run)
    assert db.query(CalibrationReport).count() == 1


def test_corrupted_legacy_frozen_spec_is_not_silently_reused(db, monkeypatch):
    run = _run(db)
    _legacy_report(db, run, corrupt_hash=True)
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_UNAVAILABLE.*hash_mismatch"):
        _request(db, run)
    assert db.query(CalibrationReport).count() == 1


@pytest.mark.parametrize("alias", [
    "candidate.factor_ablation.trend", " candidate.stock.factor_ablation.trend ",
])
def test_equivalent_ablation_paths_share_one_holdout(db, monkeypatch, alias):
    run = _run(db)
    parameter = "factor_ablation.trend"
    assert apply_parameter_variant(run.baseline_config_json, alias, 0) == apply_parameter_variant(
        run.baseline_config_json, parameter, 0,
    )
    first = _request(db, run, alias, parameter_grid=[0, 1])
    assert first.target_parameter == parameter
    assert normalise_parameter(alias) == parameter
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    assert _request(db, run, parameter, parameter_grid=[1, 0]).id == first.id
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_CONFLICT"):
        _request(db, run, parameter, parameter_grid=[0, 1], random_seed=1)
    assert db.query(CalibrationReport).count() == 1


@pytest.mark.parametrize("grid", [[0.0, 1.0], [-0.0, 1.0]])
def test_existing_ablation_alias_is_reused_without_rewriting_evidence(db, monkeypatch, grid):
    run = _run(db)
    legacy = _legacy_report(db, run, parameter="candidate.factor_ablation.trend", grid=grid)
    old_report = dict(legacy.report_json)
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    assert _request(db, run, "factor_ablation.trend", parameter_grid=[0, 1]).id == legacy.id
    assert legacy.target_parameter == "candidate.factor_ablation.trend"
    assert legacy.report_json == old_report
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_CONFLICT"):
        _request(db, run, "factor_ablation.trend", parameter_grid=[0, 1], random_seed=1)
    assert db.query(CalibrationReport).count() == 1


@pytest.mark.parametrize("grid", [["-0", 1], [-0.0, 0, "0.0", 1]])
def test_signed_zero_does_not_change_frozen_experiment_identity(db, monkeypatch, grid):
    run = _run(db)
    parameter = "factor_ablation.trend"
    first = _request(db, run, parameter, parameter_grid=grid)
    assert first.report_json["experiment_spec"]["parameter_grid"] == [0.0, 1.0]
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    assert _request(db, run, parameter, parameter_grid=[0, 1]).id == first.id
    assert db.query(CalibrationReport).count() == 1


def test_stock_and_etf_weight_parameters_remain_distinct_experiments(db):
    run = _run(db)
    paths = ("candidate.stock_factor_weights.valuation", "candidate.etf_factor_weights.valuation")
    rows = [_request(db, run, path, parameter_grid=[0.2]) for path in paths]
    assert [row.target_parameter for row in rows] == list(paths)
    assert rows[0].id != rows[1].id


@pytest.mark.parametrize("grid", [
    ["65", 65], [65.0, "65.00", "6.5e1"], [" 65 ", 65, "65.0"],
])
def test_numeric_grid_aliases_are_deduplicated_before_spec_freeze(db, monkeypatch, grid):
    assert build_parameter_grid(CANONICAL_PARAMETER, requested=grid) == [65.0]
    run = _run(db)
    first = _request(db, run, parameter_grid=grid)
    assert first.report_json["experiment_spec"]["parameter_grid"] == [65.0]
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    assert _request(db, run).id == first.id
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_CONFLICT"):
        _request(db, run, parameter_grid=[65, 66])
    assert db.query(CalibrationReport).count() == 1


@pytest.mark.parametrize("grid", [[65.0, 65.0], ["65", 65]])
def test_legacy_duplicate_numeric_grid_is_reused_without_rewriting_hash(db, monkeypatch, grid):
    run = _run(db)
    legacy = _legacy_report(db, run, grid=grid)
    old_report = dict(legacy.report_json)
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    assert _request(db, run).id == legacy.id
    assert legacy.report_json == old_report
    assert legacy.report_json["experiment_spec_hash"] == content_hash(
        legacy.report_json["experiment_spec"],
    )
    assert db.query(CalibrationReport).count() == 1


@pytest.mark.parametrize("grid", ["65", [None], ["nan"]])
def test_invalid_legacy_grid_is_not_reopened_or_rewritten(db, monkeypatch, grid):
    run = _run(db)
    legacy = _legacy_report(db, run, grid=grid)
    old_report = dict(legacy.report_json)
    monkeypatch.setattr("app.research.service.build_calibration_evidence", _no_repeat_holdout)
    with pytest.raises(ValueError, match="CALIBRATION_SPEC_UNAVAILABLE.*invalid_frozen_parameter_grid"):
        _request(db, run)
    assert legacy.report_json == old_report
    assert db.query(CalibrationReport).count() == 1


@pytest.mark.parametrize(
    "value", ["nan", "inf", "1e309", 10 ** 1000],
    ids=["nan", "infinity", "numeric-string-overflow", "integer-overflow"],
)
def test_numeric_grid_rejects_non_finite_and_overflowing_values(value):
    with pytest.raises(ValueError, match="calibration_grid_values_must_be_finite"):
        build_parameter_grid(CANONICAL_PARAMETER, requested=[value])
