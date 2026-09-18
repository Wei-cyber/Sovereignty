import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.config import Settings, settings
from backend.db import session_scope
from backend.embedding_spec import BGE_MODEL
from backend.embeddings import embedding_identity, embedding_provider, local_bge
from backend.knowledge import ingest_document, read_source, retrieve, split_sections
from backend.models import Chunk, Document, User, Workspace
from backend.providers import OpenAIProvider
from backend.schemas import Answer
from scripts.reindex_knowledge import reindex


def test_retiring_demo_login_requires_a_replacement_admin(environment, monkeypatch):
    monkeypatch.setattr(settings(), "retired_demo_password", "test-only-retired-password")
    from backend.manage import retire_demo_login
    from backend.security import hasher

    with session_scope() as db:
        db.get(User, environment["admin"]).password_hash = hasher.hash("test-only-retired-password")
    with pytest.raises(SystemExit, match="different BOOTSTRAP_EMAIL"):
        retire_demo_login()
    with session_scope() as db:
        assert db.get(User, environment["admin"]).active
        replacement = db.get(User, environment["member"])
        replacement.is_system_admin = True
        email = replacement.email
    monkeypatch.setattr(settings(), "bootstrap_email", email)
    retire_demo_login()
    with session_scope() as db:
        assert not db.get(User, environment["admin"]).active
        assert db.get(User, environment["member"]).active


def hybrid_settings(**kwargs):
    return Settings(
        _env_file=None,
        model_provider="openai",
        embedding_provider="local",
        embedding_model=BGE_MODEL,
        embedding_dimensions=384,
        **kwargs,
    )


def test_provider_and_reasoning_identity():
    cfg = hybrid_settings()
    before = cfg.model_profile()
    assert before["chat_model"] == "gpt-5.6-luna"
    assert before["embedding_dimensions"] == 384
    cfg.chat_model = "another-generation-model"
    assert embedding_identity(before) == embedding_identity(cfg.model_profile())
    cfg.agent_reasoning_effort = "high"
    assert before != cfg.model_profile()
    grader = cfg.grader_identity()
    cfg.grader_reasoning_effort = "high"
    assert grader != cfg.grader_identity()
    cfg.embedding_dimensions = 1536
    with pytest.raises(ValueError, match="384"):
        cfg.validate_deployment()


def test_legacy_embedding_identity():
    legacy = {"provider": "demo", "embedding_model": "demo-hash-v1", "embedding_dimensions": 1536}
    assert embedding_identity(legacy)["embedding_provider"] == "demo"
    with pytest.raises(ValueError, match="removed"):
        Settings(_env_file=None, model_provider="demo").validate_deployment()
    with pytest.raises(ValueError):
        embedding_provider(legacy)


def test_gpt_requests_have_role_specific_reasoning_and_reject_partial(monkeypatch):
    captured = []
    response = SimpleNamespace(
        status="completed",
        usage=SimpleNamespace(total_tokens=15),
        output=[],
        output_parsed=Answer(text="No evidence", citations=[], abstained=True),
    )

    def request(**kwargs):
        captured.append(kwargs)
        return response

    client = SimpleNamespace(responses=SimpleNamespace(parse=request, create=request))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr("backend.providers.OpenAI", lambda **kwargs: client)
    model = OpenAIProvider(hybrid_settings().model_profile())
    model.answer("q", [], "p", [])
    model.decide("q", [], [], 4)
    model.grade("q", "a", {}, [])
    assert [c["reasoning"]["effort"] for c in captured] == ["low", "medium", "medium"]
    assert [c["model"] for c in captured] == ["gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-terra"]
    assert all(c["store"] is False and c["max_output_tokens"] == 4096 for c in captured)
    assert captured[1]["parallel_tool_calls"] is False
    response.status = "incomplete"
    with pytest.raises(ValueError, match="complete"):
        model.decide("q", [], [], 4)
    with pytest.raises(ValueError, match="complete"):
        model.answer("q", [], "p", [])


MODEL_DIR = Path("data/models/bge-small-en-v1.5").resolve()
local_model = pytest.mark.skipif(
    not (MODEL_DIR / "manifest.json").exists(), reason="Prepare the pinned BGE artifact first"
)


