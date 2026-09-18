"""Deterministic test substitutes; never shipped or selectable in Relay."""

import hashlib
import math
import re
from backend.providers import ModelResult, ABSTENTION, terms
from backend.config import settings


class DeterministicProvider:
    """Deterministic extractive test double. Never presented as a live language model."""

    def __init__(self, profile=None):
        self.profile = profile or settings().model_profile()

    def split(self, text):
        for offset in range(0, len(text), 1500):
            yield text[offset : offset + 1800]

    def embed(self, texts, query=False):
        result = []
        for text in texts:
            vector = [0.0] * self.profile["embedding_dimensions"]
            for term in terms(text):
                index = int(hashlib.sha256(term.encode()).hexdigest()[:8], 16) % len(vector)
                vector[index] += 1
            norm = math.sqrt(sum(x * x for x in vector)) or 1
            result.append([x / norm for x in vector])
        return result

    def answer(self, question, sources, prompt, history):
        if question.lower().startswith("draft a test email"):
            return ModelResult(
                {
                    "text": "Draft ready for review",
                    "citations": [],
                    "abstained": False,
                    "draft": {
                        "to": ["test@example.test"],
                        "subject": "Test draft",
                        "body": "Original test message",
                    },
                }
            )
        query = set(terms(question))
        ranked = []
        for source in sources:
            for sentence in re.split(r"(?<=[.!?])\s+|\n", source["text"]):
                sentence = sentence.strip().lstrip("# ")
                overlap = len(query & set(terms(sentence)))
                if overlap and len(sentence) > 15:
                    ranked.append((overlap, sentence, source))
        ranked.sort(key=lambda x: x[0], reverse=True)
        if not ranked or ranked[0][0] < min(2, max(1, len(query))):
            return ModelResult({"text": ABSTENTION, "citations": [], "abstained": True})
        best = ranked[0]
        return ModelResult(
            {
                "text": best[1],
                "citations": [{"chunk_id": best[2]["chunk_id"], "quote": best[1]}],
                "abstained": False,
            },
            len(terms(question)) + len(terms(best[1])),
        )

    def decide(self, question, sources, history, remaining, instructions="", allowed_tools=None):
        if not history:
            return ModelResult({"tool": "knowledge_search", "arguments": {"query": question}})
        if len(history) == 1 and sources:
            return ModelResult({"tool": "read_source", "arguments": {"chunk_id": sources[0]["chunk_id"]}})
        return ModelResult({"tool": "finish", "arguments": {}})

    def grade(self, question, reference, answer, sources):
        expected = set(terms(reference))
        actual = set(terms(answer.get("text", "")))
        correctness = len(expected & actual) / max(len(expected), 1)
        supported = any(answer.get("text", "") in s["text"] for s in sources)
        return ModelResult(
            {
                "correctness": correctness,
                "evidence_support": float(supported),
                "rationale": "TEST FIXTURE: lexical overlap and exact evidence matching.",
            }
        )
