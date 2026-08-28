"""Phase J governance REST flow: proposals never auto-apply and activation is explicit."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.governance.models import ParameterSetVersion
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
