"""add LangGraph checkpoint tables (agent state persistence)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-16

建 `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` / `checkpoint_migrations`
四张表 —— LangGraph 持久化对话状态（含中断点）的地方。

**为什么调库自己的 `setup()` 而不是手抄 DDL**：这四张表的列与索引由 langgraph
版本决定；手抄一份等于把它冻在抄写那一刻，将来升级 langgraph 会静默不匹配。
`setup()` 幂等（读 `checkpoint_migrations.v` 续跑），永远与已装版本一致。

**方向归属（与仓库"表结构由 Alembic 管理"的关系）**：这四张表**不是业务表**，
不参与 `seed_demo.py` 的按 user_id 清空（那里是白名单，天然不碰），也不进
`Base.metadata`（没有 ORM 模型）。它们由 Alembic 触发创建、由 langgraph 维护。
**升级 langgraph 后如果它新增了 checkpoint 迁移，要再写一个只调 `setup()` 的
alembic 迁移**（本文件可直接照抄）。

`CREATE INDEX CONCURRENTLY` 不能在事务块里执行，而 `alembic/env.py` 把迁移包在
`context.begin_transaction()` 中 → 必须 `autocommit_block()` 逃出去。

`downgrade()` 逆序删表。删掉等于丢弃全部未完成的 Agent 会话（含待审批的中断），
可接受：会话是过程态，不是用户数据。
"""
from collections.abc import Sequence

from alembic import op

from app.agent.checkpointer import setup_checkpoint_tables

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# setup() 建的（子表在前，删表按依赖顺序）
_TABLES = [
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        setup_checkpoint_tables()


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
