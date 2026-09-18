"""Exercise the actual migration against PostgreSQL when its dedicated test DB is configured."""

import importlib
import os

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("RELAY_TEST_DATABASE_URL"), reason="Dedicated PostgreSQL test DB required"
)
def test_migration_preserves_legacy_vectors_and_indexes_bge():
    engine = create_engine(os.environ["RELAY_TEST_DATABASE_URL"])
    if engine.url.database != "relay_ci_test":
        raise RuntimeError("Migration tests require relay_ci_test")
    migration = importlib.import_module("migrations.versions.c04b384e0001_local_embeddings")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(
                text("CREATE TEMP TABLE chunks (id integer, embedding vector(1536)) ON COMMIT DROP")
            )
            # The temporary table shadows public chunks; migration operates on isolated test data.
            connection.execute(
                text(
                    "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"
                )
            )
            legacy = "[" + ",".join(["1"] + ["0"] * 1535) + "]"
            local = "[" + ",".join(["1"] + ["0"] * 383) + "]"
            connection.execute(text("INSERT INTO chunks VALUES (1, CAST(:v AS vector))"), {"v": legacy})
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.execute(text("INSERT INTO chunks VALUES (2, CAST(:v AS vector))"), {"v": local})
            assert connection.execute(
                text("SELECT id, vector_dims(embedding) FROM chunks ORDER BY id")
            ).all() == [(1, 1536), (2, 384)]
            found = connection.scalar(
                text(
                    "SELECT id FROM chunks WHERE vector_dims(embedding)=384 "
                    "ORDER BY embedding::vector(384) <=> CAST(:v AS vector(384)) LIMIT 1"
                ),
                {"v": local},
            )
            assert found == 2
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(ValueError, match="Cannot downgrade"):
                    migration.downgrade()
                connection.execute(text("DELETE FROM chunks WHERE id=2"))
                migration.downgrade()
            assert connection.scalar(text("SELECT vector_dims(embedding) FROM chunks")) == 1536
    finally:
        engine.dispose()
