"""Authenticated dual-user API tests for N18, N19 and N21."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.governance.models import ParameterChangeProposal, ParameterSetVersion
from app.governance.service import bootstrap_parameter_set
from app.history.models import HistoricalDataSyncRun, SecurityLifecycleEvent
from app.main import app
from app.security import create_access_token
from app.v2_models import User


@pytest.fixture
def users():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="operator@example.com", password_hash="unused")
        member = User(email="member@example.com", password_hash="unused")
        db.add_all([owner, member])
        db.flush()
        bootstrap_parameter_set(db)
        db.commit()
        owner_headers = {"Authorization": f"Bearer {create_access_token(owner.id)[0]}"}
        member_headers = {"Authorization": f"Bearer {create_access_token(member.id)[0]}"}

    def database():
        with Session(engine) as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    try:
        yield client, owner_headers, member_headers, engine
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


PROTECTED = [
    ("POST", "/api/v3/history/sync", {"data_type": "security_lifecycle", "rows": []}),
    ("POST", "/api/v3/history/sync-runs/1/cancel", None),
    ("POST", "/api/v3/governance/proposals/from-calibration", {"calibration_report_id": 1, "proposed_value": 6}),
    ("POST", "/api/v3/governance/proposals/manual", {"target_parameter_key": "candidate.min_decision_edge", "proposed_value": 6, "reason": "test", "risk_acknowledged": True}),
    ("POST", "/api/v3/governance/proposals/1/submit", None),
    ("POST", "/api/v3/governance/proposals/1/approve", {}),
    ("POST", "/api/v3/governance/proposals/1/reject", {}),
    ("POST", "/api/v3/governance/parameter-sets/1/validate", None),
    ("POST", "/api/v3/governance/parameter-sets/1/activate", {"emergency_override": True, "reason": "test"}),
    ("POST", "/api/v3/governance/parameter-sets/1/rollback-proposal", {"reason": "test"}),
    ("GET", "/api/v3/system/readiness", None),
    ("GET", "/api/v3/system/recovery", None),
    ("GET", "/api/v3/system/backups", None),
    ("POST", "/api/v3/system/backups", {"reason": "MANUAL"}),
    ("POST", "/api/v3/system/backups/example/verify", None),
    ("POST", "/api/v3/system/backups/example/restore-drill", None),
    ("POST", "/api/v3/system/diagnostics", None),
    ("GET", "/api/v3/system/diagnostics/example/download", None),
]


@pytest.mark.parametrize("method,path,payload", PROTECTED)
def test_normal_jwt_never_authorizes_shared_operations(users, method, path, payload):
    client, _, member_headers, engine = users
    assert client.request(method, path, json=payload).status_code == 401
    response = client.request(method, path, headers=member_headers, json=payload)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "system_operator_required"
    with Session(engine) as db:
        assert db.query(HistoricalDataSyncRun).count() == 0
        assert db.query(SecurityLifecycleEvent).count() == 0
        assert db.query(ParameterChangeProposal).count() == 0
        assert db.query(ParameterSetVersion).filter_by(status="ACTIVE").count() == 1


def test_operator_can_import_shared_history_and_normal_user_can_read(users):
    client, owner_headers, member_headers, _ = users
    response = client.post("/api/v3/history/sync", headers=owner_headers, json={
        "data_type": "security_lifecycle", "rows": [{"market": "CN", "code": "600001",
            "effective_date": "2026-10-08", "event_type": "LISTED", "security_type": "STOCK",
            "exchange": "SSE", "source": "operator-import", "source_ref": "audit-test",
            "source_available_at": "2026-10-08T09:00:00+08:00"}],
    })
    assert response.status_code == 201, response.text
    assert response.json()["inserted_count"] == 1
    assert client.get("/api/v3/history/security/600001/state?as_of=2026-10-08", headers=member_headers).json()["status"] == "ACTIVE"
    assert client.get("/api/v3/governance/parameters", headers=member_headers).status_code == 200


def test_operator_can_create_manual_proposal_but_member_cannot_read_it(users):
    client, owner_headers, member_headers, _ = users
    payload = {"target_parameter_key": "candidate.min_decision_edge", "proposed_value": 6,
        "reason": "audit evidence", "risk_acknowledged": True}
    created = client.post("/api/v3/governance/proposals/manual", headers=owner_headers, json=payload)
    assert created.status_code == 200, created.text
    assert client.get(f"/api/v3/governance/proposals/{created.json()['id']}", headers=member_headers).status_code == 404
    conflict = client.post("/api/v3/governance/proposals/manual", headers=owner_headers, json={**payload, "proposed_value": 7})
    assert conflict.status_code == 409
    assert client.get("/api/v3/system/diagnostics/example/download", headers=owner_headers).status_code == 404
