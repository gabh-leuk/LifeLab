"""drop device_usage_daily (legacy day-granularity usage)

Revision ID: d1e2f3a4b5c6
Revises: b8c9d0e1f2a3
Create Date: 2026-09-16

`device_usage_daily` 是最早的按日聚合表。分小时表（f2a3b4c5d6e7）上线后，
读取改成「同 (device, date) 有 hourly 优先、否则回退 daily」，于是这张表只剩
回退作用。两台采集器（PC 代理、安卓 App）现在都只报 hourly，实测库里
**不存在任何「有 daily 行却没有对应 hourly 行」的 (device, date)** —— 回退分支
永远不触发，整条日粒度链路（表 + `POST /ingest/usage` + 读回退）一并下线。

数据不丢：被删掉的只是 hourly 的影子副本，展示口径一个数都不变。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_device_usage_daily_date", table_name="device_usage_daily")
    op.drop_index("ix_device_usage_daily_device_id", table_name="device_usage_daily")
    op.drop_index("ix_device_usage_daily_user_id", table_name="device_usage_daily")
    op.drop_table("device_usage_daily")


def downgrade() -> None:
    op.create_table(
        "device_usage_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("app", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("switches", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "device_id", "date", "app", name="uq_usage_user_device_day_app"
        ),
    )
    op.create_index("ix_device_usage_daily_user_id", "device_usage_daily", ["user_id"])
    op.create_index(
        "ix_device_usage_daily_device_id", "device_usage_daily", ["device_id"]
    )
    op.create_index("ix_device_usage_daily_date", "device_usage_daily", ["date"])
