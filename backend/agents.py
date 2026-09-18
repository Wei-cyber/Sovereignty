"""Personal agents share the workflow engine, never the creator's credentials."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import User, Workflow, WorkflowVersion
from backend.schemas import StrictModel, WorkflowContract, NodeConfig, template
from backend.security import current_user, require_member, audit

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/agents", tags=["Agents"])


class AgentInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    knowledge_document_ids: list[str] = Field(default_factory=list, max_length=100)
    instructions: str = Field(min_length=1, max_length=8000)
    starters: list[str] = Field(default_factory=list, max_length=6)
    tools: NodeConfig.model_fields["tools"].annotation = Field(
        default_factory=lambda: ["knowledge_search", "read_source"]
    )


def owned(db, user, workspace, item_id):
    require_member(db, user.id, workspace)
    item = db.get(Workflow, item_id)
    if (
        not item
        or item.workspace_id != workspace
        or item.owner_id != user.id
        or item.archived
        or item.kind != "agent"
    ):
        raise HTTPException(404, "Personal agent not found")
    return item


def view(db, item):
    from backend.main import record

    return {
        **record(item),
        "versions": [
            record(v)
            for v in db.scalars(
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == item.id)
                .order_by(WorkflowVersion.number.desc())
            )
        ],
    }


def definition(body):
    graph = template("agent")
    config = NodeConfig(
        prompt=body.instructions, tools=body.tools, knowledge_document_ids=body.knowledge_document_ids
    )
    next(n for n in graph.nodes if n.type == "agent").config = config
    return graph.model_dump()


@router.get("", response_model=list[WorkflowContract])
def listing(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user.id, workspace_id)
    return [
        view(db, item)
        for item in db.scalars(
            select(Workflow)
            .where(
                Workflow.workspace_id == workspace_id,
                Workflow.kind == "agent",
                Workflow.owner_id == user.id,
                Workflow.archived.is_(False),
            )
            .order_by(Workflow.created_at.desc())
        )
    ]


@router.post("", response_model=WorkflowContract, status_code=201)
def create(
    workspace_id: str, body: AgentInput, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    from backend.main import make_version

    require_member(db, user.id, workspace_id)
    graph = definition(body)
    item = Workflow(
        workspace_id=workspace_id,
        owner_id=user.id,
        kind="agent",
        name=body.name,
        description=body.description,
        starters=body.starters,
    )
    db.add(item)
    db.flush()
    make_version(db, item, user.id, graph)
    audit(db, user.id, "agent.created", item.id, workspace_id)
    return view(db, item)


@router.post("/{agent_id}/versions", response_model=WorkflowContract)
def revise(
    workspace_id: str,
    agent_id: str,
    body: AgentInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from backend.main import make_version

    item = owned(db, user, workspace_id, agent_id)
    graph = definition(body)
    item.name, item.description, item.starters = body.name, body.description, body.starters
    make_version(db, item, user.id, graph)
    audit(db, user.id, "agent.revised", item.id, workspace_id)
    return view(db, item)


@router.post("/{agent_id}/duplicate", response_model=WorkflowContract, status_code=201)
def duplicate(
    workspace_id: str, agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    from backend.main import make_version

    original = owned(db, user, workspace_id, agent_id)
    item = Workflow(
        workspace_id=workspace_id,
        owner_id=user.id,
        kind="agent",
        name=(original.name + " copy")[:120],
        description=original.description,
        starters=original.starters,
    )
    db.add(item)
    db.flush()
    latest = db.scalar(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == original.id)
        .order_by(WorkflowVersion.number.desc())
    )
    make_version(db, item, user.id, latest.definition)
    audit(db, user.id, "agent.duplicated", item.id, workspace_id)
    return view(db, item)


@router.post("/{agent_id}/submit", response_model=WorkflowContract, status_code=201)
def submit(
    workspace_id: str, agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    from backend.main import make_version

    original = owned(db, user, workspace_id, agent_id)
    latest = db.scalar(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == original.id)
        .order_by(WorkflowVersion.number.desc())
    )
    existing = db.scalar(
        select(Workflow).where(Workflow.workspace_id == workspace_id, Workflow.submitted_from == latest.id)
    )
    if existing:
        return view(db, existing)
    item = Workflow(
        workspace_id=workspace_id,
        kind="agent",
        name=original.name,
        description=original.description,
        starters=original.starters,
        submitted_from=latest.id,
    )
    db.add(item)
    db.flush()
    make_version(db, item, user.id, latest.definition)
    audit(db, user.id, "agent.submitted", item.id, workspace_id)
    return view(db, item)


@router.delete("/{agent_id}")
def remove(
    workspace_id: str, agent_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    item = owned(db, user, workspace_id, agent_id)
    item.archived = True
    audit(db, user.id, "agent.archived", item.id, workspace_id)
    return {"archived": True}
