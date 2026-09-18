"""Workspace-scoped, persisted human calibration. Never auto-approve a grader."""

import json
import time
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.calibration import calibrate, validate_dataset
from backend.config import settings
from backend.db import get_db, now, session_scope
from backend.knowledge import source_dict
from backend.models import Chunk, Document, GraderCalibration, User, Workspace
from backend.providers import MODEL_DEADLINE, provider
from backend.schemas import StrictModel, fingerprint
from backend.security import audit, current_user, require_member


class HumanExample(StrictModel):
    question: str = Field(default="", max_length=8000)
    reference_answer: str = Field(default="", max_length=8000)
    answer_text: str = Field(default="", max_length=8000)
    source_chunk_ids: list[str] = Field(default_factory=list, max_length=5)
    human_correctness: float | None = Field(default=None, ge=0, le=1)
    human_evidence_support: float | None = Field(default=None, ge=0, le=1)


class CalibrationDraft(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    examples: list[HumanExample] = Field(default_factory=list, max_length=50)


class CalibrationReview(StrictModel):
    confirmed: Literal[True]
    notes: str = Field(min_length=10, max_length=4000)


class CalibrationView(CalibrationDraft):
    id: str
    status: str
    active: bool
    eligible: bool
    completed_examples: int
    report: dict
    error: str | None
    review_notes: str
    source_unavailable: bool


def sources(db, workspace_id, ids):
    found = []
    for chunk_id in dict.fromkeys(ids):
        row = db.execute(
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(
                Chunk.id == chunk_id,
                Chunk.workspace_id == workspace_id,
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
                Document.status == "ready",
            )
        ).first()
        if not row:
            raise ValueError("A supporting passage is unavailable. Choose a document from this workspace.")
        found.append(source_dict(*row))
    return found


def sources_available(db, item):
    try:
        sources(db, item.workspace_id, [cid for e in item.examples for cid in e["source_chunk_ids"]])
        return True
    except ValueError:
        return False


def eligible(db, item):
    return bool(
        item.status == "completed"
        and item.report.get("passed")
        and item.report.get("grader_identity") == settings().grader_identity()
        and item.reviewed_by
        and sources_available(db, item)
    )


def workspace_profile(db, workspace_id):
    profile = settings().model_profile()
    from backend.agent_tools import TOOL_REVISION

    profile["tool_revision"] = TOOL_REVISION
    from backend.models import Workspace

    profile["tool_policy_sha256"] = fingerprint(sorted(db.get(Workspace, workspace_id).tool_policy))
    active = db.scalar(
        select(GraderCalibration).where(
            GraderCalibration.workspace_id == workspace_id, GraderCalibration.active.is_(True)
        )
    )
    if active:
        # A stale in-app approval must not silently fall back to an older file approval.
        profile["grader_calibration_sha256"] = (
            fingerprint(active.report) if eligible(db, active) else "unavailable"
        )
    return profile


def view(db, item):
    available = sources_available(db, item)
    return dict(
        id=item.id,
        name=item.name,
        status=item.status,
        active=item.active,
        eligible=eligible(db, item),
        examples=item.examples if available else [],
        completed_examples=len(item.results),
        report=item.report if available else {},
        review_notes=item.review_notes if available else "",
        error=item.error,
        source_unavailable=not available,
    )


router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/grader-calibrations", tags=["Grader calibration"]
)


