"""日级「本地小时」聚合：一天 → 若干个小时桶。

时间轴的小时视图（`TimelineResponse.device_hours`）与实验指标取值都读这里，
保证同一批设备数据在不同模块算出同一个数。

两个数据源各司其职：

- `DeviceUsageHourly`（按小时采集、天然按小时分桶）→ 秒数与应用明细，**对账基准**
- DEVICE 事件（由会话切出的段）→ 行为大类与活动明细

三个曾经的口径坑，本模块统一修掉：

1. 小时视图只由「未被人工段覆盖」的设备段构建，而实验的 `category_seconds_between`
   统计**全部** DEVICE 事件 → 落在人工段内的设备活动计入实验、却从时间轴消失。
   现在两边都取全部事件。
2. 跨小时的段整段记给**起点小时**，而同一页的应用明细按重叠摊销 → 同一小时里
   「大类秒数」与「应用秒数」不同基准。现在一律按重叠摊销。
3. 秒数取自事件段时长、实验取自 `DeviceUsageHourly` → 两边永远对不上账。
   现在秒数一律取自 `DeviceUsageHourly`。
"""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.event import EventSource
from app.schemas.timeline import DeviceActivity, DeviceHour, SegmentApp
from app.services import device_service, timeline_service
from app.utils import ensure_aware, parse_day_range

# 每小时展示的应用条数上限（秒数仍按全量计入对账）
DEFAULT_TOP_APPS = 4

# DEVICE 事件缺失大类时的兜底（与设备采集侧的默认一致）
UNKNOWN_CATEGORY = "other_online"


def build_day_hours(
    db: Session,
    date: str,
    day_start: datetime,
    day_end: datetime,
    *,
    user_id: int,
    top: int = DEFAULT_TOP_APPS,
    with_apps: bool = True,
) -> list[DeviceHour]:
    """把一天的设备数据聚合成「本地小时」桶，只返回有数据的小时。

    `date` 是该本地日 YYYY-MM-DD（按小时采集的数据按它存储）；
    `day_start` / `day_end` 是同一本地日对应的 UTC 区间（见 `utils.parse_day_range`）。
    两者必须指向同一天，否则秒数（按 date 查）与事件（按区间查）会错位。

    `seconds` 的口径：优先取按小时采集的秒数（与实验 `usage_total` 对账）；
    该小时没有采集数据时，退回该小时设备活动段的摊销秒数 —— 采集器只发会话
    不发按小时用量时，不至于在一条可见的活动旁显示「0 秒」。

    `with_apps=False` 跳过应用明细的查询与打标（指标取值只关心秒数与大类）。
    """
    seconds_by_hour = device_service.hourly_seconds_for_day(db, date, user_id=user_id)
    apps_by_hour = (
        {
            slot["hour"]: [SegmentApp(**a) for a in slot["apps"]]
            for slot in device_service.hourly_apps_for_day(
                db, date, top=top, user_id=user_id
            )
        }
        if with_apps
        else {}
    )
    platform_by_device = {
        d.id: d.platform for d in device_service.list_devices(db, user_id)
    }

    cat_seconds: dict[int, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    activities: dict[int, list[DeviceActivity]] = defaultdict(list)

    for ev in _device_events(db, day_start, day_end, user_id):
        start = ensure_aware(ev.timestamp)
        end = ensure_aware(ev.ended_at) if ev.ended_at else start
        if end <= start:
            continue
        start_h = (start - day_start).total_seconds() / 3600.0
        end_h = (end - day_start).total_seconds() / 3600.0
        category = ev.category or UNKNOWN_CATEGORY

        # 段跨小时时按重叠摊销；末点正好落在整点则不算进下一小时。
        first_hour = max(0, min(23, int(start_h)))
        last_hour = max(0, min(23, int(end_h - 1e-9)))
        for hour in range(first_hour, last_hour + 1):
            overlap = min(end_h, hour + 1.0) - max(start_h, float(hour))
            if overlap > 0:
                cat_seconds[hour][category] += overlap * 3600.0

        # 活动明细整段挂在起点小时（段是一个视觉单位，不切开）
        activities[first_hour].append(
            DeviceActivity(
                platform=platform_by_device.get(ev.device_id, ""),
                category=category,
                label=ev.note,
                start=start,
                end=end,
                seconds=int((end - start).total_seconds()),
            )
        )

    buckets: list[DeviceHour] = []
    for hour in sorted(set(seconds_by_hour) | set(apps_by_hour) | set(cat_seconds)):
        cats = cat_seconds.get(hour) or {}
        hour_activities = activities.get(hour, [])
        seconds = seconds_by_hour.get(hour, 0) or round(sum(cats.values()))
        buckets.append(
            DeviceHour(
                hour=hour,
                # 桶的起止就是该本地小时的边界，不随活动范围收缩
                start=day_start + timedelta(hours=hour),
                end=day_start + timedelta(hours=hour + 1),
                seconds=seconds,
                apps=apps_by_hour.get(hour, []),
                categories=sorted(cats, key=lambda c: cats[c], reverse=True),
                category_seconds={c: round(v) for c, v in cats.items()},
                activities=hour_activities,
            )
        )
    return buckets


def _day_hours(db: Session, date: str, tz_offset: int, user_id: int) -> list[DeviceHour]:
    day_start, day_end = parse_day_range(date, tz_offset)
    return build_day_hours(
        db, date, day_start, day_end, user_id=user_id, with_apps=False
    )


def category_seconds_for_day(
    db: Session,
    date: str,
    category: str,
    *,
    tz_offset: int = 0,
    user_id: int,
) -> float | None:
    """某本地日某行为大类的秒数（按小时摊销，超出当日部分不计）；无设备活动 → None。"""
    hours = _day_hours(db, date, tz_offset, user_id)
    if not any(h.activities for h in hours):
        return None
    return float(sum(h.category_seconds.get(category, 0) for h in hours))


def category_totals_for_day(
    db: Session,
    date: str,
    *,
    tz_offset: int = 0,
    user_id: int,
) -> dict[str, int]:
    """某本地日**全部**行为大类的秒数合计（按小时摊销）。

    周复盘要在一行里看清"那天设备都花在哪"，逐大类各调一次
    `category_seconds_for_day` 会重复聚合 14 遍，所以这里一次算完。
    无任何设备活动 → 空字典（调用方据此决定不渲染设备段）。
    """
    totals: dict[str, float] = defaultdict(float)
    for h in _day_hours(db, date, tz_offset, user_id):
        for category, seconds in h.category_seconds.items():
            totals[category] += seconds
    return {c: round(v) for c, v in totals.items() if v > 0}


def category_count_for_day(
    db: Session,
    date: str,
    category: str,
    *,
    tz_offset: int = 0,
    user_id: int,
) -> float | None:
    """某本地日该行为大类的设备活动段条数；无设备活动 → None。"""
    activities = [a for h in _day_hours(db, date, tz_offset, user_id) for a in h.activities]
    if not activities:
        return None
    return float(sum(1 for a in activities if a.category == category))


def _device_events(db: Session, day_start: datetime, day_end: datetime, user_id: int):
    return [
        e
        for e in timeline_service.load_day_events(db, day_start, day_end, user_id)
        if (e.source or EventSource.MANUAL.value) == EventSource.DEVICE.value
    ]
