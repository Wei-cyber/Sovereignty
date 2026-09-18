import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import settings
from backend.calibration_workspace import router as calibration_router, workspace_profile
from backend.db import Base, engine, get_db, now, session_scope
from backend.evaluations import publication_problem, report_fingerprint
from backend.execution import TERMINAL, initialize_checkpoints
from backend.jobs import enqueue, start_local_worker
from backend.knowledge import revoked_snapshot
from backend.models import (
    AuditEvent,
    AuthSession,
    Chunk,
    Conversation,
    Document,
    EvaluationReport,
    EvaluationSuite,
    Feedback,
    Job,
    LoginAttempt,
    Membership,
    Run,
    RunEvent,
    StepTrace,
    User,
    Workflow,
    WorkflowVersion,
    Workspace,
    uid,
)
from backend.schemas import (
    AccountInput,
    EvaluationCase,
    EvaluationInput,
    EvaluationSuiteInput,
    FeedbackInput,
    FeedbackPromotion,
    LoginInput,
    MemberInput,
    PublishInput,
    ReviewInput,
    RunInput,
    WorkflowDefinition,
    WorkflowInput,
    WorkspaceInput,
    fingerprint,
    template,
    RunContract,
    StepTraceContract,
    EvaluationReportContract,
    EvaluationSuiteContract,
    WorkflowContract,
    WorkflowVersionContract,
)
from backend.security import (
    DUMMY_HASH,
    audit,
    current_user,
    hash_token,
    hasher,
    new_session,
    require_member,
    require_system_admin,
    verify_password,
)


@asynccontextmanager
async def lifespan(app):
    cfg = settings()
    cfg.validate_deployment()
    if cfg.app_env != "production" and cfg.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine())
        initialize_checkpoints()
    worker = start_local_worker() if cfg.job_mode == "local" else None
    yield
    if worker:
        worker[0].set()
        await asyncio.to_thread(worker[1].join, 10)


app = FastAPI(title="Enterprise AI Workspace", version="0.1.0", lifespan=lifespan)
app.include_router(calibration_router)
from backend.agents import router as agents_router  # noqa: E402
from backend.connections import router as connections_router  # noqa: E402

app.include_router(agents_router)
app.include_router(connections_router)


