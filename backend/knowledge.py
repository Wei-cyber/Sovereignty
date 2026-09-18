import json
import math
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import delete, or_, select, text, update

from backend.db import session_scope
from backend.models import Chunk, Document, Workspace
from backend.providers import terms
from backend.embeddings import embedding_identity, embedding_provider, validate_vectors
from backend.security import require_member


def split_sections(path, name, profile=None):
    if name.lower().endswith(".pdf"):
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported. Upload an unencrypted copy.")
        if len(reader.pages) > 1000:
            raise ValueError("PDFs are limited to 1,000 pages")
        sections = [(f"Page {i + 1}", page.extract_text() or "") for i, page in enumerate(reader.pages)]
    else:
        body = Path(path).read_text(encoding="utf-8-sig")
        sections, buffer, heading = [], [], "Document"
        for line in body.splitlines():
            if line.startswith("#") and buffer:
                sections.append((heading, "\n".join(buffer)))
                buffer = []
            if line.startswith("#"):
                heading = line.lstrip("# ")[:150] or "Document"
            buffer.append(line)
        if buffer:
            sections.append((heading, "\n".join(buffer)))
    if not any(body.strip() for _, body in sections):
        raise ValueError("No extractable text found. Scanned documents require OCR before upload.")
    if sum(len(body) for _, body in sections) > 3_000_000:
        raise ValueError("Extracted text exceeds the 3 million character limit")
    for location, body in sections:
        body = body.replace("\x00", "").strip()
        if profile and embedding_identity(profile)["embedding_provider"] == "local":
            for chunk in embedding_provider(profile).split(body):
                yield location, chunk
            continue
        start = 0
        while start < len(body):
            end = min(start + 1800, len(body))
            if end < len(body):
                boundary = body.rfind(" ", start + 1200, end)
                if boundary > start:
                    end = boundary
            chunk = body[start:end].strip()
            if chunk:
                yield location, chunk
            if end == len(body):
                break
            start = max(start + 1, end - 180)


def ingest_document(document_id):
    with session_scope() as db:
        doc = db.get(Document, document_id)
        if not doc or doc.deleted_at or doc.status == "ready":
            return
        doc.status = "processing"
        doc.error = None
        path, name, profile = doc.path, doc.name, doc.embedding_profile
    try:
        sections = list(split_sections(path, name, profile))
        vectors = []
        model = embedding_provider(profile)
        for offset in range(0, len(sections), 32):
            vectors.extend(model.embed([body for _, body in sections[offset : offset + 32]]))
        if len(vectors) != len(sections):
            raise ValueError("Embedding provider returned an incomplete result")
        validate_vectors(vectors, len(sections), profile["embedding_dimensions"])
        with session_scope() as db:
            # A recovered duplicate must not replace chunks already committed by another worker.
            doc = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
            if doc.deleted_at or doc.status == "ready":
                return
            # Serializes revision assignment and replacement ingestion across workers.
            revision = db.execute(
                update(Workspace)
                .where(Workspace.id == doc.workspace_id)
                .values(revision=Workspace.revision + 1)
                .returning(Workspace.revision)
            ).scalar_one()
            db.execute(
                update(Document)
                .where(
                    Document.family_id == doc.family_id,
                    Document.workspace_id == doc.workspace_id,
                    Document.status == "ready",
                    Document.superseded_revision.is_(None),
                )
                .values(superseded_revision=revision)
            )
            db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            for ordinal, ((location, body), vector) in enumerate(zip(sections, vectors)):
                db.add(
                    Chunk(
                        document_id=doc.id,
                        workspace_id=doc.workspace_id,
                        ordinal=ordinal,
                        location=location,
                        text=body,
                        embedding=vector,
                    )
                )
            doc.status, doc.introduced_revision, doc.chunk_count = "ready", revision, len(sections)
    except Exception:
        with session_scope() as db:
            doc = db.get(Document, document_id)
            if doc and not doc.deleted_at and doc.status != "ready":
                doc.status = "failed"
                doc.error = "Ingestion failed. Check file text, encryption, size, and model configuration; then retry."
        raise


def visible_document_filters(workspace_id, revision):
    return [
        Document.workspace_id == workspace_id,
        Document.deleted_at.is_(None),
        Document.status == "ready",
        Document.introduced_revision <= revision,
        or_(Document.superseded_revision.is_(None), Document.superseded_revision > revision),
    ]


def source_dict(chunk, doc):
    return {
        "chunk_id": chunk.id,
        "document_id": doc.id,
        "document_name": doc.name,
        "document_version": doc.version,
        "location": chunk.location,
        "text": chunk.text,
    }


