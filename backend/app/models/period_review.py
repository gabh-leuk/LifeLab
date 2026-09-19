from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PeriodReview(Base):
    """周/月复盘：对已浓缩产物的二级分析（只读下层输出，不读原始流水）。

    - week：period_key=2026-W37，输入=本周 L1 洞察 + 每日复盘结构化摘要 + 发现
    - month：period_key=2026-09，输入=本月周综述 + 周模式 + 周遗漏洞察
    与 daily_reviews 同构；一次一行（upsert by user+period_type+period_key）。
    """

    __tablename__ = "period_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    period_type: Mapped[str] = mapped_column(String(10), index=True)  # week | month
    period_key: Mapped[str] = mapped_column(String(20), index=True)  # 2026-W37 / 2026-09
    period_start: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD（含）
    period_end: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD（含）
    review_text: Mapped[str] = mapped_column(Text)  # markdown
    structured: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
