"""Independent embedding providers. Local inference never makes network calls."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from backend.config import settings
from backend.embedding_spec import BGE_MODEL, BGE_REVISION, BGE_PIPELINE, BGE_QUERY_PREFIX


def embedding_identity(profile):
    # Old immutable releases predate the separate embedding provider.
    return {
        "embedding_provider": profile.get("embedding_provider", profile.get("provider", "openai")),
        "embedding_model": profile["embedding_model"],
        "embedding_dimensions": profile["embedding_dimensions"],
        "embedding_revision": profile.get("embedding_revision", "legacy-v1"),
        "embedding_pipeline": profile.get("embedding_pipeline", "legacy-v1"),
    }


def validate_vectors(vectors, count, dimensions):
    import math

    if len(vectors) != count or any(
        len(v) != dimensions or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors
    ):
        raise ValueError("Embedding provider returned invalid vectors")
    return vectors


class LocalBGE:
    def __init__(self, directory):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = Path(directory)
        try:
            manifest = json.loads((directory / "manifest.json").read_text())
            if manifest["model"] != BGE_MODEL or manifest["revision"] != BGE_REVISION:
                raise ValueError("Incorrect local embedding artifact")
            for name in ("tokenizer.json", "onnx/model.onnx"):
                digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
                if digest != manifest["sha256"][name]:
                    raise ValueError("Local embedding artifact checksum mismatch")
        except (OSError, KeyError) as exc:
            raise ValueError("Prepare local embeddings with python -m scripts.prepare_embeddings") from exc
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(directory / "onnx/model.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def split(self, body):
        tokens = self.tokenizer.encode(body, add_special_tokens=False)
        offsets = tokens.offsets
        for start in range(0, len(offsets), 400):
            end = min(start + 448, len(offsets))
            # Include leading/trailing punctuation and whitespace in source slices.
            left = 0 if start == 0 else offsets[start][0]
            right = len(body) if end == len(offsets) else offsets[end][0]
            chunk = body[left:right].strip()
            if chunk:
                yield chunk
            if end == len(offsets):
                break

    def embed(self, texts, query=False):
        import numpy as np

        result = []
        for start in range(0, len(texts), 8):
            batch = texts[start : start + 8]
            encoded = self.tokenizer.encode_batch([BGE_QUERY_PREFIX + t if query else t for t in batch])
            if any(len(item.ids) > 512 for item in encoded):
                raise ValueError(
                    "Embedding input exceeds 512 tokens; shorten the query or reingest the document"
                )
            width = max(len(item.ids) for item in encoded)
            inputs = {}
            for name, attribute in [
                ("input_ids", "ids"),
                ("attention_mask", "attention_mask"),
                ("token_type_ids", "type_ids"),
            ]:
                inputs[name] = np.array(
                    [getattr(item, attribute) + [0] * (width - len(item.ids)) for item in encoded],
                    dtype=np.int64,
                )
            accepted = {item.name for item in self.session.get_inputs()}
            outputs = self.session.run(None, {k: v for k, v in inputs.items() if k in accepted})[0]
            # BGE v1.5 uses CLS pooling followed by L2 normalization.
            vectors = outputs[:, 0, :] if outputs.ndim == 3 else outputs
            vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
            result.extend(vectors.tolist())
        return validate_vectors(result, len(texts), 384)


@lru_cache(maxsize=2)
def local_bge(directory):
    return LocalBGE(directory)


class LegacyEmbeddings:
    def __init__(self, profile):
        self.profile = profile

    def embed(self, texts, query=False):
        from backend.providers import OpenAIProvider

        client = OpenAIProvider(self.profile).client
        response = client.embeddings.create(
            model=self.profile["embedding_model"],
            input=texts,
            dimensions=self.profile["embedding_dimensions"],
        )
        return validate_vectors(
            [v.embedding for v in sorted(response.data, key=lambda v: v.index)],
            len(texts),
            self.profile["embedding_dimensions"],
        )


def embedding_provider(profile):
    identity = embedding_identity(profile)
    if identity["embedding_provider"] == "local":
        if identity != {
            "embedding_provider": "local",
            "embedding_model": BGE_MODEL,
            "embedding_dimensions": 384,
            "embedding_revision": BGE_REVISION,
            "embedding_pipeline": BGE_PIPELINE,
        }:
            raise ValueError("Unsupported local embedding profile; refusing to change a pinned release")
        return local_bge(str(settings().embedding_model_dir.resolve()))
    if identity["embedding_provider"] != "openai":
        raise ValueError("Unsupported embedding provider")
    return LegacyEmbeddings(profile)
