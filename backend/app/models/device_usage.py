from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DeviceAppSession(Base):
    """设备应用/网页的精确前台会话（真实测量，区间到秒）。

    采集器保留前台切换的精确起止后上报；服务端按 (device, date) 整体替换，幂等。
    这是「自动时间段行为」的数据源：由 behavior_service 合并为行为段，
    再落成 source=DEVICE 的 events。
    """

    __tablename__ = "device_app_sessions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "device_id",
            "date",
            "app",
            "start_ts",
            name="uq_session_user_device_day_app_start",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(16))  # pc | android
    date: Mapped[str] = mapped_column(String(10), index=True)  # 采集器本地日 YYYY-MM-DD
    app: Mapped[str] = mapped_column(String(200))  # 包名/进程/域名
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DeviceUsageHourly(Base):
    """设备应用使用时长（按本地小时聚合）。

    原本还有一张按日聚合的 device_usage_daily 与之并存（读取时 hourly 优先、
    否则回退 daily）。两台采集器都早已只报 hourly，实测库里不存在「有 daily 无
    hourly」的 (device, date)，回退分支已是死代码 —— 2026-09-16 连表一起下线。
    采集器按 (device, date) 整体替换，幂等。
    """

    __tablename__ = "device_usage_hourly"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "device_id",
            "date",
            "hour",
            "app",
            name="uq_usage_hourly_user_device_day_hour_app",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD（采集器本地日）
    hour: Mapped[int] = mapped_column(Integer)  # 0..23（采集器本地小时）
    app: Mapped[str] = mapped_column(String(200))
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0)  # 该小时内的秒数（≤3600）
    switches: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
