"""add devices and device_usage_daily (collection pipeline)

Revision ID: e1f2a3b4c5d6
Revises: c5e7a9b1d3f5
Create Date: 2026-09-11

采集管道：设备 token 认证 + 应用/网页使用时长按日存储。
- devices：每台采集器一行（token 只存 sha256）
- device_usage_daily：(user, device, date, app) 唯一，采集器整日替换上报
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "c5e7a9b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])
    op.create_index("ix_devices_token_hash", "devices", ["token_hash"])

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
    op.create_index("ix_device_usage_daily_device_id", "device_usage_daily", ["device_id"])
    op.create_index("ix_device_usage_daily_date", "device_usage_daily", ["date"])


def downgrade() -> None:
    op.drop_index("ix_device_usage_daily_date", table_name="device_usage_daily")
    op.drop_index("ix_device_usage_daily_device_id", table_name="device_usage_daily")
    op.drop_index("ix_device_usage_daily_user_id", table_name="device_usage_daily")
    op.drop_table("device_usage_daily")
    op.drop_index("ix_devices_token_hash", table_name="devices")
    op.drop_index("ix_devices_user_id", table_name="devices")
    op.drop_table("devices")
