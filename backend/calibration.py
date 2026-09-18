"""Measure grader agreement with human labels; never manufacture a human review."""

import hashlib
import statistics
from datetime import datetime

from pydantic import Field

from backend.config import settings
from backend.providers import provider
from backend.schemas import Answer, SourceContract, StrictModel, fingerprint


class CalibrationExample(StrictModel):
    question: str = Field(min_length=1)
    reference_answer: str = Field(min_length=1)
    answer: Answer
    sources: list[SourceContract] = Field(min_length=1)
    human_correctness: float = Field(ge=0, le=1)
    human_evidence_support: float = Field(ge=0, le=1)


class CalibrationDataset(StrictModel):
    reviewed_by: str = Field(min_length=3)
    reviewed_at: datetime
    examples: list[CalibrationExample] = Field(min_length=10, max_length=500)


def validate_dataset(raw: bytes):
    dataset = CalibrationDataset.model_validate_json(raw)
    if len({fingerprint(e.question, e.answer.model_dump()) for e in dataset.examples}) < 10:
        raise ValueError("Calibration requires at least ten distinct question/answer examples")
    for field in ["human_correctness", "human_evidence_support"]:
        labels = [getattr(e, field) for e in dataset.examples]
        if min(labels) > 0.25 or max(labels) < 0.75:
            raise ValueError("Calibration must include both poor and strong answers for each metric")
    return dataset


def calibrate(raw: bytes, model=None, progress=None, previous=None, identity=None):
    dataset = validate_dataset(raw)
    model = model or provider()
    results = list(previous or [])
    for index, example in enumerate(dataset.examples):
        if index < len(results):
            continue
        grade = model.grade(
            example.question,
            example.reference_answer,
            example.answer.model_dump(),
            [s.model_dump() for s in example.sources],
        )
        results.append(
            {
                "index": index,
                "human_correctness": example.human_correctness,
                "human_evidence_support": example.human_evidence_support,
                "graded_correctness": grade.value["correctness"],
                "graded_evidence_support": grade.value["evidence_support"],
                "correctness_error": abs(grade.value["correctness"] - example.human_correctness),
                "evidence_support_error": abs(
                    grade.value["evidence_support"] - example.human_evidence_support
                ),
                "tokens": grade.tokens,
            }
        )
        if progress:
            progress(results)
    metrics = {
        "examples": len(results),
        "correctness_mae": statistics.mean(r["correctness_error"] for r in results),
        "evidence_support_mae": statistics.mean(r["evidence_support_error"] for r in results),
    }
    return {
        "grader_identity": identity or settings().grader_identity(),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "reviewed_by": dataset.reviewed_by,
        "reviewed_at": dataset.reviewed_at.isoformat(),
        "passed": metrics["correctness_mae"] <= 0.1 and metrics["evidence_support_mae"] <= 0.1,
        "metrics": metrics,
        "results": results,
    }