def retrieve(
    db, user_id, workspace_id, revision, query, profile, top_k=5, query_vector=None, document_ids=None
):
    require_member(db, user_id, workspace_id)
    docs = db.scalars(select(Document).where(*visible_document_filters(workspace_id, revision))).all()
    if not docs or not query.strip():
        return []
    if document_ids:
        docs = [d for d in docs if d.id in document_ids]
    doc_map = {d.id: d for d in docs}
    for doc in docs:
        if embedding_identity(doc.embedding_profile) != embedding_identity(profile):
            raise ValueError(
                "Knowledge embeddings do not match this workflow. Reingest documents with the configured model."
            )
    vector = (
        query_vector
        if query_vector is not None
        else embedding_provider(profile).embed([query], query=True)[0]
    )
    dimensions = profile["embedding_dimensions"]
    if dimensions not in {384, 1536}:
        raise ValueError("Unsupported embedding dimensions")
    validate_vectors([vector], 1, dimensions)
    if db.bind.dialect.name == "postgresql":
        # Both candidate sets filter the snapshot and workspace before ranking.
        base = """FROM chunks c JOIN documents d ON d.id=c.document_id
        WHERE c.workspace_id=:workspace AND d.workspace_id=:workspace AND d.deleted_at IS NULL
        AND d.status='ready' AND d.introduced_revision<=:revision
        AND (d.superseded_revision IS NULL OR d.superseded_revision>:revision)"""
        params = {
            "workspace": workspace_id,
            "revision": revision,
            "vector": json.dumps(vector),
            "query": query,
        }
        if document_ids:
            base += " AND d.id IN (SELECT jsonb_array_elements_text(CAST(:document_ids AS jsonb)))"
            params["document_ids"] = json.dumps(list(doc_map))
        # Iterative scans preserve recall when HNSW results are filtered by workspace/snapshot.
        db.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
        semantic = list(
            db.execute(
                text(
                    "SELECT c.id "
                    + base
                    + f" AND vector_dims(c.embedding)={dimensions}"
                    + f" ORDER BY c.embedding::vector({dimensions}) <=> CAST(:vector AS vector({dimensions})) LIMIT 30"
                ),
                params,
            ).scalars()
        )
        lexical = list(
            db.execute(
                text(
                    "SELECT c.id "
                    + base
                    + " AND to_tsvector('english', c.text) @@ plainto_tsquery('english', :query) "
                    "ORDER BY ts_rank_cd(to_tsvector('english', c.text), plainto_tsquery('english', :query)) DESC LIMIT 30"
                ),
                params,
            ).scalars()
        )
    else:
        chunks = db.scalars(
            select(Chunk).where(Chunk.workspace_id == workspace_id, Chunk.document_id.in_(doc_map))
        ).all()
        qterms = set(terms(query))
        semantic_scores = [(c.id, sum(a * b for a, b in zip(c.embedding, vector))) for c in chunks]
        lexical_scores = [
            (c.id, len(qterms & set(terms(c.text))) / math.sqrt(max(1, len(terms(c.text))))) for c in chunks
        ]
        semantic = [
            cid
            for cid, score in sorted(semantic_scores, key=lambda p: p[1], reverse=True)[:30]
            if score > 0.05
        ]
        lexical = [
            cid for cid, score in sorted(lexical_scores, key=lambda p: p[1], reverse=True)[:30] if score > 0
        ]
    scores = {}
    for ranking in [semantic, lexical]:
        for rank, cid in enumerate(ranking, 1):
            scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
    chosen = sorted(scores, key=lambda cid: (-scores[cid], cid))[:top_k]
    by_id = {
        c.id: c
        for c in db.scalars(
            select(Chunk).where(Chunk.workspace_id == workspace_id, Chunk.id.in_(chosen))
        ).all()
    }
    return [source_dict(by_id[cid], doc_map[by_id[cid].document_id]) for cid in chosen if cid in by_id]


def read_source(db, user_id, workspace_id, revision, chunk_id):
    require_member(db, user_id, workspace_id)
    row = db.execute(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.id == chunk_id,
            Chunk.workspace_id == workspace_id,
            *visible_document_filters(workspace_id, revision),
        )
    ).first()
    if not row:
        raise PermissionError("Source is unavailable or outside the permitted knowledge snapshot")
    return source_dict(*row)


def revoked_snapshot(db, workspace_id, revision):
    return bool(
        db.scalar(
            select(Document.id)
            .where(
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_not(None),
                Document.introduced_revision <= revision,
                or_(Document.superseded_revision.is_(None), Document.superseded_revision > revision),
            )
            .limit(1)
        )
    )