def owned(db, workspace_id, item_id, user):
    require_member(db, user.id, workspace_id, True)
    item = db.scalar(
        select(GraderCalibration)
        .where(GraderCalibration.id == item_id, GraderCalibration.workspace_id == workspace_id)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Grader check not found")
    return item


@router.get("", response_model=list[CalibrationView])
def listing(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id, True)
    return [
        view(db, item)
        for item in db.scalars(
            select(GraderCalibration)
            .where(GraderCalibration.workspace_id == workspace_id)
            .order_by(GraderCalibration.created_at.desc())
        )
    ]


def check_sources(db, workspace_id, body):
    try:
        sources(db, workspace_id, [cid for e in body.examples for cid in e.source_chunk_ids])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("", response_model=CalibrationView, status_code=201)
def create(
    workspace_id: str,
    body: CalibrationDraft,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_member(db, user.id, workspace_id, True)
    check_sources(db, workspace_id, body)
    item = GraderCalibration(workspace_id=workspace_id, user_id=user.id, **body.model_dump())
    db.add(item)
    db.flush()
    audit(db, user.id, "grader_calibration.created", item.id, workspace_id)
    return view(db, item)


@router.put("/{item_id}", response_model=CalibrationView)
def edit(
    workspace_id: str,
    item_id: str,
    body: CalibrationDraft,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = owned(db, workspace_id, item_id, user)
    if item.status != "draft":
        raise HTTPException(409, "Completed or running checks are immutable. Create a revised check.")
    check_sources(db, workspace_id, body)
    item.name, item.examples = body.name, [e.model_dump() for e in body.examples]
    audit(db, user.id, "grader_calibration.saved", item.id, workspace_id)
    return view(db, item)


@router.post("/{item_id}/start", response_model=CalibrationView)
def start(
    workspace_id: str,
    item_id: str,
    body: CalibrationReview,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from backend.jobs import enqueue

    item = owned(db, workspace_id, item_id, user)
    if item.status != "draft":
        return view(db, item)  # Repeated browser submissions never enqueue a second job.
    try:
        examples = []
        for e in item.examples:
            if not all(e[k].strip() for k in ("question", "reference_answer", "answer_text")):
                raise ValueError("Each example needs a question, verified answer, and sample answer.")
            examples.append(
                dict(
                    question=e["question"],
                    reference_answer=e["reference_answer"],
                    answer=dict(text=e["answer_text"], citations=[], abstained=False),
                    sources=sources(db, workspace_id, e["source_chunk_ids"]),
                    human_correctness=e["human_correctness"],
                    human_evidence_support=e["human_evidence_support"],
                )
            )
        dataset = dict(reviewed_by=user.id, reviewed_at=now().isoformat() + "Z", examples=examples)
        validate_dataset(json.dumps(dataset).encode())
    except ValidationError:
        raise HTTPException(
            400,
            "Add at least 10 complete examples, choose supporting passages, and rate both scores for every answer.",
        ) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    item.dataset = dataset
    item.model_profile = settings().model_profile()
    item.model_profile["calibration_identity"] = settings().grader_identity()
    item.reviewed_by, item.reviewed_at, item.review_notes = user.id, now(), body.notes
    item.status = "queued"
    enqueue(db, "calibration", item.id)
    audit(db, user.id, "grader_calibration.started", item.id, workspace_id, examples=len(examples))
    return view(db, item)


@router.post("/{item_id}/activate", response_model=CalibrationView)
def activate(
    workspace_id: str, item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_member(db, user.id, workspace_id, True)
    db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
    item = owned(db, workspace_id, item_id, user)
    if not eligible(db, item):
        raise HTTPException(
            409, "Only a passing check for the current grader and available sources can be used."
        )
    db.execute(
        update(GraderCalibration).where(GraderCalibration.workspace_id == workspace_id).values(active=False)
    )
    item.active = True
    audit(db, user.id, "grader_calibration.activated", item.id, workspace_id)
    return view(db, item)


@router.post("/{item_id}/cancel", response_model=CalibrationView)
def cancel(
    workspace_id: str, item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    item = owned(db, workspace_id, item_id, user)
    if item.status in {"queued", "running"}:
        item.status, item.finished_at = "cancelled", now()
        audit(db, user.id, "grader_calibration.cancelled", item.id, workspace_id)
    return view(db, item)


def run_calibration(item_id):
    with session_scope() as db:
        item = db.get(GraderCalibration, item_id)
        if not item or item.status in {"completed", "failed", "cancelled"}:
            return
        item.status = "running"
        dataset, profile, previous = item.dataset, item.model_profile, item.results
        workspace_id, actor_id = item.workspace_id, item.reviewed_by
    deadline = now() + timedelta(minutes=15)

    def ensure_access():
        with session_scope() as db:
            require_member(db, actor_id, workspace_id, True)
            current = db.get(GraderCalibration, item_id)
            if current.status == "cancelled":
                raise ValueError("Check cancelled")
            if not sources_available(db, current):
                raise ValueError("Supporting evidence was revoked")
        if now() >= deadline:
            raise TimeoutError("Grader check deadline elapsed")

    def progress(results):
        ensure_access()
        with session_scope() as db:
            db.get(GraderCalibration, item_id).results = list(results)

    class GuardedGrader:
        def grade(self, *args):
            from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

            for attempt in range(3):
                ensure_access()
                token = MODEL_DEADLINE.set(deadline)
                try:
                    return provider(profile).grade(*args)
                except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError):
                    if attempt == 2:
                        raise
                    time.sleep(0.5 * 2**attempt)
                finally:
                    MODEL_DEADLINE.reset(token)

    try:
        ensure_access()
        result = calibrate(
            json.dumps(dataset).encode(), GuardedGrader(), progress, previous, profile["calibration_identity"]
        )
        ensure_access()
        with session_scope() as db:
            item = db.get(GraderCalibration, item_id)
            if item.status == "cancelled":
                return
            item.report, item.status, item.finished_at = result, "completed", now()
            audit(
                db, actor_id, "grader_calibration.completed", item.id, workspace_id, passed=result["passed"]
            )
    except Exception:
        with session_scope() as db:
            item = db.get(GraderCalibration, item_id)
            if item.status == "cancelled":
                return
            item.status, item.finished_at = "failed", now()
            item.error = (
                "Check could not finish. Verify model access and source availability, then revise and retry."
            )
            audit(db, actor_id, "grader_calibration.failed", item.id, workspace_id)
        raise
