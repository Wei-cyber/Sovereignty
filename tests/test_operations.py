import hashlib
import importlib.util
import io
import json
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

from backend.db import now, session_scope
from backend.maintenance import purge_payloads
from backend.models import Run
from tests.test_workspace import document, path, wait_for, workflow


def test_retention_removes_payloads_and_checkpoint(admin, environment):
    document(admin, environment)
    w = workflow(admin, environment)
    created = admin.post(
        path(environment, f"/workflows/{w['id']}/runs"),
        json={
            "question": "How many vacation days do employees receive?",
            "version_id": w["versions"][0]["id"],
            "preview": True,
        },
    )
    run_id = created.json()["id"]
    wait_for(admin, path(environment, f"/runs/{run_id}"), lambda r: r["status"] == "completed")
    with session_scope() as db:
        db.get(Run, run_id).created_at = now() - timedelta(days=31)
    assert purge_payloads() == 1
    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run.answer is None and run.sources == []
        assert run.question == "[Content removed by retention policy]"
    assert admin.get("/ready").status_code == 200
    assert admin.get("/api/v1/operations").status_code == 200
    assert "expired" in admin.get(path(environment, f"/runs/{run_id}")).json()["error"]


def test_backup_manifest_validation(tmp_path):
    spec = importlib.util.spec_from_file_location("backup_check", Path("scripts/verify-backup.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "database.dump").write_bytes(b"PGDMP-test-fixture")
    with tarfile.open(tmp_path / "documents.tar.gz", "w:gz") as tar:
        body = b"A source document"
        info = tarfile.TarInfo("documents/test.md")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    module.verify(tmp_path)
    (tmp_path / "database.dump").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Checksum"):
        module.verify(tmp_path)
