"""Five-concurrent-user PostgreSQL retrieval benchmark; synthetic data, no API calls."""

import argparse
import json
import os
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import text

from backend.config import settings
from backend.db import engine, session_scope
from backend.knowledge import retrieve
from backend.manage import migrate
from backend.models import Chunk, Document, Membership, User, Workspace, uid
from tests.doubles import DeterministicProvider
from backend.security import hasher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=int, default=10000)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--output", default="artifacts/retrieval-benchmark.json")
    args = parser.parse_args()
    if engine().url.database != "relay_benchmark" or engine().dialect.name != "postgresql":
        raise SystemExit("Point DATABASE_URL at the dedicated relay_benchmark PostgreSQL database.")
    migrate()
    profile = settings().model_profile()
    model = DeterministicProvider()
    with session_scope() as db:
        user = User(
            email=f"benchmark-{uid()}@example.test", name="Benchmark", password_hash=hasher.hash(uid())
        )
        space = Workspace(name="Synthetic retrieval benchmark", revision=1)
        db.add_all([user, space])
        db.flush()
        db.add(Membership(user_id=user.id, workspace_id=space.id, role="admin"))
        document = Document(
            workspace_id=space.id,
            name="Synthetic policies",
            path="benchmark://synthetic",
            size=0,
            checksum="benchmark",
            media_type="text/plain",
            status="ready",
            introduced_revision=1,
            chunk_count=args.chunks,
            embedding_profile=profile,
        )
        db.add(document)
        db.flush()
        user_id, workspace_id, doc_id = user.id, space.id, document.id
    print(f"Indexing {args.chunks} synthetic chunks in the dedicated benchmark database.")
    for offset in range(0, args.chunks, 100):
        texts = [
            f"Operational policy record {i}. The allocation for department {i} is {10 + i % 30} units per quarter."
            for i in range(offset, min(offset + 100, args.chunks))
        ]
        vectors = model.embed(texts)
        with session_scope() as db:
            db.add_all(
                [
                    Chunk(
                        workspace_id=workspace_id,
                        document_id=doc_id,
                        ordinal=offset + i,
                        location=f"Record {offset + i}",
                        text=body,
                        embedding=vector,
                    )
                    for i, (body, vector) in enumerate(zip(texts, vectors))
                ]
            )
    with engine().begin() as db:
        db.execute(text("ANALYZE chunks"))
    query = "What is the allocation for department 123?"
    vector = model.embed([query])[0]

    def measure(_):
        with session_scope() as db:
            started = time.perf_counter()
            found = retrieve(db, user_id, workspace_id, 1, query, profile, query_vector=vector)
            duration = (time.perf_counter() - started) * 1000
            if not found:
                raise RuntimeError("Retrieval returned no results")
            return duration

    for i in range(10):
        measure(i)
    with ThreadPoolExecutor(max_workers=5) as pool:
        durations = sorted(pool.map(measure, range(args.requests)))
    report = {
        "chunks": args.chunks,
        "concurrency": 5,
        "requests": args.requests,
        "embedding_time_included": False,
        "full_answer_latency_measured": False,
        "p50_ms": statistics.median(durations),
        "p95_ms": durations[max(0, __import__("math").ceil(0.95 * len(durations)) - 1)],
        "max_ms": max(durations),
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "workspace_id": workspace_id,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["p95_ms"] >= 1000:
        raise SystemExit("Retrieval p95 exceeded the 1-second target.")


if __name__ == "__main__":
    main()
