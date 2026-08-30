"""Phase J parameter governance and versioned manual calibration contracts."""

from __future__ import annotations

import itertools
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.candidates.config import DEFAULT_CONFIG as CANDIDATE_DEFAULT
from app.database import Base
from app.governance.models import (
    ParameterChangeProposal,
    ParameterGovernanceEvent,
    ParameterSetVersion,
)
from app.governance.registry import (
    canonical_config_hash,
    candidate_config_from_snapshot,
    default_production_config,
    normalize_snapshot,
    set_path,
)
from app.governance.service import (
    GovernanceBlockedError,
    activate_parameter_set_version,
    approve_proposal,
    bootstrap_parameter_set,
    create_manual_proposal,
    create_proposal_from_calibration,
    create_rollback_proposal,
    get_active_parameter_set,
    governance_health,
    reject_proposal,
    resolve_production_parameters,
    submit_proposal,
    validate_parameter_set_version,
)
from app.portfolio_models import TradeLedgerEntry
from app.research.models import BacktestRun, CalibrationReport
from app.v2_models import User


_BACKTEST_SEQ = itertools.count(1)


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    import app.candidates.models  # noqa: F401
    import app.governance.models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.trigger_models  # noqa: F401

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _user(db: Session) -> User:
    row = User(email="governance@example.com", username="governance", password_hash="x")
    db.add(row)
    db.flush()
    return row


def _backtest(
    db: Session,
    key: str = "governance-backtest-1",
    *,
    lineage: dict[str, object] | None = None,
) -> BacktestRun:
    row = BacktestRun(
        user_id=None,
        portfolio_id=None,
        scope="MARKET",
        replay_mode="PRODUCTION_REPLAY",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status="COMPLETED",
        config_version="candidate-engine-v1",
        engine_version="historical-replay-v1",
        data_hash="data-hash",
        calculation_key=key,
        horizons_json=[1],
    )
    if lineage:
        row.parameter_set_version_id = lineage["version_id"]
        row.parameter_set_version = lineage["version"]
        row.parameter_set_hash = lineage["config_hash"]
    db.add(row)
    db.flush()
    return row


def _calibration(
    db: Session,
    *,
    recommendation: str = "CONSIDER_CHANGE",
    target: str = "candidate.min_decision_edge",
    current: float = 5.0,
    challenger: float = 6.0,
) -> CalibrationReport:
    active = get_active_parameter_set(db)
    lineage = None
    if active is not None:
        lineage = {
            "version_id": active.id,
            "version": f"v{active.version}",
            "config_hash": active.config_hash,
        }
    run = _backtest(
        db,
        key=f"governance-backtest-{next(_BACKTEST_SEQ)}-{recommendation}-{target}",
        lineage=lineage,
    )
    row = CalibrationReport(
        backtest_run_id=run.id,
        user_id=None,
        portfolio_id=None,
        status="COMPLETED",
        target_parameter=target,
        current_value_json=current,
        challenger_value_json=challenger,
        recommendation=recommendation,
        report_json={"replay_mode": "PRODUCTION_REPLAY", "global_final_test": {}},
    )
    db.add(row)
    db.flush()
    return row


def _approved_version(
    db: Session,
    *,
    snapshot: dict,
    version: int,
    source_proposal_id: int | None = None,
    base_id: int | None = None,
) -> ParameterSetVersion:
    row = ParameterSetVersion(
        version=version,
        status="APPROVED",
        snapshot_json=snapshot,
        diff_json={"target": "test"},
        config_hash=canonical_config_hash(snapshot),
        runtime_contract_version="2.4.0",
        decision_contract_version="2.4.0",
        source_proposal_id=source_proposal_id,
        parent_version_id=base_id,
    )
    db.add(row)
    db.flush()
    return row


def test_bootstrap_is_idempotent_and_resolver_uses_active_snapshot(db: Session):
    first = bootstrap_parameter_set(db)
    second = bootstrap_parameter_set(db)
    assert first.id == second.id
    assert first.status == "ACTIVE"
    context = resolve_production_parameters(db)
    assert context["version"] == "v1"
    assert context["snapshot"]["candidate"]["min_decision_edge"] == 5.0


