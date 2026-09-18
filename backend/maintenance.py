"""Retention removes conversational payloads, preserving operational metadata and audit events."""

import argparse
from datetime import timedelta

from sqlalchemy import delete, select

from backend.config import settings
from backend.db import now, session_scope
from backend.execution import TERMINAL, checkpointer
from backend.knowledge import revoked_snapshot
from backend.models import (
    ToolReceipt,
    OAuthState,
    AuthSession,
    LoginAttempt,
    Run,
    RunEvent,
    StepTrace,
    WorkflowVersion,
)


def purge_payloads():
    cutoff = now() - timedelta(days=settings().trace_retention_days)
    with session_scope() as db:
        expired = db.scalars(select(Run).where(Run.status.in_(TERMINAL))).all()
        ids = []
        for run in expired:
            version = db.get(WorkflowVersion, run.version_id)
            if run.created_at < cutoff or revoked_snapshot(db, run.workspace_id, version.corpus_revision):
                ids.append(run.id)
                run.question = "[Content removed by retention policy]"
                run.answer, run.sources = None, []
                for trace in db.scalars(select(StepTrace).where(StepTrace.run_id == run.id)):
                    trace.inputs, trace.outputs, trace.tool_calls = {}, {}, []
                db.execute(delete(RunEvent).where(RunEvent.run_id == run.id))
                db.execute(delete(ToolReceipt).where(ToolReceipt.run_id == run.id))
                run.tool_fixtures = []
        db.execute(delete(OAuthState).where(OAuthState.expires_at < now()))
        db.execute(delete(AuthSession).where(AuthSession.expires_at < now()))
        db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now() - timedelta(days=1)))
    with checkpointer() as saver:
        for run_id in ids:
            saver.delete_thread(run_id)
    return len(ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["purge"])
    parser.parse_args()
    print(f"Purged payloads for {purge_payloads()} expired or revoked runs. Audit history retained.")


if __name__ == "__main__":
    main()
