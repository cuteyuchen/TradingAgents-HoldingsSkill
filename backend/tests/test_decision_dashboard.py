from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.memory.models import DecisionMemory
from app.operations.dashboard import _analysis_section, _decision_section, build_daily_dashboard
from app.v2_models import AnalysisJob, AnalysisRun, PortfolioSnapshot
from test_daily_operations import _db, _local, _portfolio_fixture, _utc_naive


@pytest.fixture
def decision_db():
    db = _db()
    user, portfolio, snapshot = _portfolio_fixture(db)
    yield db, user, portfolio, snapshot
    db.close()


def _persist_decision(fixture, result, *, validity=None, with_memory=True, completed_at=None, created_at=None, status="succeeded"):
    db, user, portfolio, snapshot = fixture
    completed_at = completed_at or _utc_naive(_local(date(2026, 8, 20), 9, 35))
    created_at = created_at or completed_at
    job = AnalysisJob(
        user_id=user.id, portfolio_id=portfolio.id, snapshot_id=snapshot.id,
        mode="standard", status=status, current_stage="completed",
        idempotency_key=f"decision-dashboard-{completed_at.isoformat()}", created_at=created_at,
        finished_at=completed_at if status == "succeeded" else None,
    )
    db.add(job)
    db.flush()
    structured = {"result": result}
    if validity is not None:
        structured["decision_validity"] = validity
    run = AnalysisRun(
        job_id=job.id, user_id=user.id, portfolio_snapshot_id=snapshot.id,
        final_rating="no_action", data_quality_grade="A", markdown_text="fixture",
        created_at=created_at, structured_result_json=structured,
    )
    db.add(run)
    db.flush()
    if with_memory:
        db.add(DecisionMemory(
            user_id=user.id, portfolio_id=portfolio.id, analysis_run_id=run.id,
            analysis_job_id=job.id, portfolio_snapshot_id=snapshot.id,
            trade_date=date(2026, 8, 20), decision_at=completed_at, available_at=completed_at,
            analysis_mode="standard", decision_type="NO_ACTION", final_rating="no_action",
            portfolio_action="NO_ACTION", quality_status="VALID", confidence=0.9,
            holding_decisions_json=[{"code": "600519", "recommended_action": "hold", "recommended_qty": None}],
            created_at=completed_at,
        ))
    db.commit()
    return run


def _read_decision(fixture, *, hour=10, minute=0):
    db, user, portfolio, _snapshot = fixture
    return _decision_section(
        db, user_id=user.id, portfolio_id=portfolio.id,
        cutoff=_utc_naive(_local(date(2026, 8, 20), hour, minute)),
    )


@pytest.mark.parametrize("result,expected", [
    (None, "INCOMPLETE"),
    ({}, "INCOMPLETE"),
    ({"final_rating": "no_action", "holdings": []}, "INCOMPLETE"),
    ({"holdings": [{"code": "600519"}]}, "INCOMPLETE"),
    ({"holdings": [{"code": "600519", "action": "hold"}]}, "NO_ACTION"),
    ({"holdings": [{"code": "600519", "action": "watch"}]}, "WAITING"),
    ({"holdings": [{"code": "600519", "action": "watch", "portfolio_gate": "BLOCKED"}]}, "DATA_GAP"),
])
def test_dashboard_uses_original_actions_instead_of_legacy_no_action_default(decision_db, result, expected):
    _persist_decision(decision_db, result)
    section = _read_decision(decision_db)
    assert section["decision_status"] == section["latest"]["conclusion"] == expected
    assert section["latest"]["portfolio_snapshot_id"] == decision_db[3].id
    assert section["latest"]["validity_status"] == "UNVERIFIED"
    assert section["latest"]["valid_until"] is None
    if result and result.get("holdings"):
        assert section["latest"]["holding_actions"][0]["decision_status"] == expected


def test_dashboard_without_analysis_is_incomplete(decision_db):
    db, user, portfolio, _snapshot = decision_db
    dashboard = build_daily_dashboard(db, user_id=user.id, portfolio_id=portfolio.id, as_of=_local(date(2026, 8, 20), 10))
    assert dashboard["decisions"]["latest"] is None
    assert dashboard["decisions"]["final_action"] == "INCOMPLETE"


def test_new_portfolio_snapshot_expires_previous_decision(decision_db):
    _persist_decision(decision_db, {"holdings": [{"code": "600519", "action": "hold"}]})
    db, user, portfolio, _snapshot = decision_db
    db.add(PortfolioSnapshot(
        user_id=user.id, portfolio_id=portfolio.id, status="confirmed",
        snapshot_time=_utc_naive(_local(date(2026, 8, 20), 9, 50)),
    ))
    db.commit()
    section = _read_decision(decision_db)
    assert section["latest"]["decision_status"] == "EXPIRED"
    assert section["latest"]["validity_reason"] == "PORTFOLIO_SNAPSHOT_CHANGED"


