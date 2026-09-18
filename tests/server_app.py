"""Explicit offline browser-test entry point. Never included in deployment images."""

from pathlib import Path
from unittest.mock import patch
import os

from backend.config import settings, Settings

from tests.doubles import DeterministicProvider
from tests.runtime import calibration_fixture

Settings.model_config["env_file"] = None
settings.cache_clear()

if Path(settings().data_dir).resolve().name != "e2e":
    raise RuntimeError("Browser fixture runtime requires the isolated e2e directory")
for module in [
    "backend.providers",
    "backend.execution",
    "backend.evaluations",
    "backend.calibration",
    "backend.calibration_workspace",
]:
    patch(module + ".provider", DeterministicProvider).start()
patch("backend.embeddings.local_bge", lambda _: DeterministicProvider()).start()
calibration_fixture(settings().data_dir, settings())

if os.environ.get("RELAY_E2E_SEED") == "1":
    from backend.manage import migrate, bootstrap
    from backend.db import session_scope
    from backend.models import User, Membership, Document
    from sqlalchemy import select
    from tests.seed import seed_fixture

    migrate()
    bootstrap()
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == settings().bootstrap_email))
        workspace = db.scalar(select(Membership.workspace_id).where(Membership.user_id == user.id))
        seeded = bool(db.scalar(select(Document.id).where(Document.workspace_id == workspace).limit(1)))
        user_id = user.id
    if not seeded:
        seed_fixture(workspace, user_id)

from backend.main import app  # noqa: E402,F401
