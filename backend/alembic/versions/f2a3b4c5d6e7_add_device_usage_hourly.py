"""add device_usage_hourly (time-sliced collection)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-11

时段级采集：在 device_usage_daily 之外新增按本地小时聚合的使用时长。
- device_usage_hourly：(user, device, date, hour, app) 唯一，采集器整日替换上报
- 与 daily 并存：读取时同 (device, date) 有 hourly 优先用 hourly，否则回退 daily
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_usage_hourly",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
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
            "user_id",
            "device_id",
            "date",
            "hour",
            "app",
            name="uq_usage_hourly_user_device_day_hour_app",
        ),
    )
    op.create_index(
        "ix_device_usage_hourly_user_id", "device_usage_hourly", ["user_id"]
    )
    op.create_index(
        "ix_device_usage_hourly_device_id", "device_usage_hourly", ["device_id"]
    )
    op.create_index("ix_device_usage_hourly_date", "device_usage_hourly", ["date"])


def downgrade() -> None:
    op.drop_index("ix_device_usage_hourly_date", table_name="device_usage_hourly")
    op.drop_index("ix_device_usage_hourly_device_id", table_name="device_usage_hourly")
    op.drop_index("ix_device_usage_hourly_user_id", table_name="device_usage_hourly")
    op.drop_table("device_usage_hourly")
