import asyncio
import base64
import json
import time
from urllib.parse import urlparse, parse_qs

import httpx2
import pytest
from cryptography.fernet import Fernet
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from backend.config import settings
from backend.db import session_scope
from backend.models import GoogleConnection, GmailDraft, WorkflowVersion
from backend import google_services as google
from backend.mcp_bridge import issue, verify
from backend.mcp_servers import create_app
from backend.schemas import fingerprint
from tests.test_workspace import path, document, wait_for


def login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Test-password-123"})
    assert r.status_code == 200
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]


def test_personal_agents_private_run_versions_and_submission(admin, environment):
    doc = document(admin, environment)
    login(admin, "member@example.test")
    body = {
        "name": "My agent",
        "instructions": "Use evidence only",
        "tools": ["knowledge_search", "read_source"],
        "knowledge_document_ids": [doc["id"]],
    }
    r = admin.post(path(environment, "/agents"), json=body)
    assert r.status_code == 201, r.text
    agent = r.json()
    vid = agent["versions"][0]["id"]
    assert agent["owner_id"] == environment["member"]
    run = admin.post(
        path(environment, f"/workflows/{agent['id']}/runs"),
        json={"question": "How many vacation days do employees receive?"},
    ).json()
    result = wait_for(
        admin, path(environment, f"/runs/{run['id']}"), lambda r: r["status"] not in {"queued", "running"}
    )
    assert result["status"] == "completed", result
    assert result["answer"]["citations"]
    assert admin.get(path(environment, f"/runs/{run['id']}/traces")).status_code == 200
    candidate = admin.post(path(environment, f"/agents/{agent['id']}/submit")).json()
    assert candidate["owner_id"] is None and candidate["published_version_id"] is None
    assert admin.post(path(environment, f"/agents/{agent['id']}/submit")).json()["id"] == candidate["id"]
    revised = admin.post(
        path(environment, f"/agents/{agent['id']}/versions"),
        json={**body, "instructions": "New instructions"},
    ).json()
    assert len(revised["versions"]) == 2
    with session_scope() as db:
        assert db.get(WorkflowVersion, vid).definition == agent["versions"][0]["definition"]
    login(admin, "admin@example.test")
    assert admin.get(path(environment, f"/runs/{run['id']}")).status_code == 404
    assert admin.get(path(environment, f"/runs/{run['id']}/traces")).status_code == 404
    assert agent["id"] not in str(admin.get(path(environment, "/agents")).json())
    assert (
        admin.post(
            path(environment, f"/workflows/{candidate['id']}/versions"),
            json=candidate["versions"][0]["definition"],
        ).status_code
        == 409
    )


@pytest.fixture
def google_config(environment, monkeypatch):
    for key, value in {
        "connection_encryption_key": Fernet.generate_key().decode(),
        "mcp_signing_key": "x" * 48,
        "google_client_id": "test-client",
        "google_client_secret": "test-secret",
    }.items():
        monkeypatch.setattr(settings(), key, value)
    with session_scope() as db:
        for provider in ["gmail", "drive"]:
            db.add(
                GoogleConnection(
                    user_id=environment["admin"],
                    workspace_id=environment["space"],
                    provider=provider,
                    email="test@example.test",
                    subject="test",
                    tokens=google.encrypt(
                        {"access_token": "secret-test-token", "expires_at": time.time() + 3600}
                    ),
                    scopes=[google.READ_SCOPE, google.COMPOSE_SCOPE]
                    if provider == "gmail"
                    else [google.DRIVE_SCOPE],
                    selected_files=[{"id": "selected", "name": "Policy"}],
                )
            )


