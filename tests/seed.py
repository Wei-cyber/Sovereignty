import hashlib
from backend.config import settings
from backend.db import session_scope
from backend.models import Document, EvaluationSuite, Workflow, uid
from backend.schemas import Thresholds, template
from backend.knowledge import ingest_document


def seed_fixture(workspace_id, user_id):
    from tests.fixtures.catalog import ADVERSARIAL, CATALOG, UNANSWERABLE
    from backend.main import make_version

    directory = settings().data_dir / "documents" / workspace_id
    directory.mkdir(parents=True, exist_ok=True)
    cases = []
    for title, facts in CATALOG.items():
        document_id = uid()
        body = "# " + title + "\n\n" + "\n\n".join(answer for _, answer in facts)
        path = directory / (document_id + ".md")
        path.write_text(body, encoding="utf-8")
        with session_scope() as db:
            doc = Document(
                id=document_id,
                workspace_id=workspace_id,
                name=title + ".md",
                path=str(path),
                size=len(body.encode()),
                checksum=hashlib.sha256(body.encode()).hexdigest(),
                media_type="text/markdown",
                embedding_profile=settings().model_profile(),
            )
            db.add(doc)
        ingest_document(document_id)
        for question, answer in facts:
            cases.append(
                {
                    "id": f"case-{len(cases) + 1:03}",
                    "question": question,
                    "reference_answer": answer,
                    "source_document_ids": [document_id],
                    "expected_abstention": False,
                    "category": "answerable",
                }
            )
    for category, questions in [("unanswerable", UNANSWERABLE), ("adversarial", ADVERSARIAL)]:
        for question in questions:
            cases.append(
                {
                    "id": f"case-{len(cases) + 1:03}",
                    "question": question,
                    "reference_answer": "",
                    "source_document_ids": [],
                    "expected_abstention": True,
                    "category": category,
                }
            )
    with session_scope() as db:
        for kind, name, description in [
            ("rag", "Company knowledge", "Clear answers, grounded in your team's documents."),
            ("agent", "Research assistant", "An agent that searches and inspects evidence before answering."),
        ]:
            workflow = Workflow(workspace_id=workspace_id, name=name, description=description)
            db.add(workflow)
            db.flush()
            make_version(db, workflow, user_id, template(kind).model_dump())
        suite = EvaluationSuite(
            workspace_id=workspace_id,
            name="Northstar knowledge benchmark",
            cases=cases,
            thresholds=Thresholds().model_dump(),
            review_notes="60 generated examples using fictional data. Awaiting human review.",
        )
        db.add(suite)
    print(
        "Loaded 10 fictional documents, 2 workflow templates, and 60 evaluation examples awaiting human review."
    )
