from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DayNote(Base):
    """每日小结：复盘生成前的用户手写输入（可选）。

    独立于 AI 复盘存在（复盘可重生成，小结不被覆盖）；
    生成复盘时作为补充上下文注入 prompt，解决此前"本地暂存、刷新即丢"的问题。
    一次一行（upsert by user+date），空白内容视为清除该日小结。
    """

    __tablename__ = "day_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
