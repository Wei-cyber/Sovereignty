import time
from contextlib import contextmanager
from datetime import timedelta
from typing import TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from sqlalchemy import select

from backend.config import settings
from backend.db import now, session_scope
from backend.knowledge import read_source, retrieve, revoked_snapshot
from backend.models import Conversation, Run, RunEvent, StepTrace, WorkflowVersion, ToolReceipt, Workflow
from backend.providers import ABSTENTION, MODEL_DEADLINE, provider
from backend.schemas import Answer, WorkflowDefinition
from backend.security import audit, require_member

TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


class RunStopped(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class GraphState(TypedDict, total=False):
    question: str
    sources: list[dict]
    answer: dict
    history: list[dict]
    branch: str


@contextmanager
def checkpointer():
    cfg = settings()
    if cfg.database_url.startswith("postgresql"):
        url = cfg.database_url.replace("postgresql+psycopg://", "postgresql://")
        with PostgresSaver.from_conn_string(url) as saver:
            yield saver
    else:
        with SqliteSaver.from_conn_string(str(cfg.data_dir / "checkpoints.db")) as saver:
            yield saver


def initialize_checkpoints():
    with checkpointer() as saver:
        saver.setup()


def check_run(run_id):
    with session_scope() as db:
        run = db.get(Run, run_id)
        require_member(db, run.user_id, run.workspace_id, admin=run.kind in {"preview", "evaluation"})
        if run.cancel_requested:
            raise RunStopped("cancelled", "Run cancelled")
        if run.deadline_at and now() >= run.deadline_at:
            raise RunStopped("timed_out", "Run exceeded its time budget")
        if run.tokens >= settings().run_token_budget:
            raise RunStopped("failed", "Run exceeded its token budget")
        version = db.get(WorkflowVersion, run.version_id)
        if version.model_profile.get("provider") != "openai":
            raise RunStopped("failed", "Historical synthetic inference has been retired")
        workflow = db.get(Workflow, version.workflow_id)
        if workflow.archived or (workflow.owner_id and workflow.owner_id != run.user_id):
            raise RunStopped("failed", "Agent access was revoked")
        if revoked_snapshot(db, run.workspace_id, version.corpus_revision):
            raise RunStopped("failed", "A source in this knowledge snapshot was revoked")
        return run, version


def guarded_call(run_id, operation):
    for attempt in range(3):
        run, _ = check_run(run_id)
        try:
            deadline_token = MODEL_DEADLINE.set(run.deadline_at)
            try:
                result = operation()
            finally:
                MODEL_DEADLINE.reset(deadline_token)
            check_run(run_id)
            if hasattr(result, "tokens"):
                with session_scope() as db:
                    run = db.get(Run, run_id)
                    run.tokens += result.tokens
                check_run(run_id)
            return result
        except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError):
            if attempt == 2:
                raise
            time.sleep(0.5 * 2**attempt)


def validate_answer(answer, sources):
    parsed = Answer.model_validate(answer).model_dump()
    available = {s["chunk_id"]: s for s in sources}
    if parsed["abstained"]:
        return {"text": parsed["text"] or ABSTENTION, "citations": [], "abstained": True}
    if parsed.get("draft"):
        if not parsed["citations"]:
            parsed["text"] = "Draft ready for your review. It has not been saved or sent."
            return parsed
    if not parsed["citations"]:
        return {"text": ABSTENTION, "citations": [], "abstained": True}
    for citation in parsed["citations"]:
        source = available.get(citation["chunk_id"])
        quote = " ".join(citation["quote"].split())
        if not source or not quote or quote not in " ".join(source["text"].split()):
            raise ValueError("Model supplied an invalid source citation")
    return parsed


