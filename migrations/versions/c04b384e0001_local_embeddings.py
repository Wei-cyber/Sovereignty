"""Support BGE vectors while preserving legacy chunks and citation IDs."""

from alembic import op
from sqlalchemy import text

revision = "c04b384e0001"
down_revision = "b92d1024e01f"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
        op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector USING embedding::vector")
        for dimension in (384, 1536):
            op.execute(
                f"CREATE INDEX ix_chunks_embedding_hnsw_{dimension} ON chunks USING hnsw "
                f"((embedding::vector({dimension})) vector_cosine_ops) WHERE vector_dims(embedding)={dimension}"
            )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        if op.get_bind().scalar(text("SELECT count(*) FROM chunks WHERE vector_dims(embedding) <> 1536")):
            raise ValueError("Cannot downgrade while local vectors exist; restore a verified backup instead")
        for dimension in (384, 1536):
            op.execute(f"DROP INDEX IF EXISTS ix_chunks_embedding_hnsw_{dimension}")
        op.execute(
            "ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)"
        )
        op.execute("CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
