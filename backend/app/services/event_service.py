"""事件的写入与记录统计 —— 两条认证链路的共用实现。

`routers/events.py`（登录令牌，网页/客户端）与 `routers/ingest.py`（设备令牌，
安卓小组件）都从这里建事件。category 的推导、tags 的拼接、未知类型的 422
散在两处迟早会漂移 —— 这与 `LEARNING_PATH.md` 的分层纪律（router 薄 / service 厚）
是同一件事，也是 M6 第二步「写路径抽成 service」的一小步。
"""

from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.event import Event, EventSource
from app.services.event_type_service import resolve_event_type_category
from app.utils import parse_day_range

# 连续天数往回数的上限。正常用不到，是防 date 传错时死循环的闸。
STREAK_MAX_DAYS = 365


def create_event(
    db: Session,
    *,
    user_id: int,
    type: str,
    category: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    timestamp: datetime | None = None,
    source: EventSource | str = EventSource.MANUAL,
    confidence: float = 1.0,
) -> Event:
    """建一条事件并提交。

    类型不存在（或已归档）时 422 —— 与 `/events` 一如既往的规矩。
    `category` 显式给了就听它的（调用方 schema 已校验），否则由 type 推导。
    """
    resolved = category or resolve_event_type_category(db, type, user_id=user_id)
    if resolved is None:
        raise HTTPException(status_code=422, detail=f"未知的事件类型 {type}")
    event = Event(
        user_id=user_id,
        timestamp=timestamp or datetime.now(timezone.utc),
        type=type,
        source=source.value if isinstance(source, EventSource) else source,
        confidence=confidence,
        note=note,
        category=resolved,
        tags_raw=",".join(tags) if tags else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def record_stats(
    db: Session, *, user_id: int, date: str, tz_offset: int = 0
) -> dict[str, int]:
    """某本地日的记录条数 + 连续记录天数。

    `today_count` 由调用方决定是哪一天（网页端传今天，自动复盘传昨天）。
    `streak_days` 从 `date` 往回数连续有记录的天数；**`date` 当天为 0 条时改从
    前一天起算** —— 否则每天零点一过连续天数先归零再跳回来，看着像断了。
    """
    today_count = _count_day(db, user_id, date, tz_offset)
    # 只取日期部分；「一天」的边界由 parse_day_range 按 tz_offset 决定
    day = date_cls.fromisoformat(date)
    if today_count == 0:
        day -= timedelta(days=1)
    streak = 0
    for _ in range(STREAK_MAX_DAYS):
        if _count_day(db, user_id, day.isoformat(), tz_offset) == 0:
            break
        streak += 1
        day -= timedelta(days=1)
    return {"today_count": today_count, "streak_days": streak}


def _count_day(db: Session, user_id: int, date: str, tz_offset: int) -> int:
    """某本地日的事件条数。日期非法由 parse_day_range 抛 422。"""
    start, end = parse_day_range(date, tz_offset)
    return (
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.user_id == user_id,
                Event.timestamp >= start,
                Event.timestamp < end,
            )
        )
        or 0
    )
