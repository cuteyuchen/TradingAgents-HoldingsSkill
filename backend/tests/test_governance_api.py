"""Phase J governance REST flow: proposals never auto-apply and activation is explicit."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.governance.models import ParameterChangeProposal, ParameterSetVersion
from app.governance.service import bootstrap_parameter_set
import app.main as main_module
from app.portfolio_models import TradeLedgerEntry
from app.research.models import BacktestRun, CalibrationReport
from app.v2_dependencies import get_current_user
from app.v2_models import User


def test_governance_api_full_flow_without_auto_apply_or_auto_trade():
    import app.candidates.models  # noqa: F401
    import app.governance.models  # noqa: F401
    import app.market_engine_models  # noqa: F401
    import app.market_models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.trigger_models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user = User(email="governance-api@example.com", username="governance-api", password_hash="hash")
        db.add(user)
        db.flush()
        bootstrap_parameter_set(db)
        active = db.execute(
            select(ParameterSetVersion).where(ParameterSetVersion.status == "ACTIVE")
        ).scalar_one()
        run = BacktestRun(
            user_id=user.id,
            portfolio_id=None,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status="COMPLETED",
            data_hash="hash",
            calculation_key="governance-api-run",
        )
        run.parameter_set_version_id = active.id
        run.parameter_set_version = "v1"
        run.parameter_set_hash = active.config_hash
        db.add(run)
        db.flush()
        report = CalibrationReport(
            backtest_run_id=run.id,
            user_id=user.id,
            portfolio_id=None,
            status="COMPLETED",
            target_parameter="candidate.min_decision_edge",
            current_value_json=5.0,
            challenger_value_json=6.0,
            recommendation="CONSIDER_CHANGE",
            report_json={"replay_mode": "PRODUCTION_REPLAY"},
        )
        db.add(report)
        db.commit()
        user_id = user.id
        active_id = active.id
        report_id = report.id

    def override_db():
        with Session(engine) as db:
            yield db

    main_module.app.dependency_overrides[get_db] = override_db
    main_module.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, status="active")
    client = TestClient(main_module.app)
    try:
        registry = client.get("/api/v3/governance/parameters")
        assert registry.status_code == 200
        assert registry.json()["registry"]["candidate.min_decision_edge"]["current_value"] == 5.0

        active_before = client.get("/api/v3/governance/parameter-sets/active")
        assert active_before.status_code == 200
        assert active_before.json()["version"] == 1

        created = client.post(
            "/api/v3/governance/proposals/from-calibration",
            json={"calibration_report_id": report_id, "proposed_value": 6.0, "reason": "api flow"},
        )
        assert created.status_code == 200, created.text
        proposal = created.json()
        assert proposal["status"] == "DRAFT"
        proposal_id = proposal["id"]

        submitted = client.post(f"/api/v3/governance/proposals/{proposal_id}/submit")
        assert submitted.status_code == 200
        assert submitted.json()["status"] == "PENDING_REVIEW"

        approved = client.post(f"/api/v3/governance/proposals/{proposal_id}/approve", json={"review_comment": "ok"})
        assert approved.status_code == 200
        version = approved.json()["version"]
        assert version["status"] == "APPROVED"
        assert client.get("/api/v3/governance/parameter-sets/active").json()["version"] == 1

        validated = client.post(f"/api/v3/governance/parameter-sets/{version['id']}/validate")
        assert validated.status_code == 200
        assert validated.json()["validation_status"] == "PASS"

        activated = client.post(
            f"/api/v3/governance/parameter-sets/{version['id']}/activate",
            json={"expected_active_version_id": active_id, "reason": "test activation"},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == "ACTIVE"
        assert client.get("/api/v3/governance/parameter-sets/active").json()["version"] == 2

        health = client.get("/api/v3/governance/health")
        assert health.status_code == 200
        assert health.json()["active"]["version"] == 2

        events = client.get("/api/v3/governance/events")
        assert events.status_code == 200
        event_types = {item["event_type"] for item in events.json()["events"]}
        assert {"PROPOSAL_CREATED", "PROPOSAL_SUBMITTED", "PROPOSAL_APPROVED", "VERSION_ACTIVATED"} <= event_types

        with Session(engine) as db:
            assert db.execute(select(func.count()).select_from(TradeLedgerEntry)).scalar_one() == 0
    finally:
        main_module.app.dependency_overrides.pop(get_db, None)
        main_module.app.dependency_overrides.pop(get_current_user, None)
        engine.dispose()


def test_governance_api_scopes_proposals_and_calibrations_to_owner():
    from fastapi.testclient import TestClient

    from app.governance.service import (
        approve_proposal,
        submit_proposal,
        validate_parameter_set_version,
    )

    import app.candidates.models  # noqa: F401
    import app.governance.models  # noqa: F401
    import app.market_engine_models  # noqa: F401
    import app.market_models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.trigger_models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user_a = User(email="owner-a@example.com", username="owner-a", password_hash="hash")
        user_b = User(email="owner-b@example.com", username="owner-b", password_hash="hash")
        db.add_all([user_a, user_b])
        db.flush()
        bootstrap_parameter_set(db)
        active = db.execute(
            select(ParameterSetVersion).where(ParameterSetVersion.status == "ACTIVE")
        ).scalar_one()
        run = BacktestRun(
            user_id=user_a.id,
            portfolio_id=None,
            scope="MARKET",
            replay_mode="PRODUCTION_REPLAY",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            status="COMPLETED",
            data_hash="hash-a",
            calculation_key="governance-api-owner-a",
        )
        run.parameter_set_version_id = active.id
        run.parameter_set_version = "v1"
        run.parameter_set_hash = active.config_hash
        db.add(run)
        db.flush()
        report = CalibrationReport(
            backtest_run_id=run.id,
            user_id=user_a.id,
            portfolio_id=None,
            status="COMPLETED",
            target_parameter="candidate.min_decision_edge",
            current_value_json=5.0,
            challenger_value_json=6.0,
            recommendation="CONSIDER_CHANGE",
            report_json={"replay_mode": "PRODUCTION_REPLAY"},
        )
        db.add(report)
        db.flush()
        proposal = ParameterChangeProposal(
            user_id=user_a.id,
            source_type="CALIBRATION_REPORT",
            source_calibration_report_id=report.id,
            base_parameter_set_version_id=active.id,
            target_parameter_key="candidate.min_decision_edge",
            current_value_json=5.0,
            proposed_value_json=6.0,
            proposal_type="STANDARD",
            status="DRAFT",
            evidence_summary_json={"source": "calibration"},
            risk_summary_json={"human_review_required": True},
            reason="evidence",
        )
        db.add(proposal)
        db.flush()
        submit_proposal(db, proposal=proposal, user_id=user_a.id)
        approved = approve_proposal(db, proposal=proposal, reviewer_user_id=user_a.id)
        validate_parameter_set_version(db, version=approved, actor_user_id=user_a.id)
        db.commit()
        user_b_id = user_b.id
        report_id = report.id
        proposal_id = proposal.id
        approved_id = approved.id
        active_id = active.id

    def override_db():
        with Session(engine) as db:
            yield db

    main_module.app.dependency_overrides[get_db] = override_db
    main_module.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_b_id, status="active")
    client = TestClient(main_module.app)
    try:
        assert client.get("/api/v3/governance/proposals").json()["proposals"] == []
        assert client.post(
            "/api/v3/governance/proposals/from-calibration",
            json={"calibration_report_id": report_id, "proposed_value": 6.0},
        ).status_code == 404
        assert client.get(f"/api/v3/governance/proposals/{proposal_id}").status_code == 404
        assert client.post(f"/api/v3/governance/proposals/{proposal_id}/submit").status_code == 404
        assert client.post(f"/api/v3/governance/proposals/{proposal_id}/approve", json={}).status_code == 404
        assert client.post(f"/api/v3/governance/proposals/{proposal_id}/reject", json={}).status_code == 404
        assert client.post(f"/api/v3/governance/parameter-sets/{approved_id}/validate").status_code == 404
        assert client.post(
            f"/api/v3/governance/parameter-sets/{approved_id}/activate",
            json={"expected_active_version_id": active_id, "reason": "not owner"},
        ).status_code == 404

        forged_standard = client.post(
            "/api/v3/governance/proposals/manual",
            json={
                "target_parameter_key": "candidate.min_decision_edge",
                "proposed_value": 6.0,
                "reason": "forged",
                "proposal_type": "STANDARD",
                "risk_acknowledged": True,
            },
        )
        assert forged_standard.status_code == 422
        unacknowledged = client.post(
            "/api/v3/governance/proposals/manual",
            json={
                "target_parameter_key": "candidate.min_decision_edge",
                "proposed_value": 6.0,
                "reason": "unacknowledged",
                "risk_acknowledged": False,
            },
        )
        assert unacknowledged.status_code == 422
        assert "manual_proposal_requires_risk_acknowledgement" in unacknowledged.text
    finally:
        main_module.app.dependency_overrides.pop(get_db, None)
        main_module.app.dependency_overrides.pop(get_current_user, None)
        engine.dispose()
