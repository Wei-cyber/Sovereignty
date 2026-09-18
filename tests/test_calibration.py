import json

import pytest

from backend.calibration import calibrate
from backend.config import settings
from backend.providers import ModelResult


def dataset():
    return {
        "reviewed_by": "Automated fixture, not human review",
        "reviewed_at": "2026-09-08T00:00:00Z",
        "examples": [
            {
                "question": f"Fixture question {i}",
                "reference_answer": "Employees receive 25 days.",
                "answer": {"text": "25 days" if i % 2 else "Wrong", "citations": [], "abstained": False},
                "sources": [
                    {
                        "chunk_id": "chunk",
                        "document_id": "doc",
                        "document_name": "test",
                        "document_version": 1,
                        "location": "section",
                        "text": "Employees receive 25 days.",
                    }
                ],
                "human_correctness": i % 2,
                "human_evidence_support": i % 2,
            }
            for i in range(10)
        ],
    }


class FixtureGrader:
    def grade(self, question, reference, answer, sources):
        score = float(answer["text"] == "25 days")
        return ModelResult({"correctness": score, "evidence_support": score, "rationale": "Test only"})


def test_calibration_binds_reviewed_labels_and_grader(environment, tmp_path):
    result = calibrate(json.dumps(dataset()).encode(), FixtureGrader())
    assert result["passed"] and result["metrics"]["correctness_mae"] == 0
    target = tmp_path / "calibration.json"
    target.write_text(json.dumps(result))
    settings().grader_calibration_file = str(target)
    assert settings().calibration_valid()
    approved_profile = settings().model_profile()
    result["grader_identity"]["grader_revision"] = "old-grader"
    target.write_text(json.dumps(result))
    assert not settings().calibration_valid()
    assert approved_profile != settings().model_profile()


def test_calibration_rejects_unrepresentative_or_duplicate_examples(environment):
    raw = dataset()
    raw["examples"] = [raw["examples"][0]] * 10
    with pytest.raises(ValueError, match="distinct"):
        calibrate(json.dumps(raw).encode(), FixtureGrader())
    raw = dataset()
    for example in raw["examples"]:
        example["human_correctness"] = 1
    with pytest.raises(ValueError, match="poor and strong"):
        calibrate(json.dumps(raw).encode(), FixtureGrader())


def test_calibration_fails_when_grader_disagrees(environment):
    raw = dataset()
    for example in raw["examples"]:
        example["human_correctness"] = 1 - example["human_correctness"]
    result = calibrate(json.dumps(raw).encode(), FixtureGrader())
    assert not result["passed"] and result["metrics"]["correctness_mae"] == 1
