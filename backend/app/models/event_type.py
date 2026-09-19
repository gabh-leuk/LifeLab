from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 自定义类型 key 的前缀。事件行的 `type` 是字符串快照，指标源等只凭前缀就能
# 区分「内置类型」与「自定义类型」，不必查库。
CUSTOM_KEY_PREFIX = "custom_"


class CustomEventType(Base):
    """用户自定义的事件类型。内置类型不入库（见 event_type_service）。

    `key` 由后端生成 `custom_<8hex>`：**不能以 `_START` 结尾**，
    否则前端 `eventTypeLabel` 的 `replace(/_START$/, "")` 会把标签降级成裸 key。
    """

    __tablename__ = "event_types"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_event_types_user_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(16))
    # 行为大类（behavior_categories.CATEGORIES 的 key）
    category: Mapped[str] = mapped_column(String(32))
    # 精选图标集里的 key（前端 ICON_CHOICES）；空则按 category 取默认图标
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 软删：事件行保留 type/category 快照，归档不破坏历史
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