def test_calibration_consider_change_never_applies_automatically(db: Session):
    active = bootstrap_parameter_set(db)
    report = _calibration(db, recommendation="CONSIDER_CHANGE", target="candidate.min_decision_edge", challenger=6.0)
    db.commit()
    proposal = create_proposal_from_calibration(
        db,
        calibration_report=report,
        proposed_value=6.0,
        user_id=1,
        reason="evidence",
    )
    db.commit()
    assert proposal.status == "DRAFT"
    assert get_active_parameter_set(db).id == active.id
    assert resolve_production_parameters(db)["snapshot"]["candidate"]["min_decision_edge"] == 5.0
    assert db.execute(select(func.count()).select_from(TradeLedgerEntry)).scalar_one() == 0


def test_approve_creates_version_but_does_not_activate(db: Session):
    active = bootstrap_parameter_set(db)
    report = _calibration(db, target="candidate.min_decision_edge", challenger=6.0)
    db.commit()
    proposal = create_proposal_from_calibration(db, calibration_report=report, proposed_value=6.0, user_id=1)
    db.commit()
    submit_proposal(db, proposal=proposal, user_id=1)
    db.commit()
    approved = approve_proposal(db, proposal=proposal, reviewer_user_id=1)
    db.commit()
    assert approved.status == "APPROVED"
    assert get_active_parameter_set(db).id == active.id
    assert resolve_production_parameters(db)["snapshot"]["candidate"]["min_decision_edge"] == 5.0
    validate_parameter_set_version(db, version=approved, actor_user_id=1)
    db.commit()
    assert approved.validation_status == "PASS"


def test_activation_atomic_and_resolver_switches_snapshot(db: Session):
    active = bootstrap_parameter_set(db)
    report = _calibration(db, target="candidate.min_decision_edge", challenger=6.0)
    db.commit()
    proposal = create_proposal_from_calibration(db, calibration_report=report, proposed_value=6.0, user_id=1)
    db.commit()
    submit_proposal(db, proposal=proposal, user_id=1)
    db.commit()
    approved = approve_proposal(db, proposal=proposal, reviewer_user_id=1)
    db.commit()
    validate_parameter_set_version(db, version=approved, actor_user_id=1)
    db.commit()
    activate_parameter_set_version(
        db,
        version=approved,
        actor_user_id=1,
        emergency_override=True,
        reason="test",
    )
    db.commit()
    active = db.get(ParameterSetVersion, active.id)
    assert active.status == "SUPERSEDED"
    assert approved.status == "ACTIVE"
    assert resolve_production_parameters(db)["version"] == "v2"
    assert resolve_production_parameters(db)["snapshot"]["candidate"]["min_decision_edge"] == 6.0
    assert db.execute(select(func.count()).select_from(TradeLedgerEntry)).scalar_one() == 0


def test_stale_base_proposal_fails_approval(db: Session):
    bootstrap_parameter_set(db)
    report_1 = _calibration(db, target="candidate.min_decision_edge", challenger=6.0, current=5.0)
    db.commit()
    stale = create_proposal_from_calibration(db, calibration_report=report_1, proposed_value=6.0, user_id=1)
    db.commit()
    submit_proposal(db, proposal=stale, user_id=1)
    db.commit()

    report_2 = _calibration(db, target="candidate.min_decision_edge", challenger=7.0, current=5.0)
    db.commit()
    current = create_proposal_from_calibration(db, calibration_report=report_2, proposed_value=7.0, user_id=1)
    db.commit()
    submit_proposal(db, proposal=current, user_id=1)
    db.commit()
    approved = approve_proposal(db, proposal=current, reviewer_user_id=1)
    db.commit()
    validate_parameter_set_version(db, version=approved, actor_user_id=1)
    db.commit()
    activate_parameter_set_version(
        db,
        version=approved,
        actor_user_id=1,
        emergency_override=True,
        reason="test",
    )
    db.commit()

    with pytest.raises(ValueError, match="STALE_BASE_VERSION"):
        approve_proposal(db, proposal=stale, reviewer_user_id=1)


