"""add agent_threads.replay (demo 只读会话回放)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-16

M6 第二步：demo 账号的 AI 全封（app/deps.py 的 require_ai_enabled），
`/agent/run` 对 demo 一律 403，所以它永远攒不出 checkpoint —— Agent 页
在公网作品集里就是「一个灰掉的输入框」，看不出这个功能长什么样。

于是给 `agent_threads` 加一列 `replay`：**预置的会话快照**（messages /
steps / answer 的 JSON），由 seed_demo.py 写入。`GET /agent/threads/{id}`
在没有 checkpoint 时（`values` 为空）就回放它。读写端点仍对 demo 全封，
所以回放是只读的、点不动，也不会有访客写脏数据。

为什么是静态快照而不是种 checkpoint：checkpoint 的 schema 由 langgraph
版本决定（见 b2c3d4e5f6a7 的说明），种它等于把回放绑死在某个 langgraph
版本上；而这份快照只是一段要显示的文字，不参与图的执行。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine:
    """PG 用 JSONB，其他方言（SQLite 测试）用 JSON。"""
    if op.get_bind().dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.add_column("agent_threads", sa.Column("replay", _json_type(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_threads", "replay")