def execute_node(run_id, node, state):
    run, version = check_run(run_id)
    from backend.google_services import source_available

    if not run.tool_fixtures:
        with session_scope() as db:
            if any(
                not source_available(db, run.user_id, run.workspace_id, s) for s in state.get("sources", [])
            ):
                raise PermissionError("Source access was revoked")
    with session_scope() as db:
        trace = db.scalar(select(StepTrace).where(StepTrace.run_id == run_id, StepTrace.node_id == node.id))
        if trace and trace.status == "completed":
            from backend.google_services import source_available

            if not run.tool_fixtures and any(
                not source_available(db, run.user_id, run.workspace_id, s)
                for s in trace.outputs.get("sources", [])
            ):
                raise PermissionError("Saved source access was revoked")
            return trace.outputs
        if not trace:
            trace = StepTrace(run_id=run_id, node_id=node.id, node_type=node.type, status="running")
            db.add(trace)
        trace.status = "running"
        trace.inputs = {
            "question": state["question"],
            "source_ids": [s["chunk_id"] for s in state.get("sources", [])],
        }
        db.add(RunEvent(run_id=run_id, event="step_started", data={"node_id": node.id, "label": node.label}))
        trace_id = trace.id
        db.flush()
        trace_id = trace.id
    start, tool_calls, used_tokens = time.perf_counter(), [], 0
    model = provider(version.model_profile)
    sources = list(state.get("sources", []))

    def search(query):
        with session_scope() as db:
            return retrieve(
                db,
                run.user_id,
                run.workspace_id,
                version.corpus_revision,
                query,
                version.model_profile,
                node.config.top_k,
                document_ids=node.config.knowledge_document_ids,
            )

    try:
        output = {}
        if node.type == "input":
            output = {"question": state["question"]}
        elif node.type == "retrieval":
            output = {"sources": guarded_call(run_id, lambda: search(state["question"]))}
        elif node.type == "model":
            if not sources:
                output = {"answer": {"text": ABSTENTION, "citations": [], "abstained": True}}
            else:
                result = guarded_call(
                    run_id,
                    lambda: model.answer(
                        state["question"], sources, node.config.prompt, state.get("history", [])
                    ),
                )
                used_tokens += result.tokens
                output = {"answer": validate_answer(result.value, sources)}
        elif node.type == "agent":
            from backend.agent_tools import permitted, external_call, schema
            from backend.google_services import source_available

            with session_scope() as db:
                previous = list(
                    db.scalars(
                        select(ToolReceipt)
                        .where(ToolReceipt.run_id == run_id, ToolReceipt.node_id == node.id)
                        .order_by(ToolReceipt.ordinal)
                    )
                )
            for receipt in previous:
                if receipt.status == "completed":
                    if receipt.tool not in permitted(run.user_id, run.workspace_id, node.config.tools):
                        raise PermissionError("Saved tool access was revoked")
                    with session_scope() as db:
                        if not run.tool_fixtures and any(
                            not source_available(db, run.user_id, run.workspace_id, s) for s in receipt.result
                        ):
                            raise PermissionError("Saved tool evidence is no longer accessible")
                    sources = list({s["chunk_id"]: s for s in sources + receipt.result}.values())[:20]
                    tool_calls.append(
                        {
                            "tool": receipt.tool,
                            "arguments": receipt.arguments,
                            "source_ids": [s["chunk_id"] for s in receipt.result],
                        }
                    )
            with session_scope() as db:
                other_calls = len(
                    list(
                        db.scalars(
                            select(ToolReceipt.id).where(
                                ToolReceipt.run_id == run_id, ToolReceipt.node_id != node.id
                            )
                        )
                    )
                )
            for ordinal in range(len(tool_calls), min(node.config.max_tool_calls, max(0, 4 - other_calls))):
                available = permitted(run.user_id, run.workspace_id, node.config.tools)
                if not available:
                    break
                pending = next(
                    (r for r in previous if r.ordinal == ordinal and r.status != "completed"), None
                )
                if pending:
                    name, args = pending.tool, pending.arguments
                else:
                    result = guarded_call(
                        run_id,
                        lambda: model.decide(
                            state["question"],
                            sources,
                            tool_calls,
                            node.config.max_tool_calls - len(tool_calls),
                            node.config.prompt,
                            available,
                        ),
                    )
                    used_tokens += result.tokens
                    name, args = result.value.get("tool"), result.value.get("arguments", {})
                if name == "finish":
                    break
                if name not in available:
                    raise PermissionError("Agent attempted a disabled tool")
                args = schema(name).model_validate(args).model_dump()
                with session_scope() as db:
                    receipt = db.scalar(
                        select(ToolReceipt).where(
                            ToolReceipt.run_id == run_id,
                            ToolReceipt.node_id == node.id,
                            ToolReceipt.ordinal == ordinal,
                        )
                    )
                    if not receipt:
                        receipt = ToolReceipt(
                            run_id=run_id, node_id=node.id, ordinal=ordinal, tool=name, arguments=args
                        )
                        db.add(receipt)
                        db.flush()
                    receipt_id = receipt.id
                if name == "knowledge_search":
                    found = guarded_call(run_id, lambda: search(args["query"]))
                elif name == "read_source":
                    if args["chunk_id"] not in {
                        s["chunk_id"] for s in sources if s.get("kind", "knowledge") == "knowledge"
                    }:
                        raise PermissionError("Read source outside retrieved context")
                    with session_scope() as db:
                        found = [
                            read_source(
                                db, run.user_id, run.workspace_id, version.corpus_revision, args["chunk_id"]
                            )
                        ]
                elif run.kind == "evaluation":
                    match = next(
                        (f for f in run.tool_fixtures if f["tool"] == name and f["arguments"] == args), None
                    )
                    if not match:
                        raise PermissionError("Evaluation called an unexpected external tool")
                    found = match["sources"]
                else:
                    found = guarded_call(run_id, lambda: external_call(run, name, args, sources))
                with session_scope() as db:
                    receipt = db.get(ToolReceipt, receipt_id)
                    receipt.result, receipt.status = found, "completed"
                sources = list({s["chunk_id"]: s for s in sources + found}.values())[:20]
                tool_calls.append(
                    {"tool": name, "arguments": args, "source_ids": [s["chunk_id"] for s in found]}
                )
            result = guarded_call(
                run_id,
                lambda: model.answer(
                    state["question"], sources, node.config.prompt, state.get("history", [])
                ),
            )
            used_tokens += result.tokens
            output = {"sources": sources, "answer": validate_answer(result.value, sources)}
        elif node.type == "condition":
            predicates = {
                "has_sources": bool(sources),
                "has_answer": bool(state.get("answer")),
                "abstained": bool(state.get("answer", {}).get("abstained")),
            }
            output = {"branch": "true" if predicates[node.config.condition] else "false"}
        elif node.type == "answer":
            output = {
                "answer": validate_answer(
                    state.get("answer", {"text": ABSTENTION, "citations": [], "abstained": True}), sources
                )
            }
        check_run(run_id)
        with session_scope() as db:
            trace = db.get(StepTrace, trace_id)
            trace.status, trace.outputs, trace.tool_calls = "completed", output, tool_calls
            trace.latency_ms, trace.tokens = (time.perf_counter() - start) * 1000, used_tokens
            db.add(
                RunEvent(
                    run_id=run_id,
                    event="step_completed",
                    data={"node_id": node.id, "latency_ms": trace.latency_ms},
                )
            )
        return output
    except Exception as exc:
        with session_scope() as db:
            trace = db.get(StepTrace, trace_id)
            trace.status, trace.tool_calls = "failed", tool_calls
            trace.error = (
                "Tool permission denied"
                if isinstance(exc, PermissionError)
                else "Step failed; verify sources, configuration, and provider availability."
            )
            trace.latency_ms = (time.perf_counter() - start) * 1000
        raise


