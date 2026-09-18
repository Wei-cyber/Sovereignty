from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.db import now, session_scope
from backend.jobs import process_job
from backend.knowledge import retrieve
from backend.models import Job, Run, StepTrace, Workflow, WorkflowVersion
from tests.doubles import DeterministicProvider
from backend.providers import ModelResult
from backend.schemas import template
from tests.test_workspace import document, path, wait_for, workflow


def preview(admin, env, w, question="How many vacation days do employees receive?"):
    response = admin.post(
        path(env, f"/workflows/{w['id']}/runs"),
        json={"question": question, "preview": True, "version_id": w["versions"][0]["id"]},
    )
    assert response.status_code == 202, response.text
    return wait_for(
        admin,
        path(env, f"/runs/{response.json()['id']}"),
        lambda r: r["status"] in {"completed", "failed", "timed_out", "cancelled"},
    )


def test_agent_rejects_model_requested_external_tool(admin, environment, monkeypatch):
    document(admin, environment)
    w = workflow(admin, environment, "agent")
    monkeypatch.setattr(
        DeterministicProvider,
        "decide",
        lambda *args: ModelResult({"tool": "send_http", "arguments": {"url": "https://invalid.test"}}),
    )
    run = preview(admin, environment, w)
    assert run["status"] == "failed"
    assert run["error"] == "Tool permission denied"


def test_agent_cannot_read_unretrieved_chunk(admin, environment, monkeypatch):
    document(admin, environment)
    w = workflow(admin, environment, "agent")
    monkeypatch.setattr(
        DeterministicProvider,
        "decide",
        lambda *args: ModelResult({"tool": "read_source", "arguments": {"chunk_id": "outside-id"}}),
    )
    assert preview(admin, environment, w)["error"] == "Tool permission denied"


def test_provider_failure_is_sanitized(admin, environment, monkeypatch):
    document(admin, environment)
    w = workflow(admin, environment)

    def broken(*args):
        raise RuntimeError("secret-provider-key-MUST-NOT-LEAK")

    monkeypatch.setattr(DeterministicProvider, "answer", broken)
    run = preview(admin, environment, w)
    assert run["status"] == "failed"
    assert "secret-provider" not in str(run)
    traces = admin.get(path(environment, f"/runs/{run['id']}/traces")).json()
    assert "secret-provider" not in str(traces)


def test_condition_chooses_one_path(admin, environment):
    graph = {
        "nodes": [
            {"id": "start", "type": "input", "label": "Question"},
            {"id": "retrieve", "type": "retrieval", "label": "Retrieve"},
            {"id": "branch", "type": "condition", "label": "Evidence?"},
            {"id": "compose", "type": "model", "label": "Compose"},
            {"id": "finish", "type": "answer", "label": "Answer"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "retrieve"},
            {"id": "e2", "source": "retrieve", "target": "branch"},
            {"id": "e3", "source": "branch", "target": "compose", "branch": "true"},
            {"id": "e4", "source": "branch", "target": "finish", "branch": "false"},
            {"id": "e5", "source": "compose", "target": "finish"},
        ],
    }
    w = workflow(admin, environment)
    version = admin.post(path(environment, f"/workflows/{w['id']}/versions"), json=graph).json()
    w["versions"] = [version]
    run = preview(admin, environment, w)
    assert run["status"] == "completed" and run["answer"]["abstained"]
    traces = admin.get(path(environment, f"/runs/{run['id']}/traces")).json()
    assert "compose" not in [t["node_id"] for t in traces]


def test_replacement_preserves_old_snapshot(admin, environment):
    old = document(admin, environment)
    w = workflow(admin, environment)
    new = admin.post(
        path(environment, "/documents"),
        data={"replaces": old["id"]},
        files={
            "file": ("handbook.md", b"Employees receive 30 vacation days per calendar year.", "text/plain")
        },
    )
    assert new.status_code == 201
    wait_for(
        admin,
        path(environment, "/documents"),
        lambda ds: any(d["id"] == new.json()["id"] and d["status"] == "ready" for d in ds),
    )
    old_run = preview(admin, environment, w)
    assert "25" in old_run["answer"]["text"]
    latest = admin.post(
        path(environment, f"/workflows/{w['id']}/versions"), json=w["versions"][0]["definition"]
    ).json()
    w["versions"] = [latest]
    assert "30" in preview(admin, environment, w)["answer"]["text"]