@app.exception_handler(PermissionError)
async def unavailable_connection(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=403, content={"detail": str(exc)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().app_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Last-Event-ID"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != settings().app_origin:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=403, content={"detail": "Request origin is not allowed"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    if settings().cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(IntegrityError)
async def conflict_handler(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=409,
        content={"detail": "This record already exists or was changed concurrently. Refresh and retry."},
    )


def record(obj, exclude=()):
    return {
        column.name: getattr(obj, column.name)
        for column in obj.__table__.columns
        if column.name not in exclude
    }


def account(user):
    return record(user, {"password_hash"})


def workspace_object(db, user, workspace_id, model, object_id, admin=False):
    require_member(db, user.id, workspace_id, admin=admin)
    obj = db.get(model, object_id)
    if not obj or obj.workspace_id != workspace_id:
        raise HTTPException(404, "Record not found")
    if isinstance(obj, Workflow) and (obj.archived or (obj.owner_id and obj.owner_id != user.id)):
        raise HTTPException(404, "Workflow not found")
    return obj


def version_for(db, workflow, version_id):
    version = db.get(WorkflowVersion, version_id)
    if not version or version.workflow_id != workflow.id:
        raise HTTPException(404, "Workflow version not found")
    return version


def make_version(db, workflow, user_id, definition):
    # Lock the parent to serialize version numbering in PostgreSQL.
    db.execute(select(Workflow.id).where(Workflow.id == workflow.id).with_for_update())
    number = (
        db.scalar(select(func.max(WorkflowVersion.number)).where(WorkflowVersion.workflow_id == workflow.id))
        or 0
    ) + 1
    workspace = db.get(Workspace, workflow.workspace_id)
    profile = workspace_profile(db, workflow.workspace_id)
    from backend.agent_tools import TOOL_REVISION

    profile["tool_revision"] = TOOL_REVISION
    version = WorkflowVersion(
        workflow_id=workflow.id,
        number=number,
        definition=definition,
        corpus_revision=workspace.revision,
        model_profile=profile,
        created_by=user_id,
        fingerprint=fingerprint(definition, workspace.revision, profile),
    )
    db.add(version)
    db.flush()
    return version


def run_access(db, user, workspace_id, run_id, admin=False):
    run = workspace_object(db, user, workspace_id, Run, run_id)
    if run.private and run.user_id != user.id:
        raise HTTPException(404, "Run not found")
    if admin and run.user_id != user.id:
        require_member(db, user.id, workspace_id, True)
    membership = require_member(db, user.id, workspace_id)
    if run.user_id != user.id and membership.role != "admin":
        raise HTTPException(404, "Run not found")
    return run


def safe_run(db, run):
    value = record(run, {"idempotency_key"})
    version = db.get(WorkflowVersion, run.version_id)
    value["version_number"] = version.number
    value["workflow_name"] = db.get(Workflow, version.workflow_id).name
    from backend.google_services import source_available

    external_revoked = (
        any(not source_available(db, run.user_id, run.workspace_id, source) for source in run.sources)
        if not run.tool_fixtures
        else False
    )
    if revoked_snapshot(db, run.workspace_id, version.corpus_revision) or external_revoked:
        value["question"] = "[Source access revoked]"
        value["answer"], value["sources"] = None, []
        value["content_revoked"] = True
    else:
        value["content_revoked"] = False
    if run.question == "[Content removed by retention policy]":
        value["error"] = "This run's detailed content expired under the retention policy."
    return value


def safe_evaluation(db, report):
    value = record(report)
    version = db.get(WorkflowVersion, report.version_id)
    if revoked_snapshot(db, report.workspace_id, version.corpus_revision):
        value["results"] = [
            {**result, "question": "[Source revoked]", "rationale": "Source content is no longer accessible."}
            for result in report.results
        ]
    return value


P = "/api/v1"
W = P + "/workspaces/{workspace_id}"


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": "0.1.0"}


@app.get("/ready")
def readiness(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        if settings().job_mode == "celery":
            import redis

            redis.Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
    except Exception:
        raise HTTPException(503, "A required service is unavailable")
    return {"status": "ready"}


@app.get(P + "/operations")
def operations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_system_admin(user)
    cutoff = now() - timedelta(days=1)
    status_counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    oldest = db.scalar(select(func.min(Job.created_at)).where(Job.status == "queued"))
    failed_runs = db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.created_at >= cutoff, Run.status.in_(["failed", "timed_out"]))
    )
    return {
        "jobs": status_counts,
        "oldest_queued_seconds": (now() - oldest).total_seconds() if oldest else 0,
        "failed_runs_last_24h": failed_runs,
        "trace_retention_days": settings().trace_retention_days,
    }


@app.post(P + "/auth/login")
def login(body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    key = hash_token(email + ":" + (request.client.host if request.client else "local"))
    failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.key == key,
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at > now() - timedelta(minutes=15),
        )
    )
    if failures >= 10:
        raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes.")
    user = db.scalar(select(User).where(User.email == email))
    valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    success = bool(user and user.active and valid)
    db.add(LoginAttempt(key=key, success=success))
    db.commit()
    if not success:
        raise HTTPException(401, "Email or password is incorrect")
    token, login_session = new_session(db, user.id, settings().session_hours)
    audit(db, user.id, "auth.login")
    response.set_cookie(
        "workspace_session",
        token,
        httponly=True,
        secure=settings().cookie_secure,
        # Google returns by cross-site top-level GET; Strict drops the session.
        # Mutating requests still require CSRF validation, and OAuth validates state.
        samesite="lax",
        max_age=settings().session_hours * 3600,
        path="/",
    )
    return {"user": account(user), "csrf_token": login_session.csrf}


@app.get(P + "/auth/me")
def me(request: Request, user: User = Depends(current_user)):
    return {"user": account(user), "csrf_token": request.state.auth_session.csrf}


