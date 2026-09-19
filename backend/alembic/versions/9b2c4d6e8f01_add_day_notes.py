"""add day_notes (pre-review user notes)

Revision ID: 9b2c4d6e8f01
Revises: 8a2b4c6d8e10
Create Date: 2026-09-10

每日小结：用户手写、复盘前可选的补充上下文。
此前该输入只存在前端内存（刷新即丢），现独立持久化，
生成复盘时由 review_service 注入 prompt。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9b2c4d6e8f01"
down_revision: str | Sequence[str] | None = "8a2b4c6d8e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "day_notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
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
    op.create_index("ix_day_notes_user_id", "day_notes", ["user_id"])
    op.create_index("ix_day_notes_date", "day_notes", ["date"])


def downgrade() -> None:
    op.drop_index("ix_day_notes_date", table_name="day_notes")
    op.drop_index("ix_day_notes_user_id", table_name="day_notes")
    op.drop_table("day_notes")
