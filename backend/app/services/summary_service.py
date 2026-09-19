"""某日记录量的汇总统计。

原先内联在 `routers/summary.py` 里。M6 第二步抽出来，让 Agent 的
`get_review(period_type="day")` 能直接拿到「今天记了多少」，不必重复写一遍 SQL。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.state import StateRecord
from app.models.thought import Thought
from app.schemas.summary import DailySummary, TypeCount
from app.utils import parse_day_range


def daily_summary(
    db: Session, date: str, *, user_id: int, tz_offset: int = 0
) -> DailySummary:
    """按本地时区聚合某天的行为/想法/状态计数与状态均值。"""
    day_start, day_end = parse_day_range(date, tz_offset)

    event_counts = db.execute(
        select(Event.type, func.count(Event.id))
        .where(
            Event.user_id == user_id,
            Event.timestamp >= day_start,
            Event.timestamp < day_end,
        )
        .group_by(Event.type)
    ).all()
    event_total = db.scalar(
        select(func.count(Event.id)).where(
            Event.user_id == user_id,
            Event.timestamp >= day_start,
            Event.timestamp < day_end,
        )
    ) or 0
    thought_total = db.scalar(
        select(func.count(Thought.id)).where(
            Thought.user_id == user_id,
            Thought.timestamp >= day_start,
            Thought.timestamp < day_end,
        )
    ) or 0
    state_total = db.scalar(
        select(func.count(StateRecord.id)).where(
            StateRecord.user_id == user_id,
            StateRecord.timestamp >= day_start,
            StateRecord.timestamp < day_end,
        )
    ) or 0

    avg_row = db.execute(
        select(
            func.avg(StateRecord.energy),
            func.avg(StateRecord.focus),
            func.avg(StateRecord.irritation),
        ).where(
            StateRecord.user_id == user_id,
            StateRecord.timestamp >= day_start,
            StateRecord.timestamp < day_end,
        )
    ).one()

    first_event = db.scalar(
        select(Event.timestamp)
        .where(
            Event.user_id == user_id,
            Event.timestamp >= day_start,
            Event.timestamp < day_end,
        )
        .order_by(Event.timestamp.asc())
        .limit(1)
    )
    last_event = db.scalar(
        select(Event.timestamp)
        .where(
            Event.user_id == user_id,
            Event.timestamp >= day_start,
            Event.timestamp < day_end,
        )
        .order_by(Event.timestamp.desc())
        .limit(1)
    )

    def _r(x) -> float | None:
        return round(x, 1) if x is not None else None

    return DailySummary(
        date=date,
        event_total=event_total,
        by_type=[
            TypeCount(type=t, count=c) for t, c in sorted(event_counts, key=lambda r: -r[1])
        ],
        thought_total=thought_total,
        state_total=state_total,
        avg_energy=_r(avg_row[0]),
        avg_focus=_r(avg_row[1]),
        avg_irritation=_r(avg_row[2]),
        first_event_at=first_event,
        last_event_at=last_event,
    )
