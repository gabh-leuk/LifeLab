"""行为分段服务：设备会话 → 行为时间段 → 真实 DEVICE 事件。

设备使用是**真实测量数据**（非推测）：
- 采集器上报精确前台会话（device_app_sessions），这里按「同设备 + 同类别 + 间隔小」合并成段
- 与人工记录判重：**同类重叠的人工优先**（删设备段）；不同类保留（大时间段包含设备活动）
- 幂等：同 (device, 本地日) 先删旧 DEVICE 事件再重建，重复导入不会累积

无会话时降级用 hourly 近似（整点起，按 seconds 推结束）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.behavior_categories import (
    RuleSet,
    classify_app,
    event_type_category,
    resolve_label,
)
from app.models.device import Device
from app.models.device_usage import DeviceAppSession, DeviceUsageHourly
from app.models.event import Event, EventSource, EventType
from app.services import timeline_service
from app.services.app_rule_service import load_rules
from app.utils import ensure_aware, parse_day_range

# 相邻同类会话间隔小于此值则并成一段
MERGE_GAP_SECONDS = 300
# 合并后短于此值的段视为噪声丢弃
MIN_SEGMENT_SECONDS = 60
# 区间重算的天数上限（防误传超大区间把请求拖死）
MAX_REBUILD_DAYS = 90


@dataclass
class SessionLite:
    device_id: int
    platform: str
    app: str
    label: str | None
    category: str
    start: datetime
    end: datetime


@dataclass
class BehaviorSegment:
    device_id: int
    platform: str
    category: str
    app: str
    label: str | None
    start: datetime
    end: datetime
    seconds: int
    confidence: float
    apps: list[dict] = field(default_factory=list)


def _classify(app: str, label: str | None, rules: RuleSet | None) -> str:
    return classify_app(app, label, rules)


def load_sessions(
    db: Session,
    date: str,
    device_id: int,
    user_id: int,
    rules: RuleSet | None = None,
) -> list[SessionLite]:
    """该设备某本地日的精确会话（按开始时间升序），**按当前规则重新判类**。

    刻意不用库里存的 `category`：那是上报当时的快照，用户改了规则之后它就是
    陈旧缓存——重建时必须以当前规则为准，否则改规则对历史完全无效。
    """
    if rules is None:
        rules = load_rules(db, user_id)
    rows = db.scalars(
        select(DeviceAppSession)
        .where(
            DeviceAppSession.user_id == user_id,
            DeviceAppSession.device_id == device_id,
            DeviceAppSession.date == date,
        )
        .order_by(DeviceAppSession.start_ts.asc())
    ).all()
    out: list[SessionLite] = []
    for r in rows:
        out.append(
            SessionLite(
                device_id=r.device_id,
                platform=r.platform,
                app=r.app,
                label=r.label,
                category=_classify(r.app, r.label, rules),
                start=ensure_aware(r.start_ts),
                end=ensure_aware(r.end_ts),
            )
        )
    return out


def sessions_from_hourly(
    db: Session,
    date: str,
    device: Device,
    day_start: datetime,
    user_id: int,
    rules: RuleSet | None = None,
) -> list[SessionLite]:
    """无精确会话时的降级：把 hourly 行近似成会话（整点起，按 seconds 推结束）。

    小时桶不知道小时内分布，只是让功能在旧数据上也能跑；精度到整点。
    """
    if rules is None:
        rules = load_rules(db, user_id)
    rows = db.scalars(
        select(DeviceUsageHourly)
        .where(
            DeviceUsageHourly.user_id == user_id,
            DeviceUsageHourly.device_id == device.id,
            DeviceUsageHourly.date == date,
        )
        .order_by(DeviceUsageHourly.hour.asc())
    ).all()
    out: list[SessionLite] = []
    for r in rows:
        start = day_start + timedelta(hours=r.hour)
        end = start + timedelta(seconds=min(3600, max(0, r.seconds)))
        if end <= start:
            continue
        out.append(
            SessionLite(
                device_id=device.id,
                platform=device.platform,
                app=r.app,
                label=r.label,
                category=_classify(r.app, r.label, rules),
                start=start,
                end=end,
            )
        )
    return out


def segment_sessions(
    sessions: list[SessionLite],
    day_start: datetime | None = None,
    day_end: datetime | None = None,
) -> list[BehaviorSegment]:
    """同设备、同大类、间隔小的连续会话 → 行为段。"""
    if not sessions:
        return []
    ordered = sorted(sessions, key=lambda s: s.start)
    segments: list[BehaviorSegment] = []

    cur: list[SessionLite] = [ordered[0]]
    for s in ordered[1:]:
        prev = cur[-1]
        same_device = s.device_id == prev.device_id
        same_cat = s.category == prev.category
        gap = (s.start - prev.end).total_seconds()
        if same_device and same_cat and gap <= MERGE_GAP_SECONDS:
            cur.append(s)
        else:
            segments.append(_materialize(cur))
            cur = [s]
    segments.append(_materialize(cur))

    # 裁剪到当日边界并丢弃过短段
    result: list[BehaviorSegment] = []
    for seg in segments:
        if day_start is not None and seg.start < day_start:
            seg.start = day_start
        if day_end is not None and seg.end > day_end:
            seg.end = day_end
        if seg.end <= seg.start:
            continue
        seg.seconds = int((seg.end - seg.start).total_seconds())
        if seg.seconds < MIN_SEGMENT_SECONDS:
            continue
        result.append(seg)
    return result


def _materialize(group: list[SessionLite]) -> BehaviorSegment:
    by_app: dict[str, dict] = {}
    cat_seconds: dict[str, int] = {}
    total = 0
    for s in group:
        sec = max(0, int((s.end - s.start).total_seconds()))
        total += sec
        cat_seconds[s.category] = cat_seconds.get(s.category, 0) + sec
        item = by_app.get(s.app)
        if item is None:
            by_app[s.app] = {"app": s.app, "label": s.label, "seconds": sec}
        else:
            item["seconds"] += sec
            if item["label"] is None:
                item["label"] = s.label

    dominant_cat = max(cat_seconds, key=lambda k: cat_seconds[k])
    apps = sorted(by_app.values(), key=lambda a: a["seconds"], reverse=True)
    dominant_app = apps[0]
    start = group[0].start
    end = max(s.end for s in group)
    confidence = (dominant_app["seconds"] / total) if total else 0.5
    return BehaviorSegment(
        device_id=group[0].device_id,
        platform=group[0].platform,
        category=dominant_cat,
        app=dominant_app["app"],
        label=dominant_app.get("label"),
        start=start,
        end=end,
        seconds=int((end - start).total_seconds()),
        confidence=round(min(1.0, max(0.0, confidence)), 3),
        apps=apps,
    )


def _subtract_same_category(
    segments: list[BehaviorSegment],
    manual_intervals: list[tuple[datetime, datetime, str]],
) -> list[BehaviorSegment]:
    """剔除与「同类人工事件」重叠的部分（人工优先）；不同类不削。"""
    if not manual_intervals:
        return segments
    result: list[BehaviorSegment] = []
    for seg in segments:
        parts: list[tuple[datetime, datetime]] = [(seg.start, seg.end)]
        for m_start, m_end, m_cat in manual_intervals:
            if m_cat != seg.category or m_end <= m_start:
                continue
            next_parts: list[tuple[datetime, datetime]] = []
            for p_start, p_end in parts:
                if m_end <= p_start or m_start >= p_end:
                    next_parts.append((p_start, p_end))
                    continue
                if m_start > p_start:
                    next_parts.append((p_start, m_start))
                if m_end < p_end:
                    next_parts.append((m_end, p_end))
            parts = next_parts
            if not parts:
                break
        for p_start, p_end in parts:
            if (p_end - p_start).total_seconds() < MIN_SEGMENT_SECONDS:
                continue
            result.append(
                BehaviorSegment(
                    device_id=seg.device_id,
                    platform=seg.platform,
                    category=seg.category,
                    app=seg.app,
                    label=seg.label,
                    start=p_start,
                    end=p_end,
                    seconds=int((p_end - p_start).total_seconds()),
                    confidence=seg.confidence,
                    apps=seg.apps,
                )
            )
    return result


def _manual_intervals(
    db: Session, day_start: datetime, day_end: datetime, user_id: int
) -> list[tuple[datetime, datetime, str]]:
    """当天人工事件的有效区间（结束按时间线规则推断）+ 大类，用于判重。"""
    events = [
        e
        for e in timeline_service.load_day_events(db, day_start, day_end, user_id)
        if (e.source or EventSource.MANUAL.value) != EventSource.DEVICE.value
    ]
    segments = timeline_service.build_segments(events, day_start, day_end)
    intervals: list[tuple[datetime, datetime, str]] = []
    for s in segments:
        cat = s.event.category or event_type_category(s.event.type)
        if cat:
            intervals.append((s.start, s.end, cat))
    return intervals


def _delete_device_events(
    db: Session, device_id: int, day_start: datetime, day_end: datetime, user_id: int
) -> int:
    rows = db.scalars(
        select(Event).where(
            Event.user_id == user_id,
            Event.source == EventSource.DEVICE.value,
            Event.device_id == device_id,
            Event.timestamp >= day_start,
            Event.timestamp < day_end,
        )
    ).all()
    for row in rows:
        db.delete(row)
    return len(rows)


def _write_segments(
    db: Session,
    segments: list[BehaviorSegment],
    device: Device,
    rules: RuleSet | None = None,
) -> int:
    for seg in segments:
        tags = ",".join(
            resolve_label(a["app"], a.get("label"), rules) or a["app"]
            for a in seg.apps[:3]
        )[:255]
        db.add(
            Event(
                user_id=device.user_id,
                timestamp=seg.start,
                type=EventType.DEVICE_ACTIVITY.value,
                source=EventSource.DEVICE.value,
                confidence=seg.confidence,
                category=seg.category,
                device_id=device.id,
                note=resolve_label(seg.app, seg.label, rules) or seg.app,
                tags_raw=tags or None,
                ended_at=seg.end,
            )
        )
    return len(segments)


def rebuild_device_events(
    db: Session,
    date: str,
    device: Device,
    *,
    tz_offset: int = 0,
    rules: RuleSet | None = None,
) -> dict:
    """按当前会话/小时数据重建该设备某本地日的 DEVICE 事件（幂等）。

    返回 {deleted, created, segments}。
    """
    day_start, day_end = parse_day_range(date, tz_offset)
    user_id = device.user_id
    if rules is None:
        rules = load_rules(db, user_id)
    deleted = _delete_device_events(db, device.id, day_start, day_end, user_id)

    sessions = load_sessions(db, date, device.id, user_id, rules=rules)
    if not sessions:
        sessions = sessions_from_hourly(
            db, date, device, day_start, user_id, rules=rules
        )

    segments = segment_sessions(sessions, day_start, day_end)
    intervals = _manual_intervals(db, day_start, day_end, user_id)
    segments = _subtract_same_category(segments, intervals)
    created = _write_segments(db, segments, device, rules)
    db.commit()
    return {"deleted": deleted, "created": created, "segments": len(segments)}


def rebuild_for_date(
    db: Session,
    date: str,
    *,
    user_id: int,
    tz_offset: int = 0,
    rules: RuleSet | None = None,
) -> dict:
    """重算某账号所有设备在某日的 DEVICE 事件（改分类规则/补数据后手动触发）。"""
    devices = db.scalars(
        select(Device).where(Device.user_id == user_id)
    ).all()
    if rules is None:
        rules = load_rules(db, user_id)
    total = {"devices": 0, "deleted": 0, "created": 0}
    for device in devices:
        r = rebuild_device_events(db, date, device, tz_offset=tz_offset, rules=rules)
        total["devices"] += 1
        total["deleted"] += r["deleted"]
        total["created"] += r["created"]
    return total


def rebuild_range(
    db: Session,
    start: str,
    end: str,
    *,
    user_id: int,
    tz_offset: int = 0,
    max_days: int = MAX_REBUILD_DAYS,
) -> dict:
    """按日期区间重算（含两端）该账号所有设备的 DEVICE 事件：改规则后一次补齐历史。

    - 规则集**只加载一次**，整段复用（重建中途不会变）
    - 逐日调用幂等的 `rebuild_for_date`；`max_days` 是防止误传超大区间
    """
    days = plan_range(start, end, max_days=max_days)
    rules = load_rules(db, user_id)
    total = {"days": 0, "devices": 0, "deleted": 0, "created": 0}
    for day in days:
        r = rebuild_for_date(
            db, day, user_id=user_id, tz_offset=tz_offset, rules=rules
        )
        total["days"] += 1
        total["devices"] += r["devices"]
        total["deleted"] += r["deleted"]
        total["created"] += r["created"]
    return total


def plan_range(
    start: str, end: str, *, max_days: int = MAX_REBUILD_DAYS
) -> list[str]:
    """校验并展开 [start, end]（含两端）为本地日字符串列表。

    ValueError：格式不是 YYYY-MM-DD / end 早于 start / 超过 max_days 天。
    路由层先用它校验（转 422），后台任务里 `rebuild_range` 再用一次。
    """
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError as e:
        raise ValueError("start/end 必须是 YYYY-MM-DD") from e
    if last < first:
        raise ValueError("end 不能早于 start")
    days = [
        (first + timedelta(days=i)).isoformat()
        for i in range((last - first).days + 1)
    ]
    if len(days) > max_days:
        raise ValueError(f"区间最多 {max_days} 天，收到 {len(days)} 天")
    return days