def test_cross_gate_and_market_boundary_validation_block(db: Session):
    bootstrap_parameter_set(db)
    base = normalize_snapshot(default_production_config())
    base = set_path(base, "candidate.ready_entry_min", 65.0)
    invalid_gate = set_path(base, "candidate.action_entry_min", 60.0)
    gate_version = _approved_version(db, snapshot=invalid_gate, version=2)
    db.commit()
    assert validate_parameter_set_version(db, version=gate_version, actor_user_id=1).validation_status == "BLOCKED"

    invalid_market = set_path(base, "market.regime_lower_bounds.RISK_ON", 40.0)
    market_version = _approved_version(db, snapshot=invalid_market, version=3)
    db.commit()
    assert validate_parameter_set_version(db, version=market_version, actor_user_id=1).validation_status == "BLOCKED"


def test_protected_parameter_requires_manual_exception(db: Session):
    bootstrap_parameter_set(db)
    report = _calibration(
        db,
        target="portfolio.hard_caps.stock",
        current=0.2,
        challenger=0.25,
    )
    db.commit()
    with pytest.raises(ValueError, match="parameter_not_calibratable"):
        create_proposal_from_calibration(db, calibration_report=report, proposed_value=0.25, user_id=1)
    with pytest.raises(ValueError, match="manual_proposal_requires_manual_exception"):
        create_manual_proposal(
            db,
            target_parameter_key="portfolio.hard_caps.stock",
            proposed_value=0.25,
            user_id=1,
            reason="manual",
            proposal_type="STANDARD",
            risk_acknowledged=True,
        )
    with pytest.raises(ValueError, match="manual_proposal_requires_risk_acknowledgement"):
        create_manual_proposal(
            db,
            target_parameter_key="portfolio.hard_caps.stock",
            proposed_value=0.25,
            user_id=1,
            reason="manual",
            proposal_type="MANUAL_EXCEPTION",
            risk_acknowledged=False,
        )
    proposal = create_manual_proposal(
        db,
        target_parameter_key="portfolio.hard_caps.stock",
        proposed_value=0.25,
        user_id=1,
        reason="manual",
        proposal_type="MANUAL_EXCEPTION",
        risk_acknowledged=True,
        risk_summary={"risk": "high"},
    )
    db.commit()
    assert proposal.proposal_type == "MANUAL_EXCEPTION"
    assert proposal.risk_acknowledged is True


def test_manual_proposal_always_requires_manual_exception_and_risk_acknowledgement(db: Session):
    bootstrap_parameter_set(db)
    with pytest.raises(ValueError, match="manual_proposal_requires_manual_exception"):
        create_manual_proposal(
            db,
            target_parameter_key="candidate.min_decision_edge",
            proposed_value=6.0,
            user_id=1,
            reason="manual",
            proposal_type="STANDARD",
            risk_acknowledged=True,
        )
    with pytest.raises(ValueError, match="manual_proposal_requires_risk_acknowledgement"):
        create_manual_proposal(
            db,
            target_parameter_key="candidate.min_decision_edge",
            proposed_value=6.0,
            user_id=1,
            reason="manual",
            proposal_type="MANUAL_EXCEPTION",
            risk_acknowledged=False,
        )


def test_diagnostic_or_non_consider_change_cannot_create_standard_proposal(db: Session):
    bootstrap_parameter_set(db)
    report = _calibration(db, recommendation="INSUFFICIENT_EVIDENCE", target="candidate.min_decision_edge")
    db.commit()
    with pytest.raises(ValueError, match="calibration_not_consider_change"):
        create_proposal_from_calibration(db, calibration_report=report, proposed_value=6.0, user_id=1)


