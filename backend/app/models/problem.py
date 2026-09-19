from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProblemStatus(StrEnum):
    OPEN = "OPEN"
    DORMANT = "DORMANT"
    RESOLVED = "RESOLVED"


class Problem(Base):
    """长期问题库：如"为什么精力不足"。

    一个问题可被多个 Finding 引用，是以后 Research Agent 的知识底座。
    """

    __tablename__ = "problems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), default=ProblemStatus.OPEN, index=True
    )
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | ai
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )