from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Device(Base):
    """采集设备：PC 代理 / 安卓采集器。

    token 只在创建时明文返回一次，库里存 sha256（不存明文）。
    user_id 目前固定 1；将来接入用户系统时该列即数据归属。
    """

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(20))  # pc | android
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