def test_rollback_creates_new_version_and_never_reactivates_old(db: Session):
    active = bootstrap_parameter_set(db)
    report = _calibration(db, target="candidate.min_decision_edge", challenger=6.0)
    db.commit()
    proposal = create_proposal_from_calibration(db, calibration_report=report, proposed_value=6.0, user_id=1)
    db.commit()
    submit_proposal(db, proposal=proposal, user_id=1)
    db.commit()
    approved = approve_proposal(db, proposal=proposal, reviewer_user_id=1)
    db.commit()
    validate_parameter_set_version(db, version=approved, actor_user_id=1)
    db.commit()
    activate_parameter_set_version(
        db,
        version=approved,
        actor_user_id=1,
        emergency_override=True,
        reason="test",
    )
    db.commit()

    rollback = create_rollback_proposal(db, target_version_id=active.id, user_id=1, reason="rollback")
    db.commit()
    submit_proposal(db, proposal=rollback, user_id=1)
    db.commit()
    rollback_version = approve_proposal(db, proposal=rollback, reviewer_user_id=1)
    db.commit()
    validate_parameter_set_version(db, version=rollback_version, actor_user_id=1)
    db.commit()
    activate_parameter_set_version(
        db,
        version=rollback_version,
        actor_user_id=1,
        emergency_override=True,
        reason="rollback",
    )
    db.commit()
    assert rollback_version.version == 3
    assert rollback_version.status == "ACTIVE"
    assert normalize_snapshot(rollback_version.snapshot_json) == normalize_snapshot(active.snapshot_json)
    assert db.get(ParameterSetVersion, active.id).status == "SUPERSEDED"
    assert resolve_production_parameters(db)["version"] == "v3"


