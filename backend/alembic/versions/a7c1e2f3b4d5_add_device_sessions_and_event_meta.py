"""device sessions + event category/tags/device_id

Revision ID: a7c1e2f3b4d5
Revises: f2a3b4c5d6e7
Create Date: 2026-09-13

自动时间段行为（会话级）：
- device_app_sessions：设备应用/网页的精确前台区间（真实测量），供行为分段
- events 增列：category（行为大类）/ tags_raw（自由标签）/ device_id（设备来源）
- 「睡觉」→「上床」：历史 SLEEP_START 迁移为 BED_START（旧键仍保留可用）
- 回填历史事件的 category（无则按 type 推导）
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c1e2f3b4d5"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPE_TO_CATEGORY = """
UPDATE events SET category = CASE type
    WHEN 'LEARNING_START' THEN 'study_work'
    WHEN 'GAME_START' THEN 'game'
    WHEN 'PHONE_START' THEN 'other_online'
    WHEN 'MEAL_START' THEN 'meal'
    WHEN 'SLEEP_START' THEN 'bed'
    WHEN 'BED_START' THEN 'bed'
    WHEN 'OUT_START' THEN 'commute'
    WHEN 'OTHER_START' THEN 'other'
    ELSE category END
WHERE category IS NULL
"""


def upgrade() -> None:
    op.create_table(
        "device_app_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("app", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("start_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "device_id",
            "date",
            "app",
            "start_ts",
            name="uq_session_user_device_day_app_start",
        ),
    )
    op.create_index(
        "ix_device_app_sessions_user_id", "device_app_sessions", ["user_id"]
    )
    op.create_index(
        "ix_device_app_sessions_device_id", "device_app_sessions", ["device_id"]
    )
    op.create_index("ix_device_app_sessions_date", "device_app_sessions", ["date"])
    op.create_index(
        "ix_device_app_sessions_start_ts", "device_app_sessions", ["start_ts"]
    )

    op.add_column(
        "events", sa.Column("category", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "events", sa.Column("tags_raw", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "events", sa.Column("device_id", sa.Integer(), nullable=True)
    )
    op.create_index("ix_events_category", "events", ["category"])
    op.create_index("ix_events_device_id", "events", ["device_id"])

    # 历史「睡觉」→「上床」；并回填 category
    op.execute("UPDATE events SET type = 'BED_START' WHERE type = 'SLEEP_START'")
    op.execute(_TYPE_TO_CATEGORY)


def downgrade() -> None:
    op.drop_index("ix_events_device_id", table_name="events")
    op.drop_index("ix_events_category", table_name="events")
    op.drop_column("events", "device_id")
    op.drop_column("events", "tags_raw")
    op.drop_column("events", "category")

    op.drop_index(
        "ix_device_app_sessions_start_ts", table_name="device_app_sessions"
    )
    op.drop_index("ix_device_app_sessions_date", table_name="device_app_sessions")
    op.drop_index(
        "ix_device_app_sessions_device_id", table_name="device_app_sessions"
    )
    op.drop_index("ix_device_app_sessions_user_id", table_name="device_app_sessions")
    op.drop_table("device_app_sessions")
    # 注意：BED_START 不会回滚成 SLEEP_START（无法区分原本就是 BED_START 的行）
