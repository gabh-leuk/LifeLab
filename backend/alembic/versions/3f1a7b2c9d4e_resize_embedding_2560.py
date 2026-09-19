"""resize memory embedding to halfvec(2560) + HNSW index (qwen3-embedding)

Revision ID: 3f1a7b2c9d4e
Revises: 7cf2b9d1a3e0
Create Date: 2026-09-08

Switch embedding provider to qwen3-embedding (2560 dims). pgvector's
regular vector HNSW/IVFFlat index classes cap at 2000 dims, but
halfvec (float16) supports up to 4000 dims and has its own opclass
`halfvec_cosine_ops`, enabling a true HNSW index at 2560 dims.

Verified live on this PG image: CREATE INDEX ... USING hnsw
(emb halfvec_cosine_ops) succeeds; <=> distance works.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "3f1a7b2c9d4e"
down_revision: str | Sequence[str] | None = "7cf2b9d1a3e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_memory_embedding_hnsw")
        op.execute(
            "ALTER TABLE memory_items ALTER COLUMN embedding "
            "TYPE halfvec(2560) USING embedding::halfvec(2560)"
        )
        # halfvec 需要专用 opclass（vector_cosine_ops 不接受 halfvec）
        op.execute(
            "CREATE INDEX ix_memory_embedding_hnsw ON memory_items "
            "USING hnsw (embedding halfvec_cosine_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_memory_embedding_hnsw")
        op.execute(
            "ALTER TABLE memory_items ALTER COLUMN embedding "
            "TYPE vector(1024) USING embedding::vector(1024)"
        )
        op.execute(
            "CREATE INDEX ix_memory_embedding_hnsw ON memory_items "
            "USING hnsw (embedding vector_cosine_ops)"
        )