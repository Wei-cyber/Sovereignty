"""PostgreSQL hybrid search indexes and unique version constraints."""

from alembic import op

revision = "b92d1024e01f"
down_revision = "510db58abfea"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("uq_document_family_version", "documents", ["family_id", "version"], unique=True)
    op.create_index("uq_suite_family_version", "evaluation_suites", ["family_id", "version"], unique=True)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")
        op.execute("CREATE INDEX ix_chunks_fts ON chunks USING gin (to_tsvector('english', text))")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_chunks_fts")
        op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_index("uq_suite_family_version", table_name="evaluation_suites")
    op.drop_index("uq_document_family_version", table_name="documents")
