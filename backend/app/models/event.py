from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EventType(StrEnum):
    LEARNING_START = "LEARNING_START"
    GAME_START = "GAME_START"
    PHONE_START = "PHONE_START"
    MEAL_START = "MEAL_START"
    # 「上床」取代「睡觉」：只知道上床时刻，入睡时间不可观测，不假装知道。
    # SLEEP_START 保留为旧键（历史数据/旧指标源兼容），新建记录请用 BED_START。
    BED_START = "BED_START"
    SLEEP_START = "SLEEP_START"
    OUT_START = "OUT_START"
    OTHER_START = "OTHER_START"
    # 线下专属：设备观测不到，只能手记（在线行为由采集自动生成，不占记录按钮）。
    # 注意 EXERCISE/CHORES/SOCIAL_OFFLINE 三类都**不属于 ONLINE_CATEGORIES**，
    # 因此永远不会与设备段同类判重。
    EXERCISE_START = "EXERCISE_START"
    CHORES_START = "CHORES_START"
    SOCIAL_OFFLINE_START = "SOCIAL_OFFLINE_START"
    # 设备采集自动落库的行为段（真实测量，非推测）；具体活动看 category/note
    DEVICE_ACTIVITY = "DEVICE_ACTIVITY"


class EventSource(StrEnum):
    MANUAL = "MANUAL"
    AGENT = "AGENT"
    DEVICE = "DEVICE"


class Event(Base):
    """一次"点击即完成"的行为记录，timestamp 由服务器生成，不允许客户端伪造。"""

    __tablename__ = "events"

    """"
    【阶段一：程序启动（造探头）】
    mapped_column(...) 运行 ──► 生成探头 ──► 挂在 Event.user_id 上待命

    ----------------------------------------------------

    【阶段二：请求来了，你写 Event(user_id=1001)（喂数据）】
    你写 Event(user_id=1001)
    │
    ▼
    父类 Base 的 __init__ 接到参数: {"user_id": 1001}
    │
    ▼
    父类执行: setattr(event, "user_id", 1001)
    │
    ▼
    Python 发现 Event.user_id 上挂着阶段一造好的探头！
    │
    ▼
    Python 自动调用: Event.user_id.__set__(event, 1001)
    │
    ▼
    【探头终于拿到了 1001！】──► 存入数据字典，并通知状态机登记
    """
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    type: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(16), default=EventSource.MANUAL)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 行为大类（behavior_categories.CATEGORIES 的 key）；人工/设备共用，判重与统计用
    category: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    # 自由标签，逗号分隔入库（与 Thought.tags_raw 同构），读时经 property tags 转 list
    tags_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 设备采集来源（仅 source=DEVICE 时非空）：整日重算时按 (device, 日) 清理重建。
    # 不建外键约束（SQLite 批量迁移友好）；删设备时由 device_service 连带清理。
    device_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    # 用户显式给出的结束时间；NULL = 未确认，结束 = 推断（下一事件开始/当天末尾）
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def tags(self) -> list[str]:
        return [t for t in (self.tags_raw or "").split(",") if t]
