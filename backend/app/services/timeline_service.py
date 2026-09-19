"""时间线聚合服务：事件 → 连续时间段。

从 routers/timeline.py 抽出的纯逻辑，供时间线接口与实验数据聚合共用。
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event, EventSource
from app.schemas.timeline import EventSegment
from app.utils import ensure_aware

# 模糊阈值（分钟）：推断结束超过此值时打"模糊"标签。
# 睡觉/出门视为"天然长时段"，不提示模糊；其余默认 3 小时（可调）。
FUZZY_THRESHOLD_MINUTES: dict[str, int | None] = {
    "LEARNING_START": 3 * 60,
    "GAME_START": 3 * 60,
    "PHONE_START": 3 * 60,
    "MEAL_START": 3 * 60,
    "OTHER_START": 3 * 60,
    "SLEEP_START": None,  # 不模糊
    "OUT_START": None,    # 不模糊
}


def build_segments(
    events: list[Event],
    day_start: datetime,
    day_end: datetime,
) -> list[EventSegment]:
    """事件 → 连续时间段。结束规则：
    1. 用户显式 ended_at → 用它（若晚于下个事件开始，被下个事件截断取 min）
    2. 否则默认"下一事件开始"（新事件开始 = 上一事件结束）
    3. 当天最后一个 → 当天末尾（day_end 前一瞬）
    推断结束且时长超阈值 → fuzzy。
    """
    # 设备事件是真实测量，但不是人工声明的"行为边界"：它不能截断人工事件
    # （否则「上床 23:00」会被其中的手机段截成长度 0）。设备层单独渲染。
    events = [e for e in events if (e.source or EventSource.MANUAL.value) != EventSource.DEVICE.value]

    segments: list[EventSegment] = []
    count = len(events)

    for i, ev in enumerate(events):
        start = ensure_aware(ev.timestamp)
        next_start = (
            ensure_aware(events[i + 1].timestamp) if i + 1 < count else None
        )
        ev_end = ensure_aware(ev.ended_at) if ev.ended_at is not None else None

        if ev_end is not None:
            end = ev_end
            end_inferred = False
            boundary = "explicit"
            if next_start is not None and end > next_start:
                end = next_start  # 保持连续：显式结束晚于下个开始则截断
                end_inferred = True
                boundary = "next_event"
        else:
            if next_start is not None:
                end = next_start
                end_inferred = True
                boundary = "next_event"
            else:
                end = day_end
                end_inferred = True
                boundary = "day_end"

        duration_minutes = max(1, int((end - start).total_seconds() // 60))
        threshold = FUZZY_THRESHOLD_MINUTES.get(ev.type)
        fuzzy = end_inferred and threshold is not None and duration_minutes >= threshold

        segments.append(
            EventSegment(
                event=ev,
                start=start,
                end=end,
                duration_minutes=duration_minutes,
                end_inferred=end_inferred,
                end_boundary=boundary,
                fuzzy=fuzzy,
                fuzzy_reason=(
                    f"推断进行超过 {duration_minutes} 分钟，请确认该事件是否仍在进行或已结束"
                    if fuzzy
                    else None
                ),
            )
        )
    return segments


def load_day_events(
    db: Session,
    day_start: datetime,
    day_end: datetime,
    user_id: int,
) -> list[Event]:
    """取某时间窗口内的事件（升序），供分段/聚合。"""
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.timestamp >= day_start,
                Event.timestamp < day_end,
            )
            .order_by(Event.timestamp.asc())
        ).all()
    )
