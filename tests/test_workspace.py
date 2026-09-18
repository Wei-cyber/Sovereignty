import time
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.db import now, session_scope
from backend.execution import execute_run, validate_answer
from backend.jobs import process_job
from backend.models import Document, Job, Run, Workflow, WorkflowVersion
from backend.schemas import WorkflowDefinition, template


def path(ids, suffix=""):
    return "/api/v1/workspaces/" + ids["space"] + suffix


def wait_for(client, url, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        value = response.json()
        if predicate(value):
            return value
        time.sleep(0.1)
    pytest.fail(f"Timed out waiting for {url}: {value}")


def document(
    client, ids, name="handbook.md", body=b"# Benefits\nEmployees receive 25 vacation days per calendar year."
):
    response = client.post(path(ids, "/documents"), files={"file": (name, body, "text/plain")})
    assert response.status_code == 201, response.text
    doc = response.json()
    wait_for(
        client,
        path(ids, "/documents"),
        lambda docs: any(d["id"] == doc["id"] and d["status"] in {"ready", "failed"} for d in docs),
    )
    return doc


def workflow(client, ids, kind="rag"):
    response = client.post(path(ids, "/workflows"), json={"name": "Benefits assistant", "template": kind})
    assert response.status_code == 201, response.text
    return response.json()


def suite(client, ids, doc_id):
    response = client.post(
        path(ids, "/evaluation-suites"),
        json={
            "name": "Benefits regression",
            "cases": [
                {
                    "id": "answer",
                    "question": "How many vacation days do employees receive?",
                    "reference_answer": "Employees receive 25 vacation days per calendar year.",
                    "source_document_ids": [doc_id],
                },
                {
                    "id": "abstain",
                    "question": "What is the rainfall on Jupiter?",
                    "reference_answer": "",
                    "expected_abstention": True,
                    "category": "unanswerable",
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def evaluated(client, ids, w, s):
    assert (
        client.post(
            path(ids, f"/evaluation-suites/{s['id']}/review"),
            json={"notes": "Test fixture simulating an administrator's review action."},
        ).status_code
        == 200
    )
    response = client.post(
        path(ids, "/evaluations"), json={"version_id": w["versions"][0]["id"], "suite_id": s["id"]}
    )
    assert response.status_code == 202, response.text
    report_id = response.json()["id"]
    reports = wait_for(
        client,
        path(ids, "/evaluations"),
        lambda items: any(r["id"] == report_id and r["status"] in {"completed", "failed"} for r in items),
    )
    return next(r for r in reports if r["id"] == report_id)


def publish(client, ids, w, report):
    return client.post(
        path(ids, f"/workflows/{w['id']}/publish"),
        json={"version_id": w["versions"][0]["id"], "report_id": report["id"]},
    )


def test_full_upload_evaluate_publish_chat_feedback(admin, environment):
    d = document(admin, environment)
    w = workflow(admin, environment)
    s = suite(admin, environment, d["id"])
    report = evaluated(admin, environment, w, s)
    assert report["status"] == "completed", report
    assert report["passed"], report
    assert publish(admin, environment, w, report).status_code == 200
    response = admin.post(
        path(environment, f"/workflows/{w['id']}/runs"),
        json={"question": "How many vacation days do employees receive?"},
        headers={"Idempotency-Key": "question-1"},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    run = wait_for(
        admin, path(environment, f"/runs/{run_id}"), lambda r: r["status"] in {"completed", "failed"}
    )
    assert run["status"] == "completed", run
    assert "25" in run["answer"]["text"]
    assert len(run["answer"]["citations"]) == 1
    assert admin.get(path(environment, f"/documents/{d['id']}/chunks")).status_code == 200
    feedback = admin.post(
        path(environment, f"/runs/{run_id}/feedback"), json={"rating": 1, "comment": "Useful and grounded."}
    )
    assert feedback.status_code == 201
    traces = admin.get(path(environment, f"/runs/{run_id}/traces")).json()
    assert [t["node_type"] for t in traces] == ["input", "retrieval", "model", "answer"]
    assert all(t["status"] == "completed" for t in traces)
    assert len(admin.get(path(environment, "/conversations")).json()) == 1
    duplicate = admin.post(
        path(environment, f"/workflows/{w['id']}/runs"),
        json={"question": "How many vacation days do employees receive?"},
        headers={"Idempotency-Key": "question-1"},
    )
    assert duplicate.json()["id"] == run_id
    events = admin.get(path(environment, f"/runs/{run_id}/events"))
    assert "event: done" in events.text
    event_ids = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
    replay = admin.get(
        path(environment, f"/runs/{run_id}/events"), headers={"Last-Event-ID": str(event_ids[-1])}
    )
    assert "id: " not in replay.text


def test_auth_and_csrf(admin, environment):
    assert (
        admin.post(
            path(environment, "/workflows"), json={"name": "x"}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        admin.post(
            path(environment, "/workflows"),
            json={"name": "x"},
            headers={"Origin": "https://attacker.invalid"},
        ).status_code
        == 403
    )
    assert admin.post("/api/v1/auth/logout").status_code == 200
    assert admin.get("/api/v1/workspaces").status_code == 401


def test_failed_login_rate_limit(client):
    for _ in range(10):
        assert (
            client.post(
                "/api/v1/auth/login", json={"email": "none@example.test", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "none@example.test", "password": "wrong"}
        ).status_code
        == 429
    )


def test_workspace_isolation_and_member_permissions(admin, environment):
    d = document(admin, environment)
    w = workflow(admin, environment)
    login = admin.post(
        "/api/v1/auth/login", json={"email": "outside@example.test", "password": "Test-password-123"}
    )
    admin.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert admin.get(path(environment, "/documents")).status_code == 404
    assert admin.get(path(environment, f"/documents/{d['id']}/chunks")).status_code == 404
    assert admin.get(path(environment, "/workflows")).status_code == 404
    login = admin.post(
        "/api/v1/auth/login", json={"email": "member@example.test", "password": "Test-password-123"}
    )
    admin.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert admin.get(path(environment, "/workflows")).json() == []
    assert admin.post(path(environment, "/workflows"), json={"name": "No"}).status_code == 403
    assert admin.get(path(environment, "/evaluation-suites")).status_code == 403
    assert (
        admin.post(
            path(environment, f"/workflows/{w['id']}/runs"),
            json={"question": "q", "preview": True, "version_id": w["versions"][0]["id"]},
        ).status_code
        == 403
    )


def test_publication_requires_exact_passing_reviewed_report(admin, environment):
    d = document(admin, environment)
    w = workflow(admin, environment)
    s = suite(admin, environment, d["id"])
    assert (
        admin.post(
            path(environment, "/evaluations"),
            json={"version_id": w["versions"][0]["id"], "suite_id": s["id"]},
        ).status_code
        == 409
    )
    assert publish(admin, environment, w, {"id": "missing"}).status_code == 409
    report = evaluated(admin, environment, w, s)
    assert report["passed"], report
    new_version = admin.post(
        path(environment, f"/workflows/{w['id']}/versions"), json=w["versions"][0]["definition"]
    ).json()
    assert (
        admin.post(
            path(environment, f"/workflows/{w['id']}/publish"),
            json={"version_id": new_version["id"], "report_id": report["id"]},
        ).status_code
        == 409
    )
    assert publish(admin, environment, w, report).status_code == 200
    revised = admin.post(
        path(environment, f"/evaluation-suites/{s['id']}/versions"),
        json={"name": s["name"], "cases": s["cases"], "thresholds": s["thresholds"]},
    )
    assert revised.status_code == 201
    assert publish(admin, environment, w, report).status_code == 409


def test_deletion_pauses_release_and_redacts_history(admin, environment):
    d = document(admin, environment)
    w = workflow(admin, environment)
    report = evaluated(admin, environment, w, suite(admin, environment, d["id"]))
    assert publish(admin, environment, w, report).status_code == 200
    response = admin.post(
        path(environment, f"/workflows/{w['id']}/runs"),
        json={"question": "How many vacation days do employees receive?"},
    )
    run_id = response.json()["id"]
    wait_for(admin, path(environment, f"/runs/{run_id}"), lambda r: r["status"] == "completed")
    assert admin.delete(path(environment, f"/documents/{d['id']}")).status_code == 200
    assert admin.get(path(environment, f"/documents/{d['id']}/chunks")).status_code == 410
    assert admin.get(path(environment, "/workflows")).json()[0]["paused"]
    historical = admin.get(path(environment, f"/runs/{run_id}")).json()
    assert historical["content_revoked"] and historical["answer"] is None and historical["sources"] == []
    assert all(t["outputs"] == {} for t in admin.get(path(environment, f"/runs/{run_id}/traces")).json())
    assert publish(admin, environment, w, report).status_code == 409


def test_agent_only_uses_allowed_read_tools(admin, environment):
    document(admin, environment)
    w = workflow(admin, environment, "agent")
    response = admin.post(
        path(environment, f"/workflows/{w['id']}/runs"),
        json={
            "question": "How many vacation days do employees receive?",
            "preview": True,
            "version_id": w["versions"][0]["id"],
        },
    )
    run = wait_for(
        admin,
        path(environment, f"/runs/{response.json()['id']}"),
        lambda r: r["status"] in {"completed", "failed"},
    )
    assert run["status"] == "completed", run
    traces = admin.get(path(environment, f"/runs/{run['id']}/traces")).json()
    tools = next(t for t in traces if t["node_type"] == "agent")["tool_calls"]
    assert [t["tool"] for t in tools] == ["knowledge_search", "read_source"]
    assert len(tools) <= 4


def test_invalid_citations_rejected():
    with pytest.raises(ValueError):
        validate_answer(
            {
                "text": "made up",
                "abstained": False,
                "citations": [{"chunk_id": "outside", "quote": "invented"}],
            },
            [],
        )
    assert validate_answer({"text": "made up", "abstained": False, "citations": []}, [])["abstained"]


@pytest.mark.parametrize(
    "change", ["cycle", "disconnected", "unknown", "duplicate", "no_retrieval", "fanout"]
)
def test_invalid_graphs(change):
    graph = template().model_dump()
    if change == "cycle":
        graph["edges"][2]["target"] = "retrieval"
    if change == "disconnected":
        graph["nodes"].append({"id": "unused", "type": "answer", "label": "unused"})
    if change == "unknown":
        graph["edges"][0]["target"] = "missing"
    if change == "duplicate":
        graph["nodes"][1]["id"] = "input"
    if change == "no_retrieval":
        graph["nodes"][1]["type"] = "model"
    if change == "fanout":
        graph["edges"].append({"id": "extra", "source": "input", "target": "answer"})
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(graph)


def test_unsupported_empty_and_invalid_files(admin, environment):
    assert (
        admin.post(
            path(environment, "/documents"), files={"file": ("x.exe", b"bad", "application/octet-stream")}
        ).status_code
        == 415
    )
    assert (
        admin.post(
            path(environment, "/documents"), files={"file": ("x.pdf", b"not pdf", "application/pdf")}
        ).status_code
        == 415
    )
    assert (
        admin.post(path(environment, "/documents"), files={"file": ("x.md", b"", "text/plain")}).status_code
        == 400
    )
    doc = document(admin, environment, "empty.md", b"   ")
    items = admin.get(path(environment, "/documents")).json()
    assert next(d for d in items if d["id"] == doc["id"])["status"] == "failed"


def test_cancelled_and_expired_runs(environment):
    with session_scope() as db:
        workflow = Workflow(workspace_id=environment["space"], name="Test")
        db.add(workflow)
        db.flush()
        version = WorkflowVersion(
            workflow_id=workflow.id,
            number=1,
            definition=template().model_dump(),
            corpus_revision=0,
            model_profile={"provider": "demo"},
            fingerprint="test",
            created_by=environment["admin"],
        )
        db.add(version)
        db.flush()
        runs = [
            Run(
                workspace_id=environment["space"],
                user_id=environment["admin"],
                version_id=version.id,
                question="q",
                model_profile={},
                cancel_requested=True,
            ),
            Run(
                workspace_id=environment["space"],
                user_id=environment["admin"],
                version_id=version.id,
                question="q",
                model_profile={},
                started_at=now() - timedelta(minutes=10),
                deadline_at=now() - timedelta(minutes=9),
            ),
        ]
        db.add_all(runs)
        db.flush()
        ids = [r.id for r in runs]
    for rid in ids:
        execute_run(rid)
    with session_scope() as db:
        assert [db.get(Run, rid).status for rid in ids] == ["cancelled", "timed_out"]


def test_completed_job_is_idempotent(admin, environment):
    doc = document(admin, environment)
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.target_id == doc["id"]))
        job_id = job.id
        before = db.get(Document, doc["id"]).chunk_count
    process_job(job_id)
    with session_scope() as db:
        assert db.get(Document, doc["id"]).chunk_count == before
