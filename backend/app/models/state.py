from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StateRecord(Base):
    """状态快照：精力/注意力/烦躁 0-5。一次打分一行，时间由服务器生成。"""

    __tablename__ = "state_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    energy: Mapped[int] = mapped_column(Integer)  # 0-5
    focus: Mapped[int] = mapped_column(Integer)  # 0-5
    irritation: Mapped[int] = mapped_column(Integer)  # 0-5
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
