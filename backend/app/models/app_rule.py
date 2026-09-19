from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AppCategoryRule(Base):
    """用户自定义的应用/域名 → 行为大类规则（覆盖内置词表）。

    - `match_value` 一律小写入库；`scope='domain'` 的还过 `normalize_domain`
    - `display_label` 非空时作为该应用的展示名（别名/改名）
    - `priority` 越大越优先；同优先级更长的 match_value 胜出
    - 唯一 (user_id, match_type, scope, match_value)：重复提交按 upsert 处理
    """

    __tablename__ = "app_category_rules"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "match_type",
            "scope",
            "match_value",
            name="uq_app_category_rules_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    match_type: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(16))
    match_value: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(32))
    display_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
