"""add users and auth_tokens (+ 预置 demo / personal 两个账号)

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-09-13

引入用户模块：登录令牌（可撤销）取代全局 FIXED_USER_ID。

**账号在本次迁移里直接插入，主键显式指定**：
- id=1 `demo`     —— 演示/测试账号，存量全部数据都归它（见下一个迁移）
- id=2 `personal` —— 个人账号，从零开始，正式部署后才采集

demo 必须是 id=1，否则存量数据的 user_id 要全表改写。

密码从环境变量取，缺省用开发口令；**上线前务必用
POST /auth/password 改密**（改密会撤销全部令牌）。
- DEMO_PASSWORD（缺省 demo1234）
- PERSONAL_PASSWORD（缺省 lifelab1234）

business 表**不加外键**（沿用既有裸 user_id + index 的约定）；
只有 auth_tokens.user_id 加 FK，删用户即失效其全部令牌。
"""
import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.security import hash_password

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_USERS = (
    (1, "demo", os.environ.get("DEMO_PASSWORD", "demo1234"), True, "演示账号"),
    (2, "personal", os.environ.get("PERSONAL_PASSWORD", "lifelab1234"), False, "我的账号"),
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("display_name", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"])

    bind = op.get_bind()
    for uid, username, password, is_demo, display in SEED_USERS:
        bind.execute(
            sa.text(
                "INSERT INTO users (id, username, password_hash, is_demo, is_active,"
                " display_name) VALUES (:id, :u, :p, :d, :a, :n)"
            ),
            {
                "id": uid,
                "u": username,
                "p": hash_password(password),
                "d": is_demo,
                "a": True,
                "n": display,
            },
        )

    # 显式插入主键后要修序列，否则下一次 INSERT 会撞主键（SQLite 自带 max+1，无需处理）
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence('users', 'id'),"
                " (SELECT max(id) FROM users))"
            )
        )


def downgrade() -> None:
    op.drop_index("ix_auth_tokens_token_hash", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_user_id", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