@local_model
def test_real_local_embeddings_offline_and_token_safe(monkeypatch, tmp_path):
    def network_forbidden(*args, **kwargs):
        raise AssertionError("Local embeddings attempted a network call")

    monkeypatch.setattr("httpx.Client.send", network_forbidden)
    model = local_bge(str(MODEL_DIR))
    texts = ["Employees receive twenty-three vacation days each year.", "Saturn has rings made of ice."]
    vectors = model.embed(texts)
    query = model.embed(["How much annual leave do employees get?"], query=True)[0]
    assert len(vectors) == 2 and len(query) == 384
    scores = [sum(a * b for a, b in zip(query, v)) for v in vectors]
    assert scores[0] > scores[1]
    assert sum(x * x for x in query) == pytest.approx(1, abs=1e-5)
    with pytest.raises(ValueError, match="512"):
        model.embed(["vacation " * 600])
    body = "Start. " + "hello 👋 punctuation/code_name/é  " * 500 + " THE-END"
    chunks = list(model.split(body))
    assert len(chunks) > 1 and chunks[-1].endswith("THE-END")
    assert all(chunk in body and len(model.tokenizer.encode(chunk).ids) <= 512 for chunk in chunks)
    path = tmp_path / "policy.md"
    path.write_text("# Policy\n" + body, encoding="utf-8")
    monkeypatch.setattr(settings(), "embedding_model_dir", MODEL_DIR)
    sections = list(split_sections(path, path.name, hybrid_settings().model_profile()))
    assert all(location == "Policy" for location, _ in sections)


@local_model
def test_reindex_preserves_old_citations_and_is_idempotent(environment, tmp_path, monkeypatch):
    path = tmp_path / "policy.md"
    body = "Employees receive twenty-three vacation days each year."
    path.write_text(body)
    from tests.doubles import DeterministicProvider

    monkeypatch.setattr("backend.embeddings.LegacyEmbeddings", DeterministicProvider)
    old_profile = {
        **settings().model_profile(),
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
        "embedding_revision": "legacy-v1",
        "embedding_pipeline": "legacy-v1",
    }
    with session_scope() as db:
        doc = Document(
            workspace_id=environment["space"],
            name="policy.md",
            path=str(path),
            size=len(body),
            checksum=hashlib.sha256(body.encode()).hexdigest(),
            media_type="text/plain",
            embedding_profile=old_profile,
        )
        db.add(doc)
        db.flush()
        old_id = doc.id
    ingest_document(old_id)
    with session_scope() as db:
        old_chunk = db.scalar(select(Chunk).where(Chunk.document_id == old_id)).id
        old_revision = db.get(Workspace, environment["space"]).revision
    monkeypatch.setattr(settings(), "model_provider", "openai")
    monkeypatch.setattr(settings(), "embedding_provider", "local")
    monkeypatch.setattr(settings(), "embedding_model", BGE_MODEL)
    monkeypatch.setattr(settings(), "embedding_dimensions", 384)
    monkeypatch.setattr(settings(), "embedding_model_dir", MODEL_DIR)
    ids = reindex(environment["space"], environment["admin"])
    assert len(ids) == 1
    assert reindex(environment["space"], environment["admin"]) == []
    ingest_document(ids[0])
    assert reindex(environment["space"], environment["admin"]) == []
    with session_scope() as db:
        assert (
            read_source(db, environment["member"], environment["space"], old_revision, old_chunk)["text"]
            == body
        )
        revision = db.get(Workspace, environment["space"]).revision
        assert retrieve(
            db,
            environment["member"],
            environment["space"],
            revision,
            "vacation days",
            settings().model_profile(),
        )
        with pytest.raises(ValueError, match="do not match"):
            retrieve(
                db,
                environment["member"],
                environment["space"],
                old_revision,
                "leave",
                settings().model_profile(),
            )
    with pytest.raises(Exception) as exc:
        reindex(environment["space"], environment["outsider"])
    assert exc.value.status_code == 404


def test_local_ingestion_does_not_construct_generation_client(environment, monkeypatch):
    class FakeEmbedding:
        def embed(self, texts, query=False):
            return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr("backend.embeddings.local_bge", lambda _: FakeEmbedding())
    monkeypatch.setattr(
        "backend.providers.OpenAI", lambda **kwargs: pytest.fail("Generation client used for embeddings")
    )
    assert len(embedding_provider(hybrid_settings().model_profile()).embed(["text"])[0]) == 384