def test_connections_oauth_state_and_revocation(admin, environment, google_config, monkeypatch):
    response = admin.post(path(environment, "/connections/authorize"), json={"provider": "gmail"})
    params = parse_qs(urlparse(response.json()["url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert google.COMPOSE_SCOPE not in params["scope"][0]
    state = params["state"][0]
    login(admin, "member@example.test")
    assert (
        admin.get("/api/v1/connections/google/callback", params={"state": state, "code": "bad"}).status_code
        == 400
    )
    login(admin, "admin@example.test")
    assert "secret-test-token" not in admin.get(path(environment, "/connections")).text
    with session_scope() as db:
        conn = db.scalar(select(GoogleConnection).where(GoogleConnection.provider == "drive"))
        source = {
            "kind": "drive",
            "connection_id": conn.id,
            "connection_generation": 1,
            "remote_id": "selected",
        }
        assert google.source_available(db, environment["admin"], environment["space"], source, remote=False)
        assert not google.source_available(
            db, environment["member"], environment["space"], source, remote=False
        )
    assert admin.delete(path(environment, "/connections/drive")).status_code == 200
    with session_scope() as db:
        assert not google.source_available(
            db, environment["admin"], environment["space"], source, remote=False
        )


@pytest.mark.parametrize("service,tool", [("gmail", "gmail_search"), ("drive", "drive_search")])
def test_mcp_protocol_auth_discovery_call_and_capability(
    environment, google_config, monkeypatch, service, tool
):
    monkeypatch.setattr(google, tool, lambda user, space, query: [{"query": query, "user": user}])

    async def scenario():
        app = create_app(service)
        async with app.app.router.lifespan_context(app.app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={
                    "Authorization": "Bearer "
                    + issue(environment["admin"], environment["space"], tool, service)
                },
            ) as client:
                assert (
                    await client.post(
                        "http://localhost/mcp", headers={"Authorization": "Bearer bad"}, json={}
                    )
                ).status_code == 401
                async with streamable_http_client("http://localhost/mcp", http_client=client) as (
                    read,
                    write,
                ):
                    async with ClientSession(read, write, read_timeout_seconds=5) as session:
                        await session.initialize()
                        names = {t.name for t in (await session.list_tools()).tools}
                        assert names == (
                            {"gmail_search", "gmail_read", "gmail_save_draft"}
                            if service == "gmail"
                            else {"drive_search", "drive_read"}
                        )
                        assert "gmail_send" not in names
                        result = await session.call_tool(tool, {"query": "policy"})
                        assert not result.is_error, result
                        value = result.structured_content or json.loads(result.content[0].text)
                        assert value["sources"][0]["user"] == environment["admin"]
                        denied = await session.call_tool(service + "_read", {"remote_id": "secret"})
                        assert denied.is_error
                        invalid = await session.call_tool(tool, {"wrong": "value"})
                        assert invalid.is_error

    asyncio.run(scenario())


def test_draft_explicit_authorization_and_uncertain_reconciliation(environment, google_config, monkeypatch):
    body = {"to": ["test@example.test"], "subject": "Subject", "body": "Draft text"}
    with pytest.raises(PermissionError):
        google.save_gmail_draft(environment["admin"], environment["space"], "not-authorized", body)
    with session_scope() as db:
        conn = db.scalar(select(GoogleConnection).where(GoogleConnection.provider == "gmail"))
        db.add(
            GmailDraft(
                user_id=environment["admin"],
                workspace_id=environment["space"],
                connection_id=conn.id,
                operation_id="test-operation",
                body_hash=fingerprint(body, conn.subject, conn.generation),
            )
        )
    writes = []

    def uncertain(*args, **kwargs):
        if kwargs.get("body"):
            writes.append(args[3])
            raise TimeoutError("unknown upstream result")
        return {"drafts": [{"id": "reconciled"}]}

    monkeypatch.setattr(google, "google_json", uncertain)
    with pytest.raises(TimeoutError):
        google.save_gmail_draft(environment["admin"], environment["space"], "test-operation", body)
    assert (
        google.save_gmail_draft(environment["admin"], environment["space"], "test-operation", body)["status"]
        == "completed"
    )
    assert (
        google.save_gmail_draft(environment["admin"], environment["space"], "test-operation", body)["status"]
        == "completed"
    )
    assert writes == ["drafts"]


def test_public_network_guard_and_private_query_exclusion(monkeypatch):
    from backend.agent_tools import public_target, external_call
    from types import SimpleNamespace

    for address in ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1"]:
        monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", (address, 80))])
        with pytest.raises(PermissionError):
            public_target("http://unsafe.test")
    captured = []
    monkeypatch.setattr("backend.agent_tools.web_search", lambda q: captured.append(q) or [])
    external_call(
        SimpleNamespace(question="public question"),
        "web_search",
        {"query": "private email secret"},
        [{"kind": "gmail", "text": "secret"}],
    )
    assert captured == ["public question"]


def test_capability_tamper_and_audience(environment, google_config):
    token = issue("user", "workspace", "gmail_read", "gmail")
    with pytest.raises(PermissionError):
        verify(token, "drive")
    payload = base64.urlsafe_b64encode(json.dumps({"user": "attacker"}).encode()).decode()
    with pytest.raises(PermissionError):
        verify(payload + "." + token.split(".")[1], "gmail")


def test_reviewed_fixture_evaluation_binds_agent_and_tool_behavior(admin, environment, monkeypatch):
    from tests.doubles import DeterministicProvider
    from backend.providers import ModelResult
    from tests.test_workspace import evaluated, publish
    from pathlib import Path

    example = json.loads(Path("docs/external-tool-suite.example.json").read_text())

    def decide(self, question, sources, history, remaining, *args):
        query = "test project flag" if "flag" in question else "private project launch date"
        return ModelResult(
            {
                "tool": "finish" if history else "gmail_search",
                "arguments": {} if history else {"query": query},
            }
        )

    monkeypatch.setattr(DeterministicProvider, "decide", decide)
    monkeypatch.setattr(
        "backend.agent_tools.external_call", lambda *a: pytest.fail("Fixture evaluation made a live call")
    )
    agent = admin.post(
        path(environment, "/agents"),
        json={"name": "Fixture agent", "instructions": "Search once then answer", "tools": ["gmail_search"]},
    ).json()
    candidate = admin.post(path(environment, f"/agents/{agent['id']}/submit")).json()
    suite = admin.post(path(environment, "/evaluation-suites"), json=example)
    assert suite.status_code == 201, suite.text
    report = evaluated(admin, environment, candidate, suite.json())
    assert report["passed"], report
    assert all(r["evidence_mode"] == "reviewed_fixture" for r in report["results"])
    assert publish(admin, environment, candidate, report).status_code == 200
    assert (
        admin.put(path(environment, "/tool-policy"), json={"tools": ["knowledge_search"]}).status_code == 200
    )
    assert publish(admin, environment, candidate, report).status_code == 409


def test_draft_retry_cannot_switch_google_accounts(environment, google_config, monkeypatch):
    body = {"to": [], "subject": "Test", "body": "Test draft"}
    with session_scope() as db:
        conn = db.scalar(select(GoogleConnection).where(GoogleConnection.provider == "gmail"))
        db.add(
            GmailDraft(
                user_id=environment["admin"],
                workspace_id=environment["space"],
                connection_id=conn.id,
                operation_id="old-account",
                body_hash=fingerprint(body, conn.subject, conn.generation),
            )
        )
        conn.generation += 1
        conn.subject = "different-account"
    monkeypatch.setattr(
        google, "google_json", lambda *a, **kw: pytest.fail("Old authorization reached Google")
    )
    with pytest.raises(PermissionError):
        google.save_gmail_draft(environment["admin"], environment["space"], "old-account", body)


def test_oauth_success_encrypts_tokens_and_rejects_callback_replay(
    admin, environment, google_config, monkeypatch
):
    from types import SimpleNamespace
    from backend import connections

    exchanges = []

    def response(data):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

    def exchange(url, *, data, timeout):
        exchanges.append(data)
        assert data["code_verifier"] and data["grant_type"] == "authorization_code"
        return response(
            {
                "access_token": "new-private-access",
                "refresh_token": "new-private-refresh",
                "expires_in": 3600,
                "scope": google.READ_SCOPE,
            }
        )

    monkeypatch.setattr(connections.httpx, "post", exchange)
    monkeypatch.setattr(
        connections.httpx,
        "get",
        lambda *a, **kw: response({"sub": "new-account", "email": "new@example.test"}),
    )
    authorized = admin.post(path(environment, "/connections/authorize"), json={"provider": "gmail"})
    state = parse_qs(urlparse(authorized.json()["url"]).query)["state"][0]
    callback = "/api/v1/connections/google/callback"
    assert (
        admin.get(callback, params={"state": state, "code": "test-code"}, follow_redirects=False).status_code
        == 303
    )
    assert (
        admin.get(callback, params={"state": state, "code": "test-code"}, follow_redirects=False).status_code
        == 400
    )
    assert len(exchanges) == 1
    with session_scope() as db:
        conn = db.scalar(select(GoogleConnection).where(GoogleConnection.provider == "gmail"))
        assert conn.subject == "new-account" and conn.generation == 2
        assert "new-private" not in conn.tokens
        assert google.decrypt(conn.tokens)["refresh_token"] == "new-private-refresh"
    assert "new-private" not in admin.get(path(environment, "/connections")).text


def test_drive_selection_rejects_unsupported_files_and_revokes_old_evidence(
    admin, environment, google_config, monkeypatch
):
    monkeypatch.setattr(
        "backend.connections.drive_metadata",
        lambda *args: {
            "id": args[-1],
            "name": "Policy",
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    endpoint = path(environment, "/connections/drive/files")
    assert admin.put(endpoint, json={"file_ids": ["folder"]}).status_code == 400
    with session_scope() as db:
        conn = db.scalar(select(GoogleConnection).where(GoogleConnection.provider == "drive"))
        source = {
            "kind": "drive",
            "connection_id": conn.id,
            "connection_generation": conn.generation,
            "remote_id": "selected",
        }
        assert google.source_available(db, environment["admin"], environment["space"], source, remote=False)
    assert admin.put(endpoint, json={"file_ids": []}).status_code == 200
    with session_scope() as db:
        assert not google.source_available(
            db, environment["admin"], environment["space"], source, remote=False
        )
