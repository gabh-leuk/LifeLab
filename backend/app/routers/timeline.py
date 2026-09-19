from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import DbDep, UserDep
from app.models.device import Device
from app.models.event import EventSource
from app.models.state import StateRecord
from app.models.thought import Thought
from app.schemas.timeline import (
    DeviceActivity,
    EventSegment,
    FloatingItem,
    SegmentApp,
    TimelineResponse,
)
from app.services import day_hours_service, device_service, timeline_service
from app.utils import ensure_aware, parse_day_range

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("/{date}", response_model=TimelineResponse)
def get_timeline(
    date: str,
    db: DbDep,
    current_user: UserDep,
    tz_offset: int = Query(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480"),
):
    # 按客户端本地时区分天，保证"当天最后一段"锚定到本地午夜
    day_start, day_end = parse_day_range(date, tz_offset)

    events = timeline_service.load_day_events(db, day_start, day_end, current_user.id)
    thoughts = db.scalars(
        select(Thought).where(
            Thought.user_id == current_user.id,
            Thought.timestamp >= day_start,
            Thought.timestamp < day_end,
        )
    ).all()
    states = db.scalars(
        select(StateRecord).where(
            StateRecord.user_id == current_user.id,
            StateRecord.timestamp >= day_start,
            StateRecord.timestamp < day_end,
        )
    ).all()

    segments = timeline_service.build_segments(events, day_start, day_end)
    platform_by_device = {
        d.id: d.platform
        for d in db.scalars(select(Device).where(Device.user_id == current_user.id)).all()
    }
    device_segments = _attach_device_events(segments, events, platform_by_device)
    # 人工段与设备段的应用明细一起回填（一次查询覆盖两批，但两批口径不同）
    _attach_device_apps(
        db, date, segments, device_segments, day_start, current_user.id
    )
    device_hours = day_hours_service.build_day_hours(
        db, date, day_start, day_end, user_id=current_user.id
    )
    floatings = _assign_floatings(segments, thoughts, states)

    return TimelineResponse(
        date=date,
        segments=segments,
        floatings=floatings,
        device_segments=device_segments,
        device_hours=device_hours,
    )


def _attach_device_apps(
    db: Session,
    date: str,
    manual_segments,
    device_segments,
    day_start: datetime,
    user_id: int,
) -> None:
    """就地回填每段的设备应用明细：段起止映射到本地小时窗口后按重叠摊销。

    人工段是**跨端容器**（「上床」里电脑手机都算）→ 合并两端；
    设备段本身就是**一台设备**的真实时段 → 只摊它自己那台设备的应用，
    否则同一时间窗内所有设备的应用会一起挂上来（电脑段显示手机上在玩什么）。
    """
    segments = list(manual_segments) + list(device_segments)
    if not segments:
        return
    windows = [
        (
            (seg.start - day_start).total_seconds() / 3600,
            (seg.end - day_start).total_seconds() / 3600,
        )
        for seg in segments
    ]
    usage = device_service.day_app_usage_for_windows(
        db,
        date,
        windows,
        user_id=user_id,
        device_ids=[None] * len(manual_segments)
        + [seg.event.device_id for seg in device_segments],
    )
    for seg, apps in zip(segments, usage):
        seg.device_apps = [SegmentApp(**a) for a in apps]


def _attach_device_events(
    segments, events, platform_by_device: dict[int, str]
) -> list[EventSegment]:
    """把 DEVICE 事件填进时间轴：

    - 落在某个人工段内 → 作为该段 device_activities（人工容器包含真实设备活动）
    - 未被任何人工作段覆盖 → 独立 device_segments（自动记录的真实时间段）
    """
    device_events = [
        e
        for e in events
        if (e.source or EventSource.MANUAL.value) == EventSource.DEVICE.value
    ]
    if not device_events:
        return []
    uncovered: list[EventSegment] = []
    for ev in sorted(device_events, key=lambda e: ensure_aware(e.timestamp)):
        ts = ensure_aware(ev.timestamp)
        end = ensure_aware(ev.ended_at) if ev.ended_at else ts
        if end <= ts:
            continue
        activity = DeviceActivity(
            platform=platform_by_device.get(ev.device_id, ""),
            category=ev.category or "other_online",
            label=ev.note,
            start=ts,
            end=end,
            seconds=int((end - ts).total_seconds()),
        )
        idx = _segment_index_at(segments, ts)
        if idx is not None:
            segments[idx].device_activities.append(activity)
        else:
            uncovered.append(
                EventSegment(
                    kind="device",
                    event=ev,
                    platform=platform_by_device.get(ev.device_id),
                    start=ts,
                    end=end,
                    duration_minutes=max(1, int((end - ts).total_seconds() // 60)),
                    end_boundary="explicit",
                    end_inferred=False,
                    fuzzy=False,
                )
            )
    return uncovered


def _assign_floatings(
    segments,
    thoughts: list[Thought],
    states: list[StateRecord],
) -> list[FloatingItem]:
    """想法/状态作为时间戳浮点，落到所在的时间段中。"""
    floatings: list[FloatingItem] = []
    for t in thoughts:
        ts = ensure_aware(t.timestamp)
        if _segment_index_at(segments, ts) is not None:
            floatings.append(FloatingItem(kind="thought", timestamp=ts, data=t))
    for s in states:
        ts = ensure_aware(s.timestamp)
        if _segment_index_at(segments, ts) is not None:
            floatings.append(FloatingItem(kind="state", timestamp=ts, data=s))
    floatings.sort(key=lambda f: f.timestamp)
    return floatings


def _segment_index_at(segments, ts: datetime) -> int | None:
    """返回 ts 落在哪个段的下标。落在段外（最早段之前/最晚段之后）→ None。

    浮点只显示在"已被事件覆盖"的时间范围里；事件开始前的想法不显示。
    """
    for i, seg in enumerate(segments):
        if seg.start <= ts < seg.end:
            return i
    return None
