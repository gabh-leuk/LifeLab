"""add app_category_rules (user-defined app/domain → category rules)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-13

用户自定义的应用/域名分类规则，优先级高于内置词表（见 behavior_categories.RuleSet）。
`match_value` 一律小写入库；`scope='domain'` 的还过 normalize_domain。
仅建表、不回填：已有 DEVICE 事件不会自动重算，需调 rebuild-activity-range。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_category_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("match_type", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("match_value", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("display_label", sa.String(length=64), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "match_type",
            "scope",
            "match_value",
            name="uq_app_category_rules_key",
        ),
    )
    op.create_index("ix_app_category_rules_user_id", "app_category_rules", ["user_id"])
    op.create_index(
        "ix_app_category_rules_match_value", "app_category_rules", ["match_value"]
    )


def downgrade() -> None:
    op.drop_index("ix_app_category_rules_match_value", table_name="app_category_rules")
    op.drop_index("ix_app_category_rules_user_id", table_name="app_category_rules")
    op.drop_table("app_category_rules")
