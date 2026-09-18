"""One-way retirement of synthetic releases; historical records remain intact."""

from sqlalchemy import select, delete
from argon2.exceptions import VerifyMismatchError
from backend.config import settings
from backend.db import session_scope, now
from backend.models import User, AuthSession, Workflow, WorkflowVersion, Run, Document, EvaluationReport, Job
from backend.security import hasher, audit


def retire_synthetic():
    legacy_password = settings().retired_demo_password
    with session_scope() as db:
        for user in db.scalars(select(User).where(User.email == "admin@example.test", User.active.is_(True))):
            if not legacy_password:
                continue
            try:
                hasher.verify(user.password_hash, legacy_password)
            except VerifyMismatchError:
                continue
            user.active = False
            db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            audit(db, None, "account.synthetic_login_retired", user.id)
        retired = {
            v.id
            for v in db.scalars(select(WorkflowVersion))
            if v.model_profile.get("provider") != "openai"
            or v.model_profile.get("embedding_provider") == "demo"
        }
        for workflow in db.scalars(select(Workflow).where(Workflow.published_version_id.in_(retired))):
            if not workflow.paused:
                workflow.paused = True
                audit(db, None, "workflow.synthetic_release_retired", workflow.id, workflow.workspace_id)
        targets = set()
        for run in db.scalars(
            select(Run).where(Run.version_id.in_(retired), Run.status.in_(["queued", "running"]))
        ):
            run.status, run.error, run.finished_at = (
                "failed",
                "Historical synthetic inference has been retired",
                now(),
            )
            targets.add(run.id)
        for report in db.scalars(
            select(EvaluationReport).where(
                EvaluationReport.version_id.in_(retired), EvaluationReport.status.in_(["queued", "running"])
            )
        ):
            report.status, report.error = "failed", "Historical synthetic inference has been retired"
            targets.add(report.id)
        for doc in db.scalars(select(Document).where(Document.status.in_(["queued", "processing"]))):
            if (
                doc.embedding_profile.get("embedding_provider", doc.embedding_profile.get("provider"))
                == "demo"
            ):
                doc.status, doc.error = (
                    "failed",
                    "Synthetic embedding job retired; explicitly reindex to use local BGE",
                )
                targets.add(doc.id)
        for job in db.scalars(
            select(Job).where(Job.target_id.in_(targets), Job.status.in_(["queued", "running"]))
        ):
            job.status, job.error = "failed", "Synthetic job retired without provider calls"