@app.post(P + "/auth/logout")
def logout(
    request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    db.delete(request.state.auth_session)
    response.delete_cookie("workspace_session", path="/")
    audit(db, user.id, "auth.logout")
    return {"ok": True}


@app.get(P + "/configuration")
def configuration(user: User = Depends(current_user)):
    cfg = settings()
    return {
        "model_profile": cfg.model_profile(),
        "environment": cfg.app_env,
        "job_mode": cfg.job_mode,
        "max_upload_mb": cfg.max_upload_mb,
        "run_deadline_seconds": cfg.run_deadline_seconds,
        "trace_retention_days": cfg.trace_retention_days,
    }


@app.get(P + "/accounts")
def accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_system_admin(user)
    return [account(u) for u in db.scalars(select(User).order_by(User.name))]


@app.post(P + "/accounts", status_code=201)
def create_account(body: AccountInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_system_admin(user)
    new_user = User(email=body.email, name=body.name, password_hash=hasher.hash(body.password))
    db.add(new_user)
    db.flush()
    audit(db, user.id, "account.created", new_user.id)
    return account(new_user)


@app.delete(P + "/accounts/{account_id}")
def disable_account(account_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_system_admin(user)
    target = db.get(User, account_id)
    if not target:
        raise HTTPException(404, "Account not found")
    if target.is_system_admin:
        raise HTTPException(400, "System administrators cannot be disabled through this endpoint")
    target.active = False
    db.execute(delete(AuthSession).where(AuthSession.user_id == account_id))
    audit(db, user.id, "account.disabled", account_id)
    return {"ok": True}


@app.get(P + "/workspaces")
def workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at)
    ).all()
    return [{**record(workspace), "role": role} for workspace, role in rows]


@app.post(P + "/workspaces", status_code=201)
def create_workspace(body: WorkspaceInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_system_admin(user)
    workspace = Workspace(**body.model_dump())
    db.add(workspace)
    db.flush()
    db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
    audit(db, user.id, "workspace.created", workspace.id, workspace.id)
    return record(workspace)


@app.get(W + "/members")
def members(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id, True)
    return [
        {**account(member), "role": role}
        for member, role in db.execute(
            select(User, Membership.role)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.workspace_id == workspace_id)
        )
    ]


@app.post(W + "/members")
def set_member(
    workspace_id: str, body: MemberInput, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_member(db, user.id, workspace_id, True)
    target = db.get(User, body.user_id)
    if not target or not target.active:
        raise HTTPException(404, "Active account not found")
    membership = db.scalar(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == body.user_id)
    )
    if membership and membership.role == "admin" and body.role != "admin":
        count = db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.workspace_id == workspace_id, Membership.role == "admin")
        )
        if count < 2:
            raise HTTPException(400, "Keep at least one workspace administrator")
    if membership:
        membership.role = body.role
    else:
        db.add(Membership(workspace_id=workspace_id, user_id=body.user_id, role=body.role))
    audit(db, user.id, "membership.updated", body.user_id, workspace_id, role=body.role)
    return {"ok": True}


@app.delete(W + "/members/{member_id}")
def remove_member(
    workspace_id: str, member_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_member(db, user.id, workspace_id, True)
    membership = db.scalar(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == member_id)
    )
    if not membership:
        raise HTTPException(404, "Member not found")
    if membership.role == "admin":
        count = db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.workspace_id == workspace_id, Membership.role == "admin")
        )
        if count < 2:
            raise HTTPException(400, "Keep at least one workspace administrator")
    db.delete(membership)
    audit(db, user.id, "membership.removed", member_id, workspace_id)
    return {"ok": True}


@app.get(W + "/documents")
def documents(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id)
    return [
        record(doc, {"path", "embedding_profile"})
        for doc in db.scalars(
            select(Document)
            .where(Document.workspace_id == workspace_id, Document.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
        )
    ]


@app.post(W + "/documents", status_code=201)
async def upload_document(
    workspace_id: str,
    file: UploadFile = File(...),
    replaces: str | None = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_member(db, user.id, workspace_id, True)
    name = Path((file.filename or "document").replace("\\", "/")).name[:255]
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".txt", ".md"}:
        raise HTTPException(415, "Upload a PDF, UTF-8 text, or Markdown file")
    old = workspace_object(db, user, workspace_id, Document, replaces, True) if replaces else None
    if old and old.deleted_at:
        raise HTTPException(400, "A deleted document cannot be replaced")
    document_id = uid()
    directory = settings().data_dir / "documents" / workspace_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (document_id + suffix)
    size, digest = 0, hashlib.sha256()
    try:
        with path.open("wb") as output:
            while part := await file.read(1024 * 1024):
                size += len(part)
                if size > settings().max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, "File exceeds the upload size limit")
                digest.update(part)
                output.write(part)
        if not size:
            raise HTTPException(400, "The file is empty")
        if suffix == ".pdf" and not path.read_bytes()[:5] == b"%PDF-":
            raise HTTPException(415, "The uploaded file is not a valid PDF")
        version = (
            1
            if not old
            else (
                db.scalar(select(func.max(Document.version)).where(Document.family_id == old.family_id)) or 0
            )
            + 1
        )
        doc = Document(
            id=document_id,
            workspace_id=workspace_id,
            family_id=old.family_id if old else uid(),
            version=version,
            name=name,
            path=str(path),
            size=size,
            checksum=digest.hexdigest(),
            media_type="application/pdf" if suffix == ".pdf" else "text/plain",
            embedding_profile=settings().model_profile(),
        )
        db.add(doc)
        db.flush()
        enqueue(db, "ingestion", doc.id)
        audit(db, user.id, "document.uploaded", doc.id, workspace_id, name=name, version=version)
        db.commit()
        return record(doc, {"path", "embedding_profile"})
    except Exception:
        path.unlink(missing_ok=True)
        raise