def test_worker_recovery_reuses_completed_steps(environment, monkeypatch):
    """Simulate death after trace commit but before LangGraph's checkpoint commit."""
    with session_scope() as db:
        w = Workflow(name="Recovery", workspace_id=environment["space"])
        db.add(w)
        db.flush()
        definition = template().model_dump()
        version = WorkflowVersion(
            workflow_id=w.id,
            number=1,
            created_by=environment["admin"],
            definition=definition,
            corpus_revision=0,
            model_profile=settings().model_profile(),
            fingerprint="recovery",
        )
        db.add(version)
        db.flush()
        run = Run(
            workspace_id=environment["space"],
            user_id=environment["admin"],
            version_id=version.id,
            question="Question",
            model_profile=version.model_profile,
            status="running",
            kind="preview",
            started_at=now(),
            deadline_at=now() + timedelta(seconds=60),
        )
        db.add(run)
        db.flush()
        run_id = run.id
        db.add(
            StepTrace(
                run_id=run_id,
                node_id="input",
                node_type="input",
                status="completed",
                inputs={"question": "Question"},
                outputs={"question": "Question"},
            )
        )
        job = Job(kind="run", target_id=run_id, status="running", lease_until=now() - timedelta(seconds=1))
        db.add(job)
        db.flush()
        job_id = job.id
    process_job(job_id)
    with session_scope() as db:
        assert db.get(Run, run_id).status == "completed"
        assert db.get(Job, job_id).status == "completed"
        assert (
            len(
                db.scalars(
                    select(StepTrace).where(StepTrace.run_id == run_id, StepTrace.node_id == "input")
                ).all()
            )
            == 1
        )
        before = db.get(Run, run_id).tokens
    process_job(job_id)
    with session_scope() as db:
        assert db.get(Run, run_id).tokens == before


def test_retrieval_rechecks_membership(environment):
    with session_scope() as db:
        with pytest.raises(Exception) as error:
            retrieve(
                db, environment["outsider"], environment["space"], 0, "private", settings().model_profile()
            )
        assert getattr(error.value, "status_code", None) == 404


def test_recovery_attempts_are_bounded(environment):
    with session_scope() as db:
        job = Job(
            kind="ingestion",
            target_id="missing-document",
            status="running",
            attempts=3,
            lease_until=now() - timedelta(seconds=1),
        )
        db.add(job)
        db.flush()
        job_id = job.id
    process_job(job_id)
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed" and job.attempts == 3


def test_rate_limit_retries_are_bounded(admin, environment, monkeypatch):
    import httpx
    from openai import RateLimitError
    from tests.doubles import DeterministicProvider

    document(admin, environment)
    w = workflow(admin, environment)
    attempts = []

    def limited(*args, **kwargs):
        attempts.append(1)
        raise RateLimitError(
            "Simulated limit",
            response=httpx.Response(429, request=httpx.Request("POST", "https://example.test")),
            body=None,
        )

    monkeypatch.setattr(DeterministicProvider, "answer", limited)
    monkeypatch.setattr("backend.execution.time.sleep", lambda _: None)
    result = preview(admin, environment, w)
    assert result["status"] == "failed" and len(attempts) == 3


def test_production_config_fails_closed(monkeypatch):
    from backend.config import Settings

    with pytest.raises(ValueError, match="HTTPS"):
        Settings(_env_file=None, app_env="production").validate_deployment()
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings(
            _env_file=None, app_env="production", cookie_secure=True, app_origin="https://relay.test"
        ).validate_deployment()
