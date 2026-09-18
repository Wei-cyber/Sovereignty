import hashlib
from datetime import datetime
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeConfig(StrictModel):
    prompt: str = Field(
        default="Answer the question using only the supplied evidence. Cite your sources.", max_length=8000
    )
    knowledge_document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default=5, ge=1, le=10)
    max_tool_calls: int = Field(default=4, ge=1, le=4)
    condition: Literal["has_sources", "has_answer", "abstained"] = "has_sources"
    tools: list[
        Literal[
            "knowledge_search",
            "read_source",
            "web_search",
            "web_read",
            "gmail_search",
            "gmail_read",
            "drive_search",
            "drive_read",
        ]
    ] = Field(default_factory=lambda: ["knowledge_search", "read_source"], max_length=8)


class WorkflowNode(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    type: Literal["input", "retrieval", "model", "condition", "agent", "answer"]
    label: str = Field(min_length=1, max_length=80)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})
    config: NodeConfig = Field(default_factory=NodeConfig)


class WorkflowEdge(StrictModel):
    id: str
    source: str
    target: str
    branch: Literal["true", "false"] | None = None


class WorkflowDefinition(StrictModel):
    schema_version: Literal[1] = 1
    nodes: list[WorkflowNode] = Field(min_length=2, max_length=30)
    edges: list[WorkflowEdge] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def validate_graph(self):
        nodes = {n.id: n for n in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Node IDs must be unique")
        if len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("Edge IDs must be unique")
        inputs = [n.id for n in self.nodes if n.type == "input"]
        if len(inputs) != 1 or not any(n.type == "answer" for n in self.nodes):
            raise ValueError("Use exactly one input and at least one answer node")
        outgoing = {n: [] for n in nodes}
        incoming = {n: [] for n in nodes}
        for e in self.edges:
            if e.source not in nodes or e.target not in nodes:
                raise ValueError("Every edge must connect existing nodes")
            outgoing[e.source].append(e)
            incoming[e.target].append(e)
        if incoming[inputs[0]]:
            raise ValueError("Input nodes cannot have incoming edges")
        for node in self.nodes:
            edges = outgoing[node.id]
            if node.type == "answer" and edges:
                raise ValueError("Answer nodes must be terminal")
            if node.type == "condition":
                if len(edges) != 2 or {e.branch for e in edges} != {"true", "false"}:
                    raise ValueError("Conditions require one true and one false branch")
            elif node.type != "answer" and (len(edges) != 1 or edges[0].branch is not None):
                raise ValueError("Each non-condition step requires exactly one unlabelled outgoing edge")
        visiting, visited = set(), set()

        def visit(node_id):
            if node_id in visiting:
                raise ValueError("Workflow cycles are not supported")
            if node_id in visited:
                return
            visiting.add(node_id)
            for edge in outgoing[node_id]:
                visit(edge.target)
            visiting.remove(node_id)
            visited.add(node_id)

        visit(inputs[0])
        if visited != set(nodes):
            raise ValueError("All steps must be reachable from the input")

        # Every branch has a defined context: model steps must follow retrieval or an agent.
        def check_context(node_id, evidence=False):
            node = nodes[node_id]
            if node.type == "model" and not evidence:
                raise ValueError("A model step must follow retrieval or an agent on every path")
            evidence = evidence or node.type in {"retrieval", "agent"}
            for edge in outgoing[node_id]:
                check_context(edge.target, evidence)

        check_context(inputs[0])
        return self


def fingerprint(*values) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def template(kind: str = "rag") -> WorkflowDefinition:
    types = ["input", "retrieval", "model", "answer"] if kind == "rag" else ["input", "agent", "answer"]
    labels = {
        "input": "User question",
        "retrieval": "Search knowledge",
        "model": "Compose grounded answer",
        "agent": "Research agent",
        "answer": "Answer with citations",
    }
    return WorkflowDefinition(
        nodes=[
            WorkflowNode(id=t, type=t, label=labels[t], position={"x": i * 270, "y": 120})
            for i, t in enumerate(types)
        ],
        edges=[
            WorkflowEdge(id=f"e{i}", source=a, target=b) for i, (a, b) in enumerate(zip(types, types[1:]))
        ],
    )


class LoginInput(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class AccountInput(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=256)

    @model_validator(mode="after")
    def valid_email(self):
        self.email = self.email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.email):
            raise ValueError("Enter a valid email address")
        return self


class WorkspaceInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class MemberInput(StrictModel):
    user_id: str
    role: Literal["admin", "member"]


class WorkflowInput(WorkspaceInput):
    template: Literal["rag", "agent"] = "rag"


class RunInput(StrictModel):
    question: str = Field(min_length=1, max_length=8000)
    version_id: str | None = None
    conversation_id: str | None = None
    parent_run_id: str | None = None
    preview: bool = False


class Citation(StrictModel):
    chunk_id: str
    quote: str = Field(max_length=1800)


class EmailDraft(StrictModel):
    to: list[str] = Field(default_factory=list, max_length=20)
    subject: str = Field(max_length=300)
    body: str = Field(max_length=20000)


class Answer(StrictModel):
    text: str
    citations: list[Citation]
    abstained: bool
    draft: EmailDraft | None = None


class Thresholds(StrictModel):
    retrieval_recall: float = Field(default=0.85, ge=0, le=1)
    answer_correctness: float = Field(default=0.85, ge=0, le=1)
    evidence_support: float = Field(default=0.9, ge=0, le=1)
    abstention: float = Field(default=0.9, ge=0, le=1)
    citation_validity: float = Field(default=1, ge=0, le=1)
    critical_failures: Literal[0] = 0


class ToolFixture(StrictModel):
    tool: Literal["web_search", "web_read", "gmail_search", "gmail_read", "drive_search", "drive_read"]
    arguments: dict[str, str]
    sources: list["SourceContract"] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validated_arguments(self):
        from backend.agent_tools import schema

        schema(self.tool).model_validate(self.arguments)
        if any(s.connection_id or s.kind == "knowledge" for s in self.sources):
            raise ValueError("Fixtures must contain reviewed external examples without account identifiers")
        return self


class EvaluationCase(StrictModel):
    fixture_version: str = Field(default="", max_length=80)
    tool_fixtures: list[ToolFixture] = Field(default_factory=list, max_length=4)
    expected_tools: list[str] = Field(default_factory=list, max_length=4)
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=8000)
    reference_answer: str = Field(max_length=8000)
    source_document_ids: list[str] = Field(default_factory=list)
    expected_abstention: bool = False
    category: Literal["answerable", "unanswerable", "adversarial"] = "answerable"

    @model_validator(mode="after")
    def references(self):
        if self.tool_fixtures and not self.fixture_version:
            raise ValueError("External tool fixtures require an explicit version")
        if not self.expected_abstention and (
            not self.source_document_ids or not self.reference_answer.strip()
        ):
            raise ValueError("Answerable examples need a reference answer and supporting documents")
        return self


class EvaluationSuiteInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=500)
    thresholds: Thresholds = Field(default_factory=Thresholds)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Evaluation case IDs must be unique")
        return self


class EvaluationInput(StrictModel):
    version_id: str
    suite_id: str


class PublishInput(StrictModel):
    version_id: str
    report_id: str


class ReviewInput(StrictModel):
    notes: str = Field(min_length=10, max_length=4000)


class FeedbackInput(StrictModel):
    rating: Literal[-1, 1]
    comment: str = Field(default="", max_length=4000)


class FeedbackPromotion(StrictModel):
    suite_id: str
    reference_answer: str = Field(max_length=8000)
    source_document_ids: list[str] = Field(default_factory=list)
    expected_abstention: bool = False


class Grade(StrictModel):
    correctness: float = Field(ge=0, le=1)
    evidence_support: float = Field(ge=0, le=1)
    rationale: str


class SourceContract(BaseModel):
    thread_id: str = ""
    chunk_id: str
    document_id: str
    document_name: str
    document_version: int
    location: str
    text: str
    kind: str = "knowledge"
    url: str = ""
    remote_id: str = ""
    connection_id: str = ""
    connection_generation: int = 0
    fetched_at: str = ""


class WorkflowVersionContract(BaseModel):
    id: str
    workflow_id: str
    number: int
    definition: WorkflowDefinition
    corpus_revision: int
    model_profile: dict[str, str | int]
    fingerprint: str
    created_at: datetime
    created_by: str


class WorkflowContract(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str
    paused: bool
    published_version_id: str | None
    versions: list[WorkflowVersionContract]
    created_at: datetime
    owner_id: str | None = None
    kind: str = "workflow"
    starters: list[str] = Field(default_factory=list)
    submitted_from: str | None = None


class RunContract(BaseModel):
    id: str
    workspace_id: str
    version_id: str
    status: str
    question: str
    version_number: int
    workflow_name: str
    user_id: str
    conversation_id: str | None
    parent_run_id: str | None
    kind: str
    answer: Answer | None
    sources: list[SourceContract]
    model_profile: dict[str, str | int]
    content_revoked: bool
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deadline_at: datetime | None
    cancel_requested: bool
    tokens: int
    latency_ms: float | None


class StepTraceContract(BaseModel):
    id: str
    run_id: str
    node_id: str
    node_type: str
    status: str
    inputs: dict
    outputs: dict
    tool_calls: list[dict]
    latency_ms: float
    tokens: int
    error: str | None
    created_at: datetime
    content_revoked: bool = False


class EvaluationSuiteContract(BaseModel):
    id: str
    workspace_id: str
    family_id: str
    name: str
    version: int
    cases: list[EvaluationCase]
    thresholds: dict[str, float]
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_notes: str
    created_at: datetime


class EvaluationResultContract(BaseModel):
    evidence_mode: str = "live"
    fixture_version: str = ""
    case_id: str
    run_id: str
    question: str
    retrieval_recall: float | None
    answer_correctness: float | None
    evidence_support: float | None
    abstention: float | None
    citation_validity: float
    critical_failures: int
    latency_ms: float
    tokens: int
    rationale: str
    status: str


class EvaluationReportContract(BaseModel):
    id: str
    workspace_id: str
    version_id: str
    suite_id: str
    user_id: str
    fingerprint: str
    status: str
    passed: bool
    metrics: dict[str, float | bool | None]
    results: list[EvaluationResultContract]
    error: str | None
    created_at: datetime
    finished_at: datetime | None