def test_stale_portfolio_uses_existing_freshness_window(decision_db):
    decision_db[3].snapshot_time -= timedelta(days=2)
    _persist_decision(decision_db, {"holdings": [{"code": "600519", "action": "hold"}]})
    section = _read_decision(decision_db)
    assert section["latest"]["decision_status"] == "EXPIRED"
    assert section["latest"]["validity_reason"] == "PORTFOLIO_SNAPSHOT_STALE"


def test_only_explicit_server_validity_window_can_confirm_validity(decision_db):
    _persist_decision(
        decision_db, {"holdings": [{"code": "600519", "action": "hold"}]},
        validity={"valid_until": "2026-08-20T10:30:00+08:00"},
    )
    current = _read_decision(decision_db)
    assert current["latest"]["validity_status"] == "VALID"
    assert current["latest"]["decision_status"] == "NO_ACTION"
    expired = _read_decision(decision_db, hour=10, minute=30)
    assert expired["latest"]["validity_status"] == expired["latest"]["decision_status"] == "EXPIRED"
    assert expired["latest"]["validity_reason"] == "VALIDITY_WINDOW_EXPIRED"


def test_model_deadline_is_not_a_verified_validity_window(decision_db):
    _persist_decision(decision_db, {
        "holdings": [{"code": "600519", "action": "hold"}],
        "valid_until": "2026-08-20T10:30:00+08:00",
        "validity_status": "VALID",
    })
    section = _read_decision(decision_db)
    assert section["latest"]["validity_status"] == "UNVERIFIED"
    assert section["latest"]["valid_until"] is None


def test_analysis_fallback_exposes_same_lineage_and_state_without_memory(decision_db):
    run = _persist_decision(decision_db, {"holdings": [{"code": "600519", "action": "watch"}]}, with_memory=False)
    db, user, portfolio, snapshot = decision_db
    section = _analysis_section(db, user_id=user.id, portfolio_id=portfolio.id, cutoff=_utc_naive(_local(date(2026, 8, 20), 10)))
    assert section["latest"]["analysis_run_id"] == run.id
    assert section["latest"]["portfolio_snapshot_id"] == snapshot.id
    assert section["latest"]["decision_status"] == "WAITING"
    assert section["latest"]["validity_status"] == "UNVERIFIED"
    assert section["latest"]["holding_actions"][0]["action"] == "watch"


@pytest.mark.parametrize("blocked", [False, True])
def test_newer_completed_analysis_replaces_older_memory_even_with_lower_run_id(decision_db, blocked):
    result = {"holdings": [{"code": "600519", "action": "reduce", "quantity": 100}]}
    if blocked:
        result["decision_gate"] = {"status": "BLOCKED", "portfolio_action": "WATCH_ONLY"}
        result["holdings"][0].update(action="watch", portfolio_gate="BLOCKED")
    newer = _persist_decision(
        decision_db, result, with_memory=False,
        created_at=_utc_naive(_local(date(2026, 8, 20), 9, 20)),
        completed_at=_utc_naive(_local(date(2026, 8, 20), 9, 50)),
    )
    older = _persist_decision(decision_db, {"holdings": [{"code": "600519", "action": "hold"}]})
    assert newer.id < older.id
    section = _read_decision(decision_db)
    assert section["latest"]["analysis_run_id"] == newer.id
    assert section["latest"]["decision_source"] == "ANALYSIS_RUN"
    assert section["decision_status"] == ("DATA_GAP" if blocked else "ACTION")
    db, user, portfolio, _snapshot = decision_db
    dashboard = build_daily_dashboard(db, user_id=user.id, portfolio_id=portfolio.id, as_of=_local(date(2026, 8, 20), 10))
    assert dashboard["analysis"]["latest"]["analysis_run_id"] == newer.id
    assert dashboard["decisions"]["latest"]["analysis_run_id"] == newer.id
    assert dashboard["decisions"]["final_action"] == section["decision_status"]


def test_in_progress_analysis_does_not_replace_completed_decision(decision_db):
    older = _persist_decision(decision_db, {"holdings": [{"code": "600519", "action": "hold"}]})
    _persist_decision(
        decision_db, {"holdings": [{"code": "600519", "action": "reduce"}]},
        with_memory=False, status="running", completed_at=_utc_naive(_local(date(2026, 8, 20), 9, 50)),
    )
    section = _read_decision(decision_db)
    assert section["latest"]["analysis_run_id"] == older.id
    assert section["latest"]["decision_source"] == "DECISION_MEMORY"
    assert section["decision_status"] == "NO_ACTION"
