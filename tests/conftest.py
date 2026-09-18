import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.config import settings
from backend.db import Base, engine, session_scope
from backend.execution import initialize_checkpoints
from backend.models import Membership, User, Workspace
from backend.security import hasher


@pytest.fixture
def environment(tmp_path, monkeypatch):
    postgres_url = os.environ.get("RELAY_TEST_DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", postgres_url or "sqlite:///" + str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-never-sent")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "384")
    monkeypatch.setenv("CHAT_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("GRADER_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("JOB_MODE", "local")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_DEMO_PUBLICATION", "false")
    from tests.runtime import install, calibration_fixture

    install(monkeypatch)
    monkeypatch.setattr(
        "backend.config.Settings.model_config",
        {**__import__("backend.config", fromlist=["Settings"]).Settings.model_config, "env_file": None},
    )
    monkeypatch.delenv("ALLOW_DEMO_PUBLICATION", raising=False)
    settings.cache_clear()
    calibration_fixture(tmp_path, settings())
    engine.cache_clear()
    if postgres_url:
        if engine().url.database != "relay_ci_test":
            raise RuntimeError("PostgreSQL tests require the dedicated relay_ci_test database")
        with engine().begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.drop_all(engine())
    Base.metadata.create_all(engine())
    initialize_checkpoints()
    with session_scope() as db:
        admin = User(
            email="admin@example.test",
            name="Admin",
            password_hash=hasher.hash("Test-password-123"),
            is_system_admin=True,
        )
        member = User(
            email="member@example.test", name="Member", password_hash=hasher.hash("Test-password-123")
        )
        outsider = User(
            email="outside@example.test", name="Outside", password_hash=hasher.hash("Test-password-123")
        )
        space = Workspace(name="Team")
        other = Workspace(name="Private")
        db.add_all([admin, member, outsider, space, other])
        db.flush()
        db.add_all(
            [
                Membership(user_id=admin.id, workspace_id=space.id, role="admin"),
                Membership(user_id=member.id, workspace_id=space.id, role="member"),
                Membership(user_id=outsider.id, workspace_id=other.id, role="admin"),
            ]
        )
        ids = {
            "admin": admin.id,
            "member": member.id,
            "outsider": outsider.id,
            "space": space.id,
            "other": other.id,
        }
    yield ids
    engine().dispose()
    monkeypatch.setattr(
        "backend.config.Settings.model_config",
        {**__import__("backend.config", fromlist=["Settings"]).Settings.model_config, "env_file": None},
    )
    monkeypatch.delenv("ALLOW_DEMO_PUBLICATION", raising=False)
    settings.cache_clear()
    calibration_fixture(tmp_path, settings())
    engine.cache_clear()


@pytest.fixture
def client(environment):
    from backend.main import app

    # The local worker exercises asynchronous delivery with a deterministic model provider.
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def admin(client):
    response = client.post(
        "/api/v1/auth/login", json={"email": "admin@example.test", "password": "Test-password-123"}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
