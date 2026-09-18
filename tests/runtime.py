"""Offline test runtime. This package is not copied into application images."""

import json
from pathlib import Path
from tests.doubles import DeterministicProvider


def install(monkeypatch):
    for module in [
        "backend.providers",
        "backend.execution",
        "backend.evaluations",
        "backend.calibration",
        "backend.calibration_workspace",
    ]:
        monkeypatch.setattr(module + ".provider", DeterministicProvider)
    monkeypatch.setattr("backend.embeddings.local_bge", lambda _: DeterministicProvider())


def calibration_fixture(directory, cfg):
    path = Path(directory) / "TEST-ONLY-calibration.json"
    path.write_text(
        json.dumps(
            {
                "passed": True,
                "grader_identity": cfg.grader_identity(),
                "reviewed_by": "SIMULATED TEST REVIEW",
                "reviewed_at": "2000-01-01",
                "metrics": {"examples": 10, "correctness_mae": 0, "evidence_support_mae": 0},
            }
        )
    )
    cfg.grader_calibration_file = str(path)
