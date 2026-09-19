"""add agent_threads (Agent 会话归属登记)

Revision ID: a1b2c3d4e5f6
Revises: d1e2f3a4b5c6
Create Date: 2026-09-16

M6 第一步：Agent 从无状态 ReAct 循环换成有状态图（LangGraph），对话状态存在
checkpoint 表里，`thread_id` 是那里的主键。但 checkpoint 表**只回答"状态在哪"，
不回答"这状态是谁的"** —— 只有 uuid 时，任何登录用户拿到别人的 thread_id
就能续写别人的对话。本表就是那把归属锁：resume / 读取状态前先校验 user_id。

顺带承担侧栏会话列表与标题（标题取首轮提问前若干字）。

checkpoint 四表由下一个迁移建（`b2c3d4e5f6a7`），顺序有意如此：这样
`alembic downgrade -1` 只回滚 checkpoint 表，本表留着。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_threads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
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
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index("ix_agent_threads_user_id", "agent_threads", ["user_id"])
    op.create_index("ix_agent_threads_thread_id", "agent_threads", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_threads_thread_id", table_name="agent_threads")
    op.drop_index("ix_agent_threads_user_id", table_name="agent_threads")
    op.drop_table("agent_threads")
