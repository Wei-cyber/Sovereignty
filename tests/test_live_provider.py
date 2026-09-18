import os

import pytest

from backend.config import settings
from backend.execution import validate_answer
from backend.providers import provider


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_MODEL_TESTS") != "1", reason="Live model evaluation is explicitly opt-in"
)
def test_live_structured_answer_embeddings_and_grader(monkeypatch):
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY must be configured for the opt-in live test")
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("CHAT_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("GRADER_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "384")
    settings.cache_clear()
    model = provider()
    source = {
        "chunk_id": "live-test-source",
        "document_id": "live-test-document",
        "document_name": "Synthetic facts",
        "document_version": 1,
        "location": "Section one",
        "text": "The fictional Arbor team receives 23 vacation days per year.",
    }
    question = "How many vacation days does the Arbor team receive?"
    result = model.answer(question, [source], "Answer from evidence.", [])
    answer = validate_answer(result.value, [source])
    assert "23" in answer["text"] and not answer["abstained"]
    assert len(model.embed([source["text"]])[0]) == 384
    decision = model.decide(question, [], [], 4)
    assert decision.value["tool"] == "knowledge_search"
    grade = model.grade(question, source["text"], answer, [source])
    assert grade.value["correctness"] >= 0.85
    assert grade.value["evidence_support"] >= 0.9
    settings.cache_clear()
