"""boundary enforcement: profile_facts + problems.source + memory pool cleanup

Revision ID: c5e7a9b1d3f5
Revises: b2d3f4a5c6e7
Create Date: 2026-09-11

两条边界：
1. 向量库只收提炼产物 → 删除 memory_items 里的原始 kind（thought/event/problem）
2. 画像层 → 新建 profile_facts（常驻注入，用户确认制）；
   同时给 problems 加 source/source_ref（AI 候选必须可区分，红线要求）
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5e7a9b1d3f5"
down_revision: str | Sequence[str] | None = "b2d3f4a5c6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profile_facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_profile_facts_user_id", "profile_facts", ["user_id"])
    op.create_index("ix_profile_facts_category", "profile_facts", ["category"])
    op.create_index("ix_profile_facts_status", "profile_facts", ["status"])

    op.add_column(
        "problems",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )
    op.add_column("problems", sa.Column("source_ref", sa.String(length=255), nullable=True))

    # 边界一清理：历史 sync 写入的原始 kind 全部移除（原始数据仍在各自业务表）
    op.execute(
        "DELETE FROM memory_items WHERE kind IN ('thought', 'event', 'problem')"
    )


def downgrade() -> None:
    op.drop_column("problems", "source_ref")
    op.drop_column("problems", "source")
    op.drop_index("ix_profile_facts_status", table_name="profile_facts")
    op.drop_index("ix_profile_facts_category", table_name="profile_facts")
    op.drop_index("ix_profile_facts_user_id", table_name="profile_facts")
    op.drop_table("profile_facts")
    # 被删除的原始 kind 记忆不会在 downgrade 恢复（如需可重新 sync）
