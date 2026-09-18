import statistics

from sqlalchemy import select

from backend.calibration_workspace import workspace_profile
from backend.db import now, session_scope
from backend.execution import execute_run
from backend.knowledge import revoked_snapshot
from backend.models import EvaluationReport, EvaluationSuite, Run, StepTrace, WorkflowVersion
from backend.providers import provider
from backend.schemas import fingerprint
from backend.security import audit, require_member


def report_fingerprint(version, suite):
    return fingerprint(
        version.fingerprint,
        suite.id,
        suite.version,
        suite.cases,
        suite.thresholds,
        suite.reviewed_by,
        suite.reviewed_at,
    )


def publication_problem(db, workflow, version, report):
    if not version or version.workflow_id != workflow.id:
        return "Workflow version does not belong to this workflow"
    if not report or report.version_id != version.id or report.workspace_id != workflow.workspace_id:
        return "A matching evaluation report is required"
    suite = db.get(EvaluationSuite, report.suite_id)
    latest = db.scalar(
        select(EvaluationSuite)
        .where(EvaluationSuite.family_id == suite.family_id)
        .order_by(EvaluationSuite.version.desc())
        .limit(1)
    )
    if not suite.reviewed_at or not suite.reviewed_by:
        return "An administrator must review the evaluation examples before publication"
    if latest.id != suite.id:
        return "The evaluation suite has a newer version; run it before publishing"
    if report.status != "completed" or not report.passed:
        return "All evaluation checks must pass before publication"
    if report.fingerprint != report_fingerprint(version, suite):
        return "The evaluation report is stale; evaluate this exact configuration again"
    if revoked_snapshot(db, workflow.workspace_id, version.corpus_revision):
        return "A source was revoked; save a new version using current knowledge and evaluate it"
    current_profile = workspace_profile(db, workflow.workspace_id)
    if version.model_profile != current_profile:
        return "Deployment model configuration changed; save and evaluate a new workflow version"
    if (
        version.model_profile["provider"] == "demo"
        or version.model_profile.get("embedding_provider") == "demo"
    ):
        return "Demo evaluations cannot authorize publication in this deployment"
    if (
        version.model_profile["provider"] == "openai"
        and current_profile["grader_calibration_sha256"] == "unavailable"
    ):
        return "A passing grader calibration against reviewed human labels is required for live publication"
    if not any(not c["expected_abstention"] for c in suite.cases) or not any(
        c["expected_abstention"] for c in suite.cases
    ):
        return "The suite must include both answerable questions and expected abstentions"
    return None


def run_evaluation(report_id):
    with session_scope() as db:
        report = db.get(EvaluationReport, report_id)
        if not report or report.status in {"completed", "failed"}:
            return
        require_member(db, report.user_id, report.workspace_id, admin=True)
        report.status = "running"
        suite, version = db.get(EvaluationSuite, report.suite_id), db.get(WorkflowVersion, report.version_id)
        cases, thresholds, model_profile = suite.cases, suite.thresholds, version.model_profile
        user_id, workspace_id, version_id = report.user_id, report.workspace_id, version.id
        results = list(report.results)
    completed_cases = {r["case_id"] for r in results}
    try:
        for case in cases:
            if case["id"] in completed_cases:
                continue
            with session_scope() as db:
                require_member(db, user_id, workspace_id, admin=True)
                existing = db.scalar(
                    select(Run).where(
                        Run.user_id == user_id, Run.idempotency_key == f"eval:{report_id}:{case['id']}"
                    )
                )
                if not existing:
                    existing = Run(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        version_id=version_id,
                        question=case["question"],
                        kind="evaluation",
                        tool_fixtures=case.get("tool_fixtures", []),
                        private=True,
                        model_profile=model_profile,
                        idempotency_key=f"eval:{report_id}:{case['id']}",
                    )
                    db.add(existing)
                    db.flush()
                run_id = existing.id
            execute_run(run_id)
            with session_scope() as db:
                run = db.get(Run, run_id)
                answer = run.answer or {"text": "", "citations": [], "abstained": False}
                traces = db.scalars(select(StepTrace).where(StepTrace.run_id == run_id)).all()
                retrieved = []
                for trace in traces:
                    retrieved.extend(trace.outputs.get("sources", []))
                retrieved = list({s["chunk_id"]: s for s in retrieved}.values())[:5]
                expected = set(case["source_document_ids"])
                recall = (
                    len(expected & {s["document_id"] for s in retrieved}) / len(expected)
                    if expected
                    else None
                )
                sources, latency, tokens, status = run.sources, run.latency_ms or 0, run.tokens, run.status
                actual_tools = [c["tool"] for trace in traces for c in trace.tool_calls]
                critical = int(run.error == "Tool permission denied")
                if case.get("expected_tools") and actual_tools != case["expected_tools"]:
                    critical += 1
                successful = status == "completed"
            if case["expected_abstention"]:
                correctness = support = None
                abstention = float(successful and answer["abstained"])
                rationale = "Expected an explicit abstention."
            elif successful and not answer["abstained"]:
                grade = provider(model_profile).grade(
                    case["question"], case["reference_answer"], answer, sources
                )
                correctness, support = grade.value["correctness"], grade.value["evidence_support"]
                rationale, abstention = grade.value["rationale"], None
            else:
                correctness = support = 0.0
                rationale, abstention = "Run failed or abstained on an answerable question.", None
            source_map = {s["chunk_id"]: s for s in sources}
            citations = answer.get("citations", [])
            valid = successful and (bool(citations) or answer["abstained"])
            for citation in citations:
                source = source_map.get(citation["chunk_id"])
                valid = (
                    valid
                    and bool(source)
                    and " ".join(citation["quote"].split()) in " ".join(source["text"].split())
                )
            if case["category"] == "adversarial" and not (successful and answer["abstained"]):
                critical += 1
            result = {
                "case_id": case["id"],
                "run_id": run_id,
                "question": case["question"],
                "retrieval_recall": recall,
                "answer_correctness": correctness,
                "evidence_support": support,
                "abstention": abstention,
                "citation_validity": float(valid),
                "critical_failures": critical,
                "latency_ms": latency,
                "tokens": tokens,
                "rationale": rationale,
                "status": status,
                "evidence_mode": "reviewed_fixture" if case.get("tool_fixtures") else "live",
                "fixture_version": case.get("fixture_version", ""),
            }
            results.append(result)
            with session_scope() as db:
                db.get(EvaluationReport, report_id).results = list(results)
        metrics = {}
        for metric in [
            "retrieval_recall",
            "answer_correctness",
            "evidence_support",
            "abstention",
            "citation_validity",
        ]:
            values = [r[metric] for r in results if r[metric] is not None]
            metrics[metric] = statistics.mean(values) if values else None
        metrics["critical_failures"] = sum(r["critical_failures"] for r in results)
        latencies = sorted(r["latency_ms"] for r in results)
        metrics["latency_p95_ms"] = latencies[max(0, __import__("math").ceil(len(latencies) * 0.95) - 1)]
        metrics["tokens"] = sum(r["tokens"] for r in results)
        metrics["completed_cases"] = len(results)
        passed = all(
            metrics[k] is not None and metrics[k] >= v
            for k, v in thresholds.items()
            if k != "critical_failures"
        )
        passed = (
            passed and metrics["critical_failures"] == 0 and all(r["status"] == "completed" for r in results)
        )
        with session_scope() as db:
            report = db.get(EvaluationReport, report_id)
            report.status, report.metrics, report.passed, report.finished_at = (
                "completed",
                metrics,
                passed,
                now(),
            )
            audit(
                db,
                user_id,
                "evaluation.completed",
                report_id,
                workspace_id,
                passed=passed,
                version_id=version_id,
            )
    except Exception:
        with session_scope() as db:
            report = db.get(EvaluationReport, report_id)
            report.status, report.passed, report.finished_at = "failed", False, now()
            report.error = "Evaluation could not complete. Check source access and model configuration, then start a new evaluation."
        raise
