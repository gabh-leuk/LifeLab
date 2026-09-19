"""add memory_items table with pgvector for semantic memory

Revision ID: 7cf2b9d1a3e0
Revises: 59ef01fe7da9
Create Date: 2026-09-08

"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7cf2b9d1a3e0"
down_revision: str | Sequence[str] | None = "59ef01fe7da9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "memory_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "embedding", Vector(dim=1024), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_items_user_id", "memory_items", ["user_id"])
    # 向量相似度索引（HNSW，余弦距离），仅 PG
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_memory_embedding_hnsw ON memory_items "
            "USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_memory_items_user_id", table_name="memory_items")
    op.drop_table("memory_items")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP EXTENSION IF EXISTS vector")