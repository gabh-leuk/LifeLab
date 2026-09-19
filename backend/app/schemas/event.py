from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.behavior_categories import CATEGORIES
from app.models.event import EventSource

BACKFILL_MAX_DAYS = 30


def _ensure_aware(v: datetime) -> datetime:
    return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)


class EventCreate(BaseModel):
    # 内置类型 key 或自定义类型 key（`custom_<8hex>`）；合法性由
    # event_type_service.resolve_event_type_category 判定（那里有 db）
    type: str = Field(max_length=32)
    source: EventSource = EventSource.MANUAL
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str | None = Field(default=None, max_length=2000)
    # 行为大类（不传则由 type 推导）与自由标签
    category: str | None = Field(default=None, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=12)
    # 补记/预记：客户端自定义事件时间；不传 = 服务器当前时间
    timestamp: datetime | None = None

    @field_validator("category")
    @classmethod
    def _category_known(cls, v: str | None) -> str | None:
        if v is not None and v not in CATEGORIES:
            raise ValueError("未知的行为大类 category")
        return v

    @field_validator("tags")
    @classmethod
    def _tags_clean(cls, v: list[str]) -> list[str]:
        out = [t.strip()[:32] for t in v if t and t.strip()]
        return out[:12]

    @field_validator("timestamp", mode="before")
    @classmethod
    def _validate_backfill(cls, v):
        if v is None:
            return None
        if not isinstance(v, datetime):
            v = datetime.fromisoformat(v)  # str → datetime（解析失败 pydantic 自动 422）
        dt = _ensure_aware(v)
        now = datetime.now(timezone.utc)
        if abs(dt - now) > timedelta(days=BACKFILL_MAX_DAYS):
            raise ValueError(f"timestamp 必须在当前时间 ±{BACKFILL_MAX_DAYS} 天内")
        # 精确到分钟：去掉秒和微秒
        return dt.replace(second=0, microsecond=0)


class EventEndRequest(BaseModel):
    """用户显式确认某事件结束。不传 ended_at 则默认当前时间。"""
    ended_at: datetime | None = None


class EventUpdate(BaseModel):
    """就地修订一条记录：只改显式传入的字段。

    用 `model_dump(exclude_unset=True)` 区分"没传"与"显式传 null"——
    `note=None` 表示清空备注，而不是"不改"。
    """

    type: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=32)
    tags: list[str] | None = None

    @field_validator("category")
    @classmethod
    def _category_known(cls, v: str | None) -> str | None:
        if v is not None and v not in CATEGORIES:
            raise ValueError("未知的行为大类 category")
        return v

    @field_validator("tags")
    @classmethod
    def _tags_clean(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [t.strip()[:32] for t in v if t and t.strip()][:12]


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    timestamp: datetime
    # str 而非 EventType：库里可以有自定义类型，用枚举读会 500
    type: str
    source: EventSource
    confidence: float
    note: str | None
    category: str | None = None
    tags: list[str] = []
    device_id: int | None = None
    ended_at: datetime | None

    @field_validator("timestamp", "ended_at", mode="before")
    @classmethod
    def _naive_to_aware(cls, v):
        if isinstance(v, datetime):
            return _ensure_aware(v)
        return v


class DeviceEventCreate(BaseModel):
    """设备令牌通路的手动记录（安卓主屏小组件）。

    刻意比 `EventCreate` 窄：设备侧只按一个钮，不该由它决定大类（由 type 推导）、
    来源（服务端恒置 MANUAL）与置信度。
    """

    type: str = Field(max_length=32)
    note: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("tags")
    @classmethod
    def _tags_clean(cls, v: list[str]) -> list[str]:
        return [t.strip()[:32] for t in v if t and t.strip()][:12]


class RecordStatsResponse(BaseModel):
    """某本地日的记录条数 + 连续记录天数（非 AI 的确定数字）。"""

    date: str
    today_count: int
    streak_days: int


class DeviceEventCreateResponse(BaseModel):
    """记一条就把回报带回来，小组件无需二次请求。"""

    event: EventRead
    today_count: int
    streak_days: int
