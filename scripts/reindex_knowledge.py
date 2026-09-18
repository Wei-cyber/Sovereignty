"""Queue new document versions for the configured embedding model, preserving old citations."""

import argparse
import shutil
from pathlib import Path

from sqlalchemy import select

from backend.config import settings
from backend.db import session_scope
from backend.embeddings import embedding_identity, embedding_provider
from backend.jobs import enqueue
from backend.models import Document, User, Workspace, uid
from backend.security import audit, require_member


def reindex(workspace_id, actor_id):
    profile = settings().model_profile()
    embedding_provider(profile)  # Fail before creating candidates if local artifacts are missing.
    queued, copied = [], []
    try:
        with session_scope() as db:
            require_member(db, actor_id, workspace_id, admin=True)
            db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
            documents = db.scalars(
                select(Document)
                .where(Document.workspace_id == workspace_id)
                .order_by(Document.version.desc())
            ).all()
            families = set()
            for old in documents:
                if old.family_id in families:
                    continue
                families.add(old.family_id)
                if old.deleted_at:
                    continue
                if embedding_identity(old.embedding_profile) == embedding_identity(profile):
                    if old.status == "failed":
                        old.status, old.error = "queued", None
                        enqueue(db, "ingestion", old.id)
                        queued.append(old.id)
                    continue
                if old.status != "ready":
                    raise ValueError("Finish or resolve existing document ingestion before reindexing")
                document_id = uid()
                directory = settings().data_dir / "documents" / workspace_id
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / (document_id + Path(old.path).suffix)
                copied.append(path)
                shutil.copyfile(old.path, path)
                doc = Document(
                    id=document_id,
                    workspace_id=workspace_id,
                    family_id=old.family_id,
                    version=old.version + 1,
                    name=old.name,
                    path=str(path),
                    media_type=old.media_type,
                    size=old.size,
                    checksum=old.checksum,
                    embedding_profile=profile,
                )
                db.add(doc)
                enqueue(db, "ingestion", doc.id)
                queued.append(doc.id)
            audit(
                db,
                actor_id,
                "knowledge.reindex_queued",
                workspace_id,
                workspace_id,
                document_ids=queued,
                embedding_profile=embedding_identity(profile),
            )
    except Exception:
        for path in copied:
            path.unlink(missing_ok=True)
        raise
    return queued


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--admin-email", required=True)
    args = parser.parse_args()
    with session_scope() as db:
        actor = db.scalar(select(User).where(User.email == args.admin_email.lower(), User.active.is_(True)))
        if not actor:
            raise SystemExit("Active administrator account not found")
        actor_id = actor.id
    ids = reindex(args.workspace, actor_id)
    print(
        f"Queued {len(ids)} document versions. Keep workers running; evaluate a new workflow version after ingestion."
    )


if __name__ == "__main__":
    main()