@app.get(W + "/documents/{document_id}/chunks")
def document_chunks(
    workspace_id: str, document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    doc = workspace_object(db, user, workspace_id, Document, document_id)
    if doc.deleted_at:
        raise HTTPException(410, "Document has been revoked")
    return [
        record(chunk, {"embedding"})
        for chunk in db.scalars(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ordinal))
    ]


@app.post(W + "/documents/{document_id}/retry")
def retry_document(
    workspace_id: str, document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    doc = workspace_object(db, user, workspace_id, Document, document_id, True)
    if doc.status != "failed" or doc.deleted_at:
        raise HTTPException(409, "Only failed, active documents can be retried")
    doc.status, doc.error = "queued", None
    job = db.scalar(select(Job).where(Job.kind == "ingestion", Job.target_id == doc.id))
    if job:
        job.status, job.error = "queued", None
    else:
        enqueue(db, "ingestion", doc.id)
    audit(db, user.id, "document.retry", doc.id, workspace_id)
    return {"ok": True}


@app.delete(W + "/documents/{document_id}")
def revoke_document(
    workspace_id: str, document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    doc = workspace_object(db, user, workspace_id, Document, document_id, True)
    if doc.deleted_at:
        return {"ok": True}
    doc.deleted_at, doc.status = now(), "deleted"
    db.execute(update(Workspace).where(Workspace.id == workspace_id).values(revision=Workspace.revision + 1))
    for workflow in db.scalars(
        select(Workflow).where(
            Workflow.workspace_id == workspace_id, Workflow.published_version_id.is_not(None)
        )
    ):
        version = db.get(WorkflowVersion, workflow.published_version_id)
        if (
            doc.introduced_revision is not None
            and doc.introduced_revision <= version.corpus_revision
            and (doc.superseded_revision is None or doc.superseded_revision > version.corpus_revision)
        ):
            workflow.paused = True
    # Retain tombstones for audit and gate invalidation; remove source text and vectors immediately.
    db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    audit(db, user.id, "document.revoked", doc.id, workspace_id)
    db.commit()
    Path(doc.path).unlink(missing_ok=True)
    return {"ok": True}


@app.get(W + "/workflows", response_model=list[WorkflowContract])
def workflows(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    membership = require_member(db, user.id, workspace_id)
    statement = (
        select(Workflow)
        .where(
            Workflow.workspace_id == workspace_id,
            Workflow.archived.is_(False),
            (Workflow.owner_id.is_(None)) | (Workflow.owner_id == user.id),
        )
        .order_by(Workflow.created_at)
    )
    if membership.role != "admin":
        statement = statement.where(
            (Workflow.owner_id == user.id)
            | (Workflow.published_version_id.is_not(None) & Workflow.paused.is_(False))
        )
    result = []
    for workflow in db.scalars(statement):
        versions = db.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.number.desc())
        ).all()
        result.append(
            {
                **record(workflow),
                "versions": [
                    record(v)
                    for v in versions
                    if membership.role == "admin"
                    or workflow.owner_id == user.id
                    or v.id == workflow.published_version_id
                ],
            }
        )
    return result


@app.post(W + "/workflows", status_code=201, response_model=WorkflowContract)
def create_workflow(
    workspace_id: str, body: WorkflowInput, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_member(db, user.id, workspace_id, True)
    workflow = Workflow(workspace_id=workspace_id, name=body.name, description=body.description)
    db.add(workflow)
    db.flush()
    version = make_version(db, workflow, user.id, template(body.template).model_dump())
    audit(db, user.id, "workflow.created", workflow.id, workspace_id)
    return {**record(workflow), "versions": [record(version)]}


@app.post(W + "/workflows/{workflow_id}/versions", status_code=201, response_model=WorkflowVersionContract)
def save_version(
    workspace_id: str,
    workflow_id: str,
    body: WorkflowDefinition,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    workflow = workspace_object(db, user, workspace_id, Workflow, workflow_id, True)
    if workflow.submitted_from:
        raise HTTPException(409, "Review candidates are immutable. Duplicate to edit.")
    version = make_version(db, workflow, user.id, body.model_dump())
    audit(db, user.id, "workflow.version_created", version.id, workspace_id, workflow_id=workflow.id)
    return record(version)


@app.post(W + "/workflows/{workflow_id}/duplicate", status_code=201, response_model=WorkflowContract)
def duplicate_workflow(
    workspace_id: str, workflow_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    old = workspace_object(db, user, workspace_id, Workflow, workflow_id, True)
    latest = db.scalar(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == old.id)
        .order_by(WorkflowVersion.number.desc())
        .limit(1)
    )
    workflow = Workflow(
        workspace_id=workspace_id, name=(old.name[:105] + " (copy)"), description=old.description
    )
    db.add(workflow)
    db.flush()
    version = make_version(db, workflow, user.id, latest.definition)
    audit(db, user.id, "workflow.duplicated", workflow.id, workspace_id, source=old.id)
    return {**record(workflow), "versions": [record(version)]}


@app.post(W + "/workflows/{workflow_id}/publish")
def publish(
    workspace_id: str,
    workflow_id: str,
    body: PublishInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    workflow = workspace_object(db, user, workspace_id, Workflow, workflow_id, True)
    version = version_for(db, workflow, body.version_id)
    report = db.get(EvaluationReport, body.report_id)
    problem = publication_problem(db, workflow, version, report)
    if problem:
        audit(db, user.id, "publication.blocked", workflow.id, workspace_id, reason=problem)
        db.commit()
        raise HTTPException(409, problem)
    prior = workflow.published_version_id
    workflow.published_version_id, workflow.paused = version.id, False
    audit(
        db,
        user.id,
        "workflow.published",
        workflow.id,
        workspace_id,
        version_id=version.id,
        previous_version_id=prior,
        report_id=report.id,
    )
    return record(workflow)


@app.post(W + "/workflows/{workflow_id}/runs", status_code=202, response_model=RunContract)
def create_run(
    workspace_id: str,
    workflow_id: str,
    body: RunInput,
    idempotency_key: str | None = Header(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    workflow = workspace_object(db, user, workspace_id, Workflow, workflow_id)
    personal = workflow.owner_id == user.id
    if body.preview and not personal:
        require_member(db, user.id, workspace_id, True)
    if idempotency_key and (len(idempotency_key) > 120 or idempotency_key.startswith("eval:")):
        raise HTTPException(400, "Invalid idempotency key")
    if personal:
        selected = body.version_id or db.scalar(
            select(WorkflowVersion.id)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.number.desc())
        )
        version = version_for(db, workflow, selected)
    elif body.preview:
        if not body.version_id:
            raise HTTPException(400, "Preview requires a saved workflow version")
        version = version_for(db, workflow, body.version_id)
    else:
        if not workflow.published_version_id or workflow.paused:
            raise HTTPException(409, "This assistant is not published or is paused")
        if body.version_id and body.version_id != workflow.published_version_id:
            raise HTTPException(409, "Only the published version can answer employee questions")
        version = version_for(db, workflow, workflow.published_version_id)
    if version.model_profile.get("provider") != "openai":
        raise HTTPException(409, "This historical provider is retired. Save a new live version.")
    if revoked_snapshot(db, workspace_id, version.corpus_revision):
        raise HTTPException(409, "This knowledge snapshot contains a revoked source")
    if idempotency_key:
        existing = db.scalar(
            select(Run).where(Run.user_id == user.id, Run.idempotency_key == idempotency_key)
        )
        if existing:
            if (
                existing.workspace_id != workspace_id
                or existing.version_id != version.id
                or existing.question != body.question
            ):
                raise HTTPException(409, "Idempotency key was already used with another request")
            return safe_run(db, existing)
    active = db.scalar(
        select(func.count())
        .select_from(Run)
        .where(Run.user_id == user.id, Run.status.in_(["queued", "running"]))
    )
    if active >= 5:
        raise HTTPException(429, "You already have five active runs")
    conversation_id = body.conversation_id
    if conversation_id:
        conversation = workspace_object(db, user, workspace_id, Conversation, conversation_id)
        if conversation.user_id != user.id or conversation.workflow_id != workflow.id:
            raise HTTPException(404, "Conversation not found")
    elif not body.preview:
        conversation = Conversation(
            workspace_id=workspace_id, user_id=user.id, workflow_id=workflow.id, title=body.question[:120]
        )
        db.add(conversation)
        db.flush()
        conversation_id = conversation.id
    if body.parent_run_id:
        run_access(db, user, workspace_id, body.parent_run_id)
    run = Run(
        workspace_id=workspace_id,
        user_id=user.id,
        version_id=version.id,
        question=body.question,
        kind="personal" if personal else "preview" if body.preview else "assistant",
        private=personal
        or any(
            any(t.startswith(("gmail_", "drive_")) for t in n.get("config", {}).get("tools", []))
            for n in version.definition["nodes"]
        ),
        conversation_id=conversation_id,
        parent_run_id=body.parent_run_id,
        idempotency_key=idempotency_key,
        model_profile=version.model_profile,
    )
    db.add(run)
    db.flush()
    enqueue(db, "run", run.id)
    db.add(RunEvent(run_id=run.id, event="status", data={"status": "queued"}))
    audit(db, user.id, "run.created", run.id, workspace_id, version_id=version.id)
    return safe_run(db, run)


@app.get(W + "/runs", response_model=list[RunContract])
def runs(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    membership = require_member(db, user.id, workspace_id)
    statement = select(Run).where(
        Run.workspace_id == workspace_id,
        Run.kind != "evaluation",
        (Run.private.is_(False)) | (Run.user_id == user.id),
    )
    if membership.role != "admin":
        statement = statement.where(Run.user_id == user.id)
    return [safe_run(db, run) for run in db.scalars(statement.order_by(Run.created_at.desc()).limit(100))]


@app.get(W + "/runs/{run_id}", response_model=RunContract)
def get_run(
    workspace_id: str, run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return safe_run(db, run_access(db, user, workspace_id, run_id))


@app.get(W + "/runs/{run_id}/traces", response_model=list[StepTraceContract])
def traces(workspace_id: str, run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = run_access(db, user, workspace_id, run_id, True)
    revoked = safe_run(db, run)["content_revoked"]
    return [
        {
            **record(trace),
            **({"inputs": {}, "outputs": {}, "tool_calls": [], "content_revoked": True} if revoked else {}),
        }
        for trace in db.scalars(
            select(StepTrace).where(StepTrace.run_id == run.id).order_by(StepTrace.created_at)
        )
    ]


@app.post(W + "/runs/{run_id}/cancel")
def cancel_run(
    workspace_id: str, run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    run = run_access(db, user, workspace_id, run_id)
    if run.status not in TERMINAL:
        run.cancel_requested = True
        if run.status == "queued":
            run.status, run.finished_at = "cancelled", now()
            db.add(RunEvent(run_id=run.id, event="status", data={"status": "cancelled"}))
    audit(db, user.id, "run.cancel_requested", run.id, workspace_id)
    return {"ok": True}


@app.get(W + "/runs/{run_id}/events")
async def run_events(
    workspace_id: str,
    run_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    run_access(db, user, workspace_id, run_id)
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError:
        raise HTTPException(400, "Invalid event cursor")
    session_id = request.state.auth_session.id
    user_id = user.id

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            with session_scope() as fresh:
                login_session = fresh.get(AuthSession, session_id)
                try:
                    if not login_session or login_session.expires_at <= now():
                        raise HTTPException(401)
                    require_member(fresh, user_id, workspace_id)
                except HTTPException:
                    yield "event: access_revoked\ndata: {}\n\n"
                    return
                events = fresh.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.id > cursor)
                    .order_by(RunEvent.id)
                    .limit(100)
                ).all()
                for event in events:
                    cursor = event.id
                    yield f"id: {event.id}\nevent: {event.event}\ndata: {json.dumps(event.data)}\n\n"
                run = fresh.get(Run, run_id)
                if run.status in TERMINAL and len(events) < 100:
                    yield "event: done\ndata: {}\n\n"
                    return
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.get(W + "/conversations")
def conversations(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id)
    return [
        record(c)
        for c in db.scalars(
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id, Conversation.user_id == user.id)
            .order_by(Conversation.created_at.desc())
            .limit(100)
        )
    ]


@app.get(W + "/conversations/{conversation_id}")
def conversation(
    workspace_id: str, conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    item = workspace_object(db, user, workspace_id, Conversation, conversation_id)
    if item.user_id != user.id:
        raise HTTPException(404, "Conversation not found")
    return {
        **record(item),
        "runs": [
            safe_run(db, run)
            for run in db.scalars(select(Run).where(Run.conversation_id == item.id).order_by(Run.created_at))
        ],
    }


@app.post(W + "/runs/{run_id}/feedback", status_code=201)
def feedback(
    workspace_id: str,
    run_id: str,
    body: FeedbackInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    run = run_access(db, user, workspace_id, run_id)
    if run.status != "completed":
        raise HTTPException(409, "Feedback requires a completed answer")
    item = Feedback(run_id=run_id, user_id=user.id, **body.model_dump())
    db.add(item)
    db.flush()
    audit(db, user.id, "feedback.created", item.id, workspace_id)
    return record(item)


@app.get(W + "/feedback")
def list_feedback(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id, True)
    return [
        {**record(item), "question": run.question}
        for item, run in db.execute(
            select(Feedback, Run)
            .join(Run, Run.id == Feedback.run_id)
            .where(Run.workspace_id == workspace_id, (Run.private.is_(False)) | (Run.user_id == user.id))
            .order_by(Feedback.created_at.desc())
            .limit(100)
        )
    ]


def validate_suite_sources(db, workspace_id, cases):
    for case in cases:
        fixture_ids = {s["document_id"] for f in case.get("tool_fixtures", []) for s in f["sources"]}
        for doc_id in case["source_document_ids"]:
            if doc_id in fixture_ids:
                continue
            doc = db.get(Document, doc_id)
            if not doc or doc.workspace_id != workspace_id or doc.deleted_at or doc.status != "ready":
                raise HTTPException(400, "Evaluation sources must be indexed documents in this workspace")


@app.get(W + "/evaluation-suites", response_model=list[EvaluationSuiteContract])
def suites(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id, True)
    return [
        record(s)
        for s in db.scalars(
            select(EvaluationSuite)
            .where(EvaluationSuite.workspace_id == workspace_id)
            .order_by(EvaluationSuite.created_at.desc())
        )
    ]


@app.post(W + "/evaluation-suites", status_code=201, response_model=EvaluationSuiteContract)
def create_suite(
    workspace_id: str,
    body: EvaluationSuiteInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_member(db, user.id, workspace_id, True)
    data = body.model_dump()
    validate_suite_sources(db, workspace_id, data["cases"])
    suite = EvaluationSuite(workspace_id=workspace_id, **data)
    db.add(suite)
    db.flush()
    audit(db, user.id, "evaluation_suite.created", suite.id, workspace_id)
    return record(suite)


@app.post(
    W + "/evaluation-suites/{suite_id}/versions", status_code=201, response_model=EvaluationSuiteContract
)
def revise_suite(
    workspace_id: str,
    suite_id: str,
    body: EvaluationSuiteInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    old = workspace_object(db, user, workspace_id, EvaluationSuite, suite_id, True)
    data = body.model_dump()
    validate_suite_sources(db, workspace_id, data["cases"])
    latest = db.scalar(
        select(func.max(EvaluationSuite.version)).where(EvaluationSuite.family_id == old.family_id)
    )
    suite = EvaluationSuite(workspace_id=workspace_id, family_id=old.family_id, version=latest + 1, **data)
    db.add(suite)
    db.flush()
    audit(db, user.id, "evaluation_suite.revised", suite.id, workspace_id)
    return record(suite)


@app.post(W + "/evaluation-suites/{suite_id}/review", response_model=EvaluationSuiteContract)
def review_suite(
    workspace_id: str,
    suite_id: str,
    body: ReviewInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    suite = workspace_object(db, user, workspace_id, EvaluationSuite, suite_id, True)
    if not suite.reviewed_at:
        suite.reviewed_at, suite.reviewed_by, suite.review_notes = now(), user.id, body.notes
        audit(db, user.id, "evaluation_suite.reviewed", suite.id, workspace_id)
    return record(suite)


@app.post(W + "/feedback/{feedback_id}/promote", status_code=201, response_model=EvaluationSuiteContract)
def promote_feedback(
    workspace_id: str,
    feedback_id: str,
    body: FeedbackPromotion,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_member(db, user.id, workspace_id, True)
    item = db.get(Feedback, feedback_id)
    if not item:
        raise HTTPException(404, "Feedback not found")
    run = run_access(db, user, workspace_id, item.run_id, True)
    old = workspace_object(db, user, workspace_id, EvaluationSuite, body.suite_id, True)
    case = EvaluationCase(
        id="feedback-" + item.id,
        question=run.question,
        reference_answer=body.reference_answer,
        source_document_ids=body.source_document_ids,
        expected_abstention=body.expected_abstention,
        category="unanswerable" if body.expected_abstention else "answerable",
    )
    cases = old.cases + [case.model_dump()]
    validate_suite_sources(db, workspace_id, cases)
    latest = db.scalar(
        select(func.max(EvaluationSuite.version)).where(EvaluationSuite.family_id == old.family_id)
    )
    suite = EvaluationSuite(
        workspace_id=workspace_id,
        family_id=old.family_id,
        name=old.name,
        version=latest + 1,
        cases=cases,
        thresholds=old.thresholds,
    )
    db.add(suite)
    db.flush()
    item.promoted_suite_id = suite.id
    audit(db, user.id, "feedback.promoted", item.id, workspace_id, suite_id=suite.id)
    return record(suite)


@app.get(W + "/evaluations", response_model=list[EvaluationReportContract])
def evaluations(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id, True)
    return [
        safe_evaluation(db, r)
        for r in db.scalars(
            select(EvaluationReport)
            .where(EvaluationReport.workspace_id == workspace_id)
            .order_by(EvaluationReport.created_at.desc())
            .limit(50)
        )
    ]


@app.post(W + "/evaluations", status_code=202, response_model=EvaluationReportContract)
def evaluate(
    workspace_id: str,
    body: EvaluationInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_member(db, user.id, workspace_id, True)
    version = db.get(WorkflowVersion, body.version_id)
    workflow = db.get(Workflow, version.workflow_id) if version else None
    if not workflow or workflow.workspace_id != workspace_id:
        raise HTTPException(404, "Workflow version not found")
    suite = workspace_object(db, user, workspace_id, EvaluationSuite, body.suite_id, True)
    if workflow.owner_id and workflow.owner_id != user.id:
        raise HTTPException(404, "Workflow version not found")
    external = any(
        t.startswith(("gmail_", "drive_", "web_"))
        for n in version.definition["nodes"]
        for t in n.get("config", {}).get("tools", [])
    )
    if external and any(not c.get("fixture_version") for c in suite.cases):
        raise HTTPException(
            409,
            "External-tool evaluations require versioned, reviewed fixtures for every case. Use Connections for separate live checks.",
        )
    if not suite.reviewed_at:
        raise HTTPException(409, "Review the evaluation examples before running this suite")
    validate_suite_sources(db, workspace_id, suite.cases)
    if revoked_snapshot(db, workspace_id, version.corpus_revision):
        raise HTTPException(409, "Save a workflow version using the current knowledge revision")
    active = db.scalar(
        select(func.count())
        .select_from(EvaluationReport)
        .where(
            EvaluationReport.workspace_id == workspace_id, EvaluationReport.status.in_(["queued", "running"])
        )
    )
    if active >= 2:
        raise HTTPException(429, "Two evaluations are already active in this workspace")
    report = EvaluationReport(
        workspace_id=workspace_id,
        user_id=user.id,
        version_id=version.id,
        suite_id=suite.id,
        fingerprint=report_fingerprint(version, suite),
    )
    db.add(report)
    db.flush()
    enqueue(db, "evaluation", report.id)
    audit(db, user.id, "evaluation.created", report.id, workspace_id, version_id=version.id)
    return record(report)


@app.get(W + "/audit")
def audit_events(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id, True)
    return [
        record(event)
        for event in db.scalars(
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(200)
        )
    ]
