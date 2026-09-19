from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FindingKind(StrEnum):
    """证据类型：严格区分事实/推测/结论（AI 红线）。"""

    OBSERVATION = "OBSERVATION"  # 观察：发生了什么（事实）
    HYPOTHESIS = "HYPOTHESIS"    # 假设：可能的解释（推测）
    CONCLUSION = "CONCLUSION"    # 结论：经过验证的判断


class Finding(Base):
    """证据结论：一次实验或观察得到的发现。

    observation 记录事实，interpretation 记录推测，kind 明确分类。
    problem_id 可空：不一定要挂在某个问题上。
    """

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    problem_id: Mapped[int | None] = mapped_column(
        ForeignKey("problems.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default=FindingKind.OBSERVATION)
    title: Mapped[str] = mapped_column(String(200))
    observation: Mapped[str] = mapped_column(Text)  # 事实：观察到什么
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # 证据来源
    interpretation: Mapped[str | None] = mapped_column(Text, nullable=True)  # 推测
    confidence: Mapped[float] = mapped_column(
        Float, default=0.5, server_default="0.5"
    )  # 0-1 可信度
    next_step: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )