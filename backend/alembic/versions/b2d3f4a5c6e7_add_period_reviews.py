"""add period_reviews (weekly/monthly reviews)

Revision ID: b2d3f4a5c6e7
Revises: 9b2c4d6e8f01
Create Date: 2026-09-10

周/月复盘：对已浓缩产物的二级分析，与 daily_reviews 同构。
week: period_key=2026-W37；month: period_key=2026-09。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2d3f4a5c6e7"
down_revision: str | Sequence[str] | None = "9b2c4d6e8f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "period_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period_type", sa.String(length=10), nullable=False),
        sa.Column("period_key", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.String(length=10), nullable=False),
        sa.Column("period_end", sa.String(length=10), nullable=False),
        sa.Column("review_text", sa.Text(), nullable=False),
        sa.Column("structured", sa.JSON(), nullable=True),
        sa.Column("source_data", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
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
    op.create_index("ix_period_reviews_user_id", "period_reviews", ["user_id"])
    op.create_index("ix_period_reviews_period_type", "period_reviews", ["period_type"])
    op.create_index("ix_period_reviews_period_key", "period_reviews", ["period_key"])


def downgrade() -> None:
    op.drop_index("ix_period_reviews_period_key", table_name="period_reviews")
    op.drop_index("ix_period_reviews_period_type", table_name="period_reviews")
    op.drop_index("ix_period_reviews_user_id", table_name="period_reviews")
    op.drop_table("period_reviews")
