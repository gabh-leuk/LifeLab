from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DailyReview(Base):
    """AI 生成的每日复盘。

    与原始记录严格分离：只读快照 source_data 生成，永不修改 Event/Thought/State。
    一次生成一行（upsert by user+date），保留 model 与 status 便于追溯。
    """

    __tablename__ = "daily_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    review_text: Mapped[str] = mapped_column(Text)  # markdown
    structured: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