def compile_workflow(definition, run_id, saver):
    definition = WorkflowDefinition.model_validate(definition)
    builder = StateGraph(GraphState)
    for node in definition.nodes:
        builder.add_node(node.id, lambda state, n=node: execute_node(run_id, n, state))
    builder.set_entry_point(next(n.id for n in definition.nodes if n.type == "input"))
    for node in definition.nodes:
        outgoing = [e for e in definition.edges if e.source == node.id]
        if node.type == "condition":
            builder.add_conditional_edges(
                node.id, lambda state: state["branch"], {e.branch: e.target for e in outgoing}
            )
        elif node.type == "answer":
            builder.add_edge(node.id, END)
        else:
            builder.add_edge(node.id, outgoing[0].target)
    return builder.compile(checkpointer=saver)


def execute_run(run_id):
    with session_scope() as db:
        run = db.get(Run, run_id)
        if not run or run.status in TERMINAL:
            return
        run.started_at = run.started_at or now()
        run.deadline_at = run.deadline_at or (
            run.started_at + timedelta(seconds=settings().run_deadline_seconds)
        )
        run.status = "running"
        version = db.get(WorkflowVersion, run.version_id)
        definition, question = version.definition, run.question
        history = []
        if run.conversation_id:
            conversation = db.get(Conversation, run.conversation_id)
            if conversation.user_id != run.user_id:
                raise PermissionError("Conversation ownership mismatch")
            previous = db.scalars(
                select(Run)
                .where(
                    Run.conversation_id == run.conversation_id, Run.status == "completed", Run.id != run.id
                )
                .order_by(Run.created_at.desc())
                .limit(3)
            ).all()
            for item in reversed(previous):
                prior_version = db.get(WorkflowVersion, item.version_id)
                from backend.google_services import source_available

                if not revoked_snapshot(db, run.workspace_id, prior_version.corpus_revision) and all(
                    source_available(db, run.user_id, run.workspace_id, s) for s in item.sources
                ):
                    history.extend(
                        [
                            {"role": "user", "text": item.question},
                            {"role": "assistant", "text": (item.answer or {}).get("text", "")},
                        ]
                    )
        db.add(RunEvent(run_id=run_id, event="status", data={"status": "running"}))
    try:
        check_run(run_id)
        with checkpointer() as saver:
            graph = compile_workflow(definition, run_id, saver)
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 64}
            snapshot = graph.get_state(config)
            initial = None if snapshot.values else {"question": question, "sources": [], "history": history}
            if snapshot.values and not snapshot.next:
                result = snapshot.values
            else:
                result = graph.invoke(initial, config=config, durability="sync")
        check_run(run_id)
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.answer, run.sources, run.status = result["answer"], result.get("sources", []), "completed"
    except Exception as exc:
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.status = exc.status if isinstance(exc, RunStopped) else "failed"
            run.error = (
                str(exc)
                if isinstance(exc, RunStopped)
                else (
                    "Tool permission denied"
                    if isinstance(exc, PermissionError)
                    else "Run failed. Inspect the failed step and check source access or provider configuration."
                )
            )
    finally:
        with session_scope() as db:
            run = db.get(Run, run_id)
            run.finished_at = now()
            run.latency_ms = (run.finished_at - run.started_at).total_seconds() * 1000
            db.add(RunEvent(run_id=run_id, event="status", data={"status": run.status}))
            audit(
                db,
                run.user_id,
                "run." + run.status,
                run.id,
                run.workspace_id,
                version_id=run.version_id,
                tokens=run.tokens,
            )
