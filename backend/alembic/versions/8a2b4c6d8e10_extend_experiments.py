"""extend experiments (metrics/analysis/conclusion) + add logs & status events

Revision ID: 8a2b4c6d8e10
Revises: 3f1a7b2c9d4e
Create Date: 2026-09-10

Turn experiments into real experiments:
- experiments: metrics JSONB, expected_days, baseline_note, completion_analysis,
  result_verdict, conclusion, conclusion_reason, conclusion_confidence, concluded_at
- experiment_logs: metric data points
- experiment_status_events: transition history with required reason
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "8a2b4c6d8e10"
down_revision: str | Sequence[str] | None = "3f1a7b2c9d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine:
    """PG 用 JSONB，其他方言（SQLite 测试）用 JSON。"""
    if op.get_bind().dialect.name == "postgresql":
        return JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.add_column("experiments", sa.Column("metrics", _json_type(), nullable=True))
    op.add_column("experiments", sa.Column("expected_days", sa.Integer(), nullable=True))
    op.add_column("experiments", sa.Column("baseline_note", sa.Text(), nullable=True))
    op.add_column("experiments", sa.Column("completion_analysis", sa.Text(), nullable=True))
    op.add_column("experiments", sa.Column("result_verdict", sa.String(length=30), nullable=True))
    op.add_column("experiments", sa.Column("conclusion", sa.Text(), nullable=True))
    op.add_column("experiments", sa.Column("conclusion_reason", sa.Text(), nullable=True))
    op.add_column(
        "experiments", sa.Column("conclusion_confidence", sa.Float(), nullable=True)
    )
    op.add_column(
        "experiments",
        sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "experiment_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiment_logs_user_id", "experiment_logs", ["user_id"])
    op.create_index("ix_experiment_logs_experiment_id", "experiment_logs", ["experiment_id"])
    op.create_index("ix_experiment_logs_timestamp", "experiment_logs", ["timestamp"])

    op.create_table(
        "experiment_status_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experiment_status_events_user_id", "experiment_status_events", ["user_id"]
    )
    op.create_index(
        "ix_experiment_status_events_experiment_id",
        "experiment_status_events",
        ["experiment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_experiment_status_events_experiment_id", table_name="experiment_status_events")
    op.drop_index("ix_experiment_status_events_user_id", table_name="experiment_status_events")
    op.drop_table("experiment_status_events")
    op.drop_index("ix_experiment_logs_timestamp", table_name="experiment_logs")
    op.drop_index("ix_experiment_logs_experiment_id", table_name="experiment_logs")
    op.drop_index("ix_experiment_logs_user_id", table_name="experiment_logs")
    op.drop_table("experiment_logs")

    for col in (
        "metrics",
        "expected_days",
        "baseline_note",
        "completion_analysis",
        "result_verdict",
        "conclusion",
        "conclusion_reason",
        "conclusion_confidence",
        "concluded_at",
    ):
        op.drop_column("experiments", col)
