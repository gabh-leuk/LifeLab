from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 边界二：画像层。只有"跨话题稳定 + 用户确认"的事实才进这里，
# 每次问答/分析自动注入 prompt（常驻上下文，体量有配额）。
# 向量库（memory_items）是情景召回；本表是自我档案，两者不互相回流。
PROFILE_CATEGORIES = ("identity", "habit", "preference", "context", "constraint")
PROFILE_STATUSES = ("active", "archived")


class ProfileFact(Base):
    """用户背景事实：常驻注入 prompt 的稳定自我描述。

    - source=manual（用户手写=已确认）/ ai（AI 候选经用户授权后写入）
    - source_ref 可溯源（如 analysis:<id>、pattern:2026-W37）
    - status=archived 保留可查，但不再注入；只有用户显式删除才真删
    """

    __tablename__ = "profile_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active", index=True)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
