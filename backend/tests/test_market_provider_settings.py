"""Credential lifecycle and runtime use, with an isolated DB and fake transport."""
import pytest
import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.market.providers.factory import QuoteProviderFactory
from app.market.providers.fuyao_client import client_from_settings
from app.security import create_access_token, decrypt_secret
from app.services import market_provider_settings
from app.v2_models import MarketProviderSetting, User


@pytest.fixture
def config_api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[User.__table__, MarketProviderSetting.__table__])
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([
            User(id=1, email="owner@example.com", password_hash="unused"),
            User(id=2, email="member@example.com", password_hash="unused"),
        ])
        db.commit()

    def override_db():
        with sessions() as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(market_provider_settings, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "FUYAO_API_KEY", "")
    monkeypatch.setattr(settings, "ACCEPTANCE_MODE", False)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(1)[0]}"}
    member = {"Authorization": f"Bearer {create_access_token(2)[0]}"}
    yield client, headers, member, sessions
    client.close()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def test_save_encrypts_and_survives_new_sessions_without_returning_key(config_api):
    client, headers, _, sessions = config_api
    assert client.get("/api/v3/fuyao/config", headers=headers).json()["source"] == "none"
    secret = "test-fixture-fuyao-key"
    result = client.put("/api/v3/fuyao/config", headers=headers, json={"api_key": secret})
    assert result.status_code == 200
    assert result.json()["configured"] is True
    assert result.json()["source"] == "system"
    with sessions() as db:
        ciphertext = db.get(MarketProviderSetting, "fuyao").encrypted_api_key
        assert ciphertext != secret
        assert decrypt_secret(ciphertext) == secret
    for path in ("/api/v3/fuyao/config", "/api/v3/fuyao/status"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert secret not in response.text
        assert ciphertext not in response.text
    assert client_from_settings().api_key == secret


def test_saved_key_overrides_environment_and_rotation_reaches_quote_requests(config_api, monkeypatch):
    client, headers, _, _ = config_api
    monkeypatch.setattr(settings, "FUYAO_API_KEY", "test-environment-key")
    assert client.get("/api/v3/fuyao/config", headers=headers).json()["source"] == "environment"
    sent_keys = []

    def transport(_url, **kwargs):
        sent_keys.append(kwargs["headers"]["X-api-key"])
        return {"code": 0, "message": "ok", "data": {"item": []}}

    for secret in ("test-first-key", "test-replacement-key"):
        assert client.put("/api/v3/fuyao/config", headers=headers, json={"api_key": secret}).status_code == 200
        provider = QuoteProviderFactory().create("fuyao", transport=transport)
        provider.client.get("/api/a-share/prices/snapshot")
        client_from_settings(transport=transport).get("/api/a-share/prices/snapshot")
    assert sent_keys == ["test-first-key", "test-first-key", "test-replacement-key", "test-replacement-key"]
    result = client.delete("/api/v3/fuyao/config", headers=headers)
    assert result.status_code == 200
    assert result.json()["source"] == "environment"
    assert client_from_settings().api_key == "test-environment-key"


def test_config_writes_require_instance_owner_and_authentication(config_api):
    client, headers, member, _ = config_api
    for method in ("get", "put", "delete"):
        kwargs = {"json": {"api_key": "test-other-key"}} if method == "put" else {}
        assert getattr(client, method)("/api/v3/fuyao/config", **kwargs).status_code == 401
    assert client.get("/api/v3/fuyao/config", headers=member).json()["can_manage"] is False
    assert client.put("/api/v3/fuyao/config", headers=headers, json={"api_key": "test-owner-key"}).status_code == 200
    assert client.put("/api/v3/fuyao/config", headers=member, json={"api_key": "test-other-key"}).status_code == 403
    assert client.delete("/api/v3/fuyao/config", headers=member).status_code == 403
    assert client_from_settings().api_key == "test-owner-key"


@pytest.mark.parametrize("value", ["", "  ", "test secret", "test\nsecret", "x" * 4097])
def test_invalid_keys_do_not_replace_saved_key_or_appear_in_validation_errors(config_api, value):
    client, headers, _, _ = config_api
    assert client.put("/api/v3/fuyao/config", headers=headers, json={"api_key": "test-valid-key"}).status_code == 200
    response = client.put("/api/v3/fuyao/config", headers=headers, json={"api_key": value})
    assert response.status_code == 422
    assert response.json() == {"detail": "请输入有效的 API Key（不含空白，长度不超过 4096 个字符）。"}
    assert client_from_settings().api_key == "test-valid-key"


def test_clear_without_environment_disables_provider_and_bad_ciphertext_fails_closed(config_api, monkeypatch):
    client, headers, _, _ = config_api
    assert client.put("/api/v3/fuyao/config", headers=headers, json={"api_key": "test-key"}).status_code == 200
    with monkeypatch.context() as changed:
        changed.setattr(settings, "APP_SECRET_KEY", "another-test-encryption-key")
        with pytest.raises(ValueError, match="cannot be decrypted"):
            client_from_settings()
    assert client.delete("/api/v3/fuyao/config", headers=headers).json()["configured"] is False
    assert client_from_settings().configured is False


@pytest.mark.parametrize("payload", ["test-secret", ["test-secret"], {"api_key": ["test-secret"]}, {"secret": "test-secret"}])
def test_malformed_payload_does_not_echo_credentials(config_api, payload):
    client, headers, _, _ = config_api
    response = client.put("/api/v3/fuyao/config", headers=headers, json=payload)
    assert response.status_code == 422
    assert "test-secret" not in response.text
    assert client_from_settings().configured is False


def test_migration_preserves_settings_created_by_local_bootstrap(config_api):
    client, headers, _, sessions = config_api
    assert client.put("/api/v3/fuyao/config", headers=headers, json={"api_key": "test-local-key"}).status_code == 200
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20261008_0024_market_provider_settings.py"
    spec = importlib.util.spec_from_file_location("market_settings_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sessions() as db:
        with Operations.context(MigrationContext.configure(db.connection())):
            migration.upgrade()
        db.commit()
        assert decrypt_secret(db.get(MarketProviderSetting, "fuyao").encrypted_api_key) == "test-local-key"
