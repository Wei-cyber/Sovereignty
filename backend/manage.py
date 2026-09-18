import argparse
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy import text

from backend.config import settings
from backend.db import session_scope
from backend.execution import initialize_checkpoints
from backend.models import AuthSession, Membership, User, Workspace
from backend.security import audit, hasher


def migrate():
    command.upgrade(Config("alembic.ini"), "head")
    initialize_checkpoints()
    from backend.retirement import retire_synthetic

    retire_synthetic()
    if settings().database_url.startswith("postgresql"):
        with session_scope() as db:
            if db.scalar(text("SELECT 1 FROM pg_roles WHERE rolname='relay_app'")):
                db.execute(
                    text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO relay_app")
                )
                db.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO relay_app"))
                db.execute(text("REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM relay_app"))


def bootstrap():
    cfg = settings()
    password = cfg.bootstrap_password
    if len(password) < 12:
        raise SystemExit("Set BOOTSTRAP_PASSWORD to at least 12 characters before bootstrapping")
    if cfg.database_url.startswith("sqlite"):
        migrate()
    with session_scope() as db:
        existing = db.scalar(select(User).where(User.email == cfg.bootstrap_email.lower()))
        if existing:
            print("Administrator already exists; account and password left unchanged.")
            return
        user = User(
            email=cfg.bootstrap_email.lower(),
            name="Administrator",
            password_hash=hasher.hash(password),
            is_system_admin=True,
        )
        db.add(user)
        db.flush()
        workspace = Workspace(
            name="Company workspace",
            description="A shared space for company knowledge and reliable AI workflows.",
        )
        db.add(workspace)
        db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
        audit(db, user.id, "workspace.bootstrapped", workspace.id, workspace.id)
    print("Workspace initialized. Sign in with the configured bootstrap email and password.")


def retire_demo_login():
    """Remove the known demo credential when an operator explicitly bootstraps live mode."""
    from argon2.exceptions import VerifyMismatchError

    legacy_password = settings().retired_demo_password
    if not legacy_password:
        return
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.test", User.active.is_(True)))
        if not user:
            return
        try:
            hasher.verify(user.password_hash, legacy_password)
        except VerifyMismatchError:
            return
        replacement = db.scalar(
            select(User).where(
                User.email == settings().bootstrap_email.lower(),
                User.id != user.id,
                User.active.is_(True),
                User.is_system_admin.is_(True),
            )
        )
        if not replacement:
            raise SystemExit(
                "Set a different BOOTSTRAP_EMAIL and bootstrap a real administrator before retiring demo access"
            )
        user.active = False
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        audit(db, None, "account.demo_login_retired", user.id)


def export_openapi(output):
    from backend.main import app

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"Exported API contract to {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "bootstrap", "openapi"])
    parser.add_argument("--output", default="frontend/openapi.json")
    args = parser.parse_args()
    if args.action == "migrate":
        migrate()
    elif args.action == "bootstrap":
        bootstrap()
        if args.action == "bootstrap" and settings().model_provider == "openai":
            retire_demo_login()
    else:
        export_openapi(args.output)


if __name__ == "__main__":
    main()