def test_trading_session_blocks_ordinary_activation_and_emergency_requires_reason(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    bootstrap_parameter_set(db)
    report = _calibration(db, target="candidate.min_decision_edge", challenger=6.0)
    db.commit()
    proposal = create_proposal_from_calibration(db, calibration_report=report, proposed_value=6.0, user_id=1)
    db.commit()
    submit_proposal(db, proposal=proposal, user_id=1)
    db.commit()
    approved = approve_proposal(db, proposal=proposal, reviewer_user_id=1)
    db.commit()
    validate_parameter_set_version(db, version=approved, actor_user_id=1)
    db.commit()

    monkeypatch.setattr("app.governance.service._trading_session_blocked", lambda db: True)
    with pytest.raises(ValueError, match="BLOCKED_TRADING_SESSION"):
        activate_parameter_set_version(db, version=approved, actor_user_id=1)
    with pytest.raises(ValueError, match="emergency_reason_required"):
        activate_parameter_set_version(db, version=approved, actor_user_id=1, emergency_override=True)


def test_duplicate_active_is_blocked_and_fail_closed(db: Session):
    active = bootstrap_parameter_set(db)
    duplicate = ParameterSetVersion(
        version=2,
        status="ACTIVE",
        snapshot_json=active.snapshot_json,
        diff_json={},
        config_hash=active.config_hash,
        runtime_contract_version="2.4.0",
        decision_contract_version="2.4.0",
    )
    db.add(duplicate)
    db.commit()
    health = governance_health(db)
    assert health["status"] == "BLOCKED"
    assert "MULTIPLE_ACTIVE_PARAMETER_SETS" in health["reasons"]
    with pytest.raises(GovernanceBlockedError, match="MULTIPLE_ACTIVE_PARAMETER_SETS"):
        get_active_parameter_set(db)


def test_default_drift_reports_but_resolver_keeps_database_authority(db: Session):
    active = bootstrap_parameter_set(db)
    snapshot = normalize_snapshot(active.snapshot_json)
    snapshot = set_path(snapshot, "candidate.min_decision_edge", 6.0)
    active._allow_governance_update = True
    active.snapshot_json = snapshot
    active.config_hash = canonical_config_hash(snapshot)
    db.commit()
    health = governance_health(db)
    assert health["status"] == "DEGRADED"
    assert "PARAMETER_DEFAULT_DRIFT" in health["reasons"]
    assert resolve_production_parameters(db)["snapshot"]["candidate"]["min_decision_edge"] == 6.0


def test_audit_events_are_append_only(db: Session):
    bootstrap_parameter_set(db)
    event = db.execute(select(ParameterGovernanceEvent).order_by(ParameterGovernanceEvent.id)).scalars().first()
    assert event is not None
    with pytest.raises(RuntimeError, match="governance_record_is_immutable"):
        event._allow_governance_update = False
        event.metadata_json = {"tampered": True}
        db.flush()


def test_parameter_registry_drives_candidate_config(db: Session):
    bootstrap_parameter_set(db)
    config = candidate_config_from_snapshot(resolve_production_parameters(db)["snapshot"])
    assert config.min_decision_edge == CANDIDATE_DEFAULT.min_decision_edge
    assert config.no_action_thresholds == CANDIDATE_DEFAULT.no_action_thresholds


def test_backtest_run_freezes_parameter_set_lineage(db: Session):
    from app.research.runner import create_backtest_run

    active = bootstrap_parameter_set(db)
    run = create_backtest_run(
        db,
        scope="MARKET",
        replay_mode="BAR_ONLY_DIAGNOSTIC",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        horizons=[1],
    )
    db.commit()
    assert run.parameter_set_version_id == active.id
    assert run.parameter_set_version == "v1"
    assert run.parameter_set_hash == active.config_hash
    assert run.baseline_config_json["candidate"]["min_decision_edge"] == 5.0


def test_no_governance_history_without_active_is_blocked_not_guessed(db: Session):
    from app.governance.models import ParameterGovernanceEvent

    bootstrap_parameter_set(db)
    active = get_active_parameter_set(db)
    active._allow_governance_update = True
    active.status = "SUPERSEDED"
    active.deactivated_at = active.activated_at
    db.commit()
    with pytest.raises(GovernanceBlockedError, match="NO_ACTIVE_PARAMETER_SET_WITH_HISTORY"):
        bootstrap_parameter_set(db)


def test_resolver_blocks_legacy_fallback_when_history_exists_without_active(db: Session):
    bootstrap_parameter_set(db)
    active = get_active_parameter_set(db)
    active._allow_governance_update = True
    active.status = "SUPERSEDED"
    active.deactivated_at = active.activated_at
    db.commit()

    with pytest.raises(GovernanceBlockedError, match="NO_ACTIVE_PARAMETER_SET_WITH_HISTORY"):
        resolve_production_parameters(db)


def test_calibration_evidence_must_match_active_value_and_backtest_base(db: Session):
    bootstrap_parameter_set(db)
    report = _calibration(
        db,
        target="candidate.min_decision_edge",
        current=5.0,
        challenger=6.0,
    )
    db.commit()
    with pytest.raises(ValueError, match="CALIBRATION_VALUE_MISMATCH"):
        create_proposal_from_calibration(
            db,
            calibration_report=report,
            proposed_value=50.0,
            user_id=1,
            reason="forged",
        )

    wrong_current = _calibration(
        db,
        target="candidate.min_decision_edge",
        current=4.0,
        challenger=6.0,
    )
    db.commit()
    with pytest.raises(ValueError, match="CALIBRATION_VALUE_MISMATCH"):
        create_proposal_from_calibration(
            db,
            calibration_report=wrong_current,
            proposed_value=6.0,
            user_id=1,
            reason="stale current",
        )


def test_calibration_from_old_base_version_is_rejected(db: Session):
    v1 = bootstrap_parameter_set(db)
    report = _calibration(
        db,
        target="candidate.min_decision_edge",
        current=5.0,
        challenger=6.0,
    )
    db.commit()

    proposal = create_proposal_from_calibration(
        db,
        calibration_report=report,
        proposed_value=6.0,
        user_id=1,
        reason="evidence",
    )
    db.commit()
    submit_proposal(db, proposal=proposal, user_id=1)
    db.commit()
    approved = approve_proposal(db, proposal=proposal, reviewer_user_id=1)
    db.commit()
    validate_parameter_set_version(db, version=approved, actor_user_id=1)
    db.commit()
    activate_parameter_set_version(
        db,
        version=approved,
        actor_user_id=1,
        emergency_override=True,
        reason="test",
    )
    db.commit()

    old_run = _backtest(
        db,
        key=f"governance-backtest-old-base-{next(_BACKTEST_SEQ)}",
        lineage={
            "version_id": v1.id,
            "version": "v1",
            "config_hash": v1.config_hash,
        },
    )
    old_report = CalibrationReport(
        backtest_run_id=old_run.id,
        user_id=None,
        portfolio_id=None,
        status="COMPLETED",
        target_parameter="candidate.min_decision_edge",
        current_value_json=6.0,
        challenger_value_json=7.0,
        recommendation="CONSIDER_CHANGE",
        report_json={"replay_mode": "PRODUCTION_REPLAY"},
    )
    db.add(old_report)
    db.commit()
    with pytest.raises(ValueError, match="CALIBRATION_BASE_VERSION_CHANGED"):
        create_proposal_from_calibration(
            db,
            calibration_report=old_report,
            proposed_value=7.0,
            user_id=1,
            reason="old evidence",
        )
