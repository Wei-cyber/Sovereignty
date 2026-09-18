"""Isolated fixtures exercise the human-review controls, not real human approvals."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.calibration_workspace import run_calibration, workspace_profile
from backend.config import settings
from backend.db import now, session_scope
from backend.models import Chunk, Document, GraderCalibration, Job
from backend.providers import ModelResult


@pytest.fixture
def setup(environment):
    from backend.main import app

    with session_scope() as db:
        ids = []
        for workspace in [environment["space"], environment["other"]]:
            doc = Document(
                workspace_id=workspace,
                name="Policy.md",
                path="fixture.md",
                media_type="text/markdown",
                size=40,
                checksum="a" * 64,
                status="ready",
                introduced_revision=1,
            )
            db.add(doc)
            db.flush()
            chunk = Chunk(
                workspace_id=workspace,
                document_id=doc.id,
                ordinal=0,
                text="Employees receive 25 days of annual leave.",
                location="Leave policy",
                embedding=[0.0] * 64,
            )
            db.add(chunk)
            db.flush()
            ids.append(chunk.id)
    client = TestClient(app)  # No background worker: explicitly control deliveries.
    login(client, "admin")
    yield client, f"/api/v1/workspaces/{environment['space']}/grader-calibrations", ids
    client.close()


def login(client, name):
    response = client.post(
        "/api/v1/auth/login", json={"email": f"{name}@example.test", "password": "Test-password-123"}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def examples(chunk):
    return [
        dict(
            question=f"What is the leave allowance, case {i}?",
            reference_answer="Employees receive 25 days of annual leave.",
            answer_text="Employees receive 25 days of annual leave."
            if i >= 5
            else "Everyone receives unlimited holidays.",
            source_chunk_ids=[chunk],
            human_correctness=int(i >= 5),
            human_evidence_support=int(i >= 5),
        )
        for i in range(10)
    ]


REVIEW = {"confirmed": True, "notes": "Automated test fixture only, not a real human review."}


def create(client, path, cases):
    response = client.post(path, json={"name": "Policy grading", "examples": cases})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_drafts_review_validation_and_workspace_access(setup, environment):
    client, path, chunks = setup
    item = create(client, path, [{}])
    assert client.get(path).json()[0]["examples"][0]["human_correctness"] is None
    assert client.post(f"{path}/{item}/start", json=REVIEW).status_code == 400
    assert (
        client.post(path, json={"name": "Cross workspace", "examples": examples(chunks[1])}).status_code
        == 400
    )
    payload = {"name": "Reviewed fixture", "examples": examples(chunks[0])}
    assert client.put(f"{path}/{item}", json=payload).status_code == 200
    assert client.post(f"{path}/{item}/start", json={**REVIEW, "confirmed": False}).status_code == 422
    assert client.post(f"{path}/{item}/start", json={**REVIEW, "reviewed_by": "forged"}).status_code == 422
    assert client.post(f"{path}/{item}/start", json=REVIEW).json()["status"] == "queued"
    assert client.post(f"{path}/{item}/start", json=REVIEW).status_code == 200
    assert client.put(f"{path}/{item}", json=payload).status_code == 409
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "calibration")) == 1
        assert db.get(GraderCalibration, item).reviewed_by == environment["admin"]
    assert client.get(path.replace(environment["space"], environment["other"])).status_code == 404
    login(client, "member")
    assert client.get(path).status_code == 403
    assert client.post(f"{path}/{item}/activate").status_code == 403


class ExactGrader:
    calls = 0

    def grade(self, question, reference, answer, sources):
        self.calls += 1
        score = float(answer["text"] == reference)
        return ModelResult({"correctness": score, "evidence_support": score}, 12)


def test_passing_activation_is_scoped_pinned_and_revocable(setup, environment, monkeypatch):
    client, path, chunks = setup
    model = ExactGrader()
    monkeypatch.setattr("backend.calibration_workspace.provider", lambda profile: model)
    item = create(client, path, examples(chunks[0]))
    assert client.post(f"{path}/{item}/activate").status_code == 409
    client.post(f"{path}/{item}/start", json=REVIEW)
    run_calibration(item)
    run_calibration(item)
    assert model.calls == 10
    result = client.get(path).json()[0]
    assert result["eligible"] and not result["active"]
    assert result["completed_examples"] == 10
    assert client.post(f"{path}/{item}/activate").json()["active"]
    with session_scope() as db:
        approved = workspace_profile(db, environment["space"])
        assert approved != workspace_profile(db, environment["other"])
    cfg = settings()
    monkeypatch.setattr(cfg, "grader_reasoning_effort", "high")
    assert not client.get(path).json()[0]["eligible"]
    assert client.post(f"{path}/{item}/activate").status_code == 409
    with session_scope() as db:
        assert workspace_profile(db, environment["space"])["grader_calibration_sha256"] == "unavailable"
        doc = db.get(Document, db.get(Chunk, chunks[0]).document_id)
        doc.deleted_at = now()
    result = client.get(path).json()[0]
    assert result["source_unavailable"]
    assert result["examples"] == [] and result["report"] == {} and result["review_notes"] == ""


def test_failed_and_cancelled_checks_cannot_activate(setup, monkeypatch):
    client, path, chunks = setup

    class WrongGrader:
        def grade(self, *args):
            return ModelResult({"correctness": 0.5, "evidence_support": 0.5})

    monkeypatch.setattr("backend.calibration_workspace.provider", lambda profile: WrongGrader())
    item = create(client, path, examples(chunks[0]))
    client.post(f"{path}/{item}/start", json=REVIEW)
    run_calibration(item)
    assert not client.get(path).json()[0]["report"]["passed"]
    assert client.post(f"{path}/{item}/activate").status_code == 409
    cancelled = create(client, path, examples(chunks[0]))
    client.post(f"{path}/{cancelled}/start", json=REVIEW)
    assert client.post(f"{path}/{cancelled}/cancel").json()["status"] == "cancelled"
    run_calibration(cancelled)
    assert client.post(f"{path}/{cancelled}/activate").status_code == 409


def test_worker_resume_preserves_completed_scores_and_sanitizes_errors(setup, monkeypatch):
    client, path, chunks = setup
    model = ExactGrader()
    monkeypatch.setattr("backend.calibration_workspace.provider", lambda profile: model)
    item = create(client, path, examples(chunks[0]))
    client.post(f"{path}/{item}/start", json=REVIEW)
    # Simulate persisted progress left by a terminated worker.
    with session_scope() as db:
        db.get(GraderCalibration, item).results = [
            dict(
                index=i,
                human_correctness=0,
                human_evidence_support=0,
                graded_correctness=0,
                graded_evidence_support=0,
                correctness_error=0,
                evidence_support_error=0,
                tokens=12,
            )
            for i in range(3)
        ]
    run_calibration(item)
    assert model.calls == 7
    broken = create(client, path, examples(chunks[0]))
    client.post(f"{path}/{broken}/start", json=REVIEW)

    def failure(profile):
        raise RuntimeError("private-provider-credential")

    monkeypatch.setattr("backend.calibration_workspace.provider", failure)
    with pytest.raises(RuntimeError):
        run_calibration(broken)
    response = client.get(path)
    assert "private-provider-credential" not in response.text
    assert response.json()[0]["status"] == "failed"
