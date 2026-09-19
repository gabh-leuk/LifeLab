"""add event_types (user-defined record types)

Revision ID: d4e5f6a7b8c9
Revises: a7c1e2f3b4d5
Create Date: 2026-09-13

用户自定义事件类型。内置类型不入库（由 event_type_service 合成），
本表只存自定义项；`key` 为后端生成的 `custom_<8hex>`，事件行仍存字符串快照。
仅建表、不回填。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "a7c1e2f3b4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_types",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("icon", sa.String(length=32), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_event_types_user_key"),
    )
    op.create_index("ix_event_types_user_id", "event_types", ["user_id"])
    op.create_index("ix_event_types_key", "event_types", ["key"])


def downgrade() -> None:
    op.drop_index("ix_event_types_key", table_name="event_types")
    op.drop_index("ix_event_types_user_id", table_name="event_types")
    op.drop_table("event_types")
