"""Phase L history API surface tests."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app as fastapi_app
from app.v2_dependencies import get_current_user
from app.v2_models import User


def test_history_api_coverage_availability_sync_and_state() -> None:
    import app.candidates.models  # noqa: F401
    import app.governance.models  # noqa: F401
    import app.history.models  # noqa: F401
    import app.memory.models  # noqa: F401
    import app.operations.models  # noqa: F401
    import app.portfolio_models  # noqa: F401
    import app.trigger_models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    day = date(2025, 1, 2)
    with Session(engine) as db:
        user = User(email="history-api@example.com", username="history-api", password_hash="hash")
        db.add(user)
        db.commit()
        user_id = user.id

    def override_db():
        with Session(engine) as db:
            yield db

    fastapi_app.dependency_overrides[get_db] = override_db
    fastapi_app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, status="active")
    client = TestClient(fastapi_app)
    try:
        coverage = client.get(
            f"/api/v3/history/coverage?start_date={day}&end_date={day}"
        )
        assert coverage.status_code == 200
        assert [item["data_type"] for item in coverage.json()["items"]] == [
            "security_lifecycle",
            "trading_status",
            "st_classification",
            "valuation",
            "fundamentals",
            "etf_metadata",
            "price_basis",
        ]

        availability = client.get(
            f"/api/v3/history/availability?start_date={day}&end_date={day}"
        )
        assert availability.status_code == 200
        assert availability.json()["items"]["historical_security_state"]["status"] == "DATA_GAP"

        state = client.get(f"/api/v3/history/security/600001/state?as_of={day}")
        assert state.status_code == 200
        assert state.json()["status"] == "UNKNOWN"

        sync = client.post(
            "/api/v3/history/sync",
            json={
                "data_type": "security_lifecycle",
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "rows": [
                    {
                        "market": "CN",
                        "code": "600001",
                        "effective_date": day.isoformat(),
                        "event_type": "LISTED",
                        "security_type": "STOCK",
                        "exchange": "SSE",
                        "source": "operator-import",
                        "source_ref": "api-lifecycle-1",
                    }
                ],
            },
        )
        assert sync.status_code == 201
        assert sync.json()["status"] == "COMPLETED"
        assert sync.json()["inserted_count"] == 1

        state = client.get(f"/api/v3/history/security/600001/state?as_of={day}")
        assert state.json()["status"] == "ACTIVE"
        timeline = client.get("/api/v3/history/security/600001/timeline")
        assert timeline.status_code == 200
        assert timeline.json()["events"][0]["event_type"] == "LISTED"

        unsupported = client.post(
            "/api/v3/history/sync",
            json={"data_type": "valuation", "provider": "EASTMONEY"},
        )
        assert unsupported.status_code == 201
        assert unsupported.json()["status"] == "UNSUPPORTED"

        runs = client.get("/api/v3/history/sync-runs")
        assert runs.status_code == 200
        assert len(runs.json()["runs"]) == 2
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        fastapi_app.dependency_overrides.pop(get_current_user, None)


__all__: list[str] = []
