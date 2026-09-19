"""实验服务：统计计算（指标聚合、有效运行时长、趋势判断）+ 数据自动聚合。

设计原则：统计只读实验数据（logs + status_events），不修改任何记录。
前端拿 ExperimentDetail 一次展示：设计 / 数据 / 统计 / 分析 / 结论。

数据自动聚合（aggregate_logs）：
- 指标可声明 source（event_duration:XXX / event_count:XXX / state_avg:focus）
- 按实验运行期间（started_at ~ ended_at/now）逐日聚合 events/state_records
- 写入 source="AUTO" 的数据点；每次聚合并发同一天同一指标（重算覆盖）
- 手动数据点（source=MANUAL）永不被覆盖
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from statistics import fmean

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.experiment import (
    Experiment,
    ExperimentLog,
    ExperimentStatus,
    ExperimentStatusEvent,
)
from app.models.state import StateRecord
from app.schemas.experiment import ExperimentStats, MetricStats, split_time_window
from app.services import day_hours_service, device_service, timeline_service
from app.utils import ensure_aware, parse_day_range

logger = logging.getLogger(__name__)

# 趋势判定：相对变化小于此比例视为 flat（避免小数噪声）
FLAT_RATIO = 0.05

# 聚合步长：一次最多覆盖多少天（防止超长实验卡死）
MAX_AGGREGATE_DAYS = 366

# 账户系统完成前固定单用户（与各 router 一致）


def _is_improved(trend: str, direction: str) -> bool | None:
    if direction == "neutral":
        return None
    if trend == "flat":
        return False
    return (direction == "up_good" and trend == "up") or (
        direction == "down_good" and trend == "down"
    )


def metric_stats(
    metrics: list[dict] | None, logs: list[ExperimentLog]
) -> list[MetricStats]:
    """按指标分组统计：n/均值/极值/首尾变化/前后半段/趋势。

    指标顺序：先按设计清单 metrics 的顺序，再补上清单外出现过数据的指标。
    """
    defs = {m.get("key"): m for m in (metrics or []) if m.get("key")}
    buckets: dict[str, list[ExperimentLog]] = {}
    for log in logs:
        buckets.setdefault(log.metric, []).append(log)

    ordered: list[str] = [k for k in defs if k in buckets]
    ordered += [k for k in buckets if k not in defs]

    result: list[MetricStats] = []
    for key in ordered:
        rows = sorted(buckets[key], key=lambda x: ensure_aware(x.timestamp))
        values = [r.value for r in rows]
        meta = defs.get(key, {})
        direction = meta.get("direction", "neutral")

        first, latest = values[0], values[-1]
        change = latest - first
        half = len(values) // 2
        first_half = fmean(values[:half]) if half else None
        second_half = fmean(values[half:]) if half else None

        if first_half is None or second_half is None:
            trend = "flat"
        else:
            base = max(abs(first_half), abs(second_half), 1e-9)
            if abs(second_half - first_half) <= FLAT_RATIO * base:
                trend = "flat"
            else:
                trend = "up" if second_half > first_half else "down"

        result.append(
            MetricStats(
                metric=key,
                unit=meta.get("unit"),
                count=len(values),
                mean=fmean(values),
                min=min(values),
                max=max(values),
                first=first,
                latest=latest,
                change=change,
                first_half_mean=first_half,
                second_half_mean=second_half,
                trend=trend,
                direction=direction,
                improved=_is_improved(trend, direction),
            )
        )
    return result


def compute_running_days(
    status_events: list[ExperimentStatusEvent], now: datetime | None = None
) -> tuple[float | None, float | None, float | None]:
    """由状态历史算 (有效运行天数, 暂停总天数, 总跨度天数)。

    算法：按时间顺序扫状态事件，累计处于各状态的时长；
    若当前仍 RUNNING/PAUSED，则累计到 now。
    """
    if not status_events:
        return None, None, None
    now = now or datetime.now(timezone.utc)
    events = sorted(status_events, key=lambda e: ensure_aware(e.created_at))

    start: datetime | None = None
    running = 0.0
    paused = 0.0
    current: str | None = None
    last_ts: datetime | None = None

    for ev in events:
        ts = ensure_aware(ev.created_at)
        if current is not None and last_ts is not None:
            delta = (ts - last_ts).total_seconds()
            if current == ExperimentStatus.RUNNING.value:
                running += delta
            elif current == ExperimentStatus.PAUSED.value:
                paused += delta
        if start is None:
            start = ts
        current = ev.to_status
        last_ts = ts

    if last_ts is not None:
        delta = (now - last_ts).total_seconds()
        if current == ExperimentStatus.RUNNING.value:
            running += delta
        elif current == ExperimentStatus.PAUSED.value:
            paused += delta

    day = 86400.0
    elapsed = (now - start).total_seconds() if start else None
    return running / day, paused / day, (elapsed / day if elapsed is not None else None)


def get(db: Session, exp_id: int, user_id: int) -> Experiment | None:
    """按 id 取实验（**带归属校验**：别人的 id 一律当作不存在）。"""
    return db.scalar(
        select(Experiment).where(
            Experiment.id == exp_id, Experiment.user_id == user_id
        )
    )


def list_for_user(db: Session, user_id: int) -> Sequence[Experiment]:
    """按最近更新排序的实验列表（router 与 Agent 工具共用同一顺序）。"""
    stmt = (
        select(Experiment)
        .where(Experiment.user_id == user_id)
        .order_by(Experiment.updated_at.desc())
    )
    return db.scalars(stmt).all()


def build_stats(db: Session, exp: Experiment, now: datetime | None = None) -> ExperimentStats:
    """组装实验统计：数据点统计 + 状态时长。"""
    logs = list(
        db.scalars(
            select(ExperimentLog)
            .where(
                ExperimentLog.experiment_id == exp.id,
                ExperimentLog.user_id == exp.user_id,
            )
            .order_by(ExperimentLog.timestamp.asc())
        ).all()
    )
    events = _load_status_events(db, exp)
    running_days, paused_days, elapsed_days = compute_running_days(events, now=now)
    return ExperimentStats(
        experiment_id=exp.id,
        total_logs=len(logs),
        metric_stats=metric_stats(exp.metrics, logs),
        running_days=running_days,
        paused_total_days=paused_days,
        days_elapsed=elapsed_days,
    )


# ── 数据自动聚合 ────────────────────────────────────────────


def _load_status_events(db: Session, exp: Experiment) -> list[ExperimentStatusEvent]:
    return list(
        db.scalars(
            select(ExperimentStatusEvent)
            .where(
                ExperimentStatusEvent.experiment_id == exp.id,
                ExperimentStatusEvent.user_id == exp.user_id,
            )
            .order_by(ExperimentStatusEvent.created_at.asc())
        ).all()
    )


def _agg_window(exp: Experiment, now: datetime | None = None) -> tuple[datetime, datetime]:
    """聚合窗口：started_at（含）~ ended_at（不含，未完则到 now）。"""
    now = now or datetime.now(timezone.utc)
    start = ensure_aware(exp.started_at) if exp.started_at else now
    end = ensure_aware(exp.ended_at) if exp.ended_at else now
    return start, end


def _aggregatable_metrics(metrics: list[dict] | None) -> list[dict]:
    return [
        m
        for m in (metrics or [])
        if m.get("source", "manual") != "manual" and m.get("key")
    ]


def _running_overlap_seconds(
    status_events: list[ExperimentStatusEvent],
    window_start: datetime,
    window_end: datetime,
    now: datetime,
) -> float | None:
    """窗口内实验处于 RUNNING 的总秒数（暂停期排除）。

    返回 None 表示无状态历史（旧实验）→ 视为整个窗口都在运行，
    保持兼容，避免误删历史数据点。
    """
    if not status_events:
        return None
    total = 0.0
    current: str | None = None
    last_ts: datetime | None = None
    for ev in status_events:
        ts = ensure_aware(ev.created_at)
        if current == ExperimentStatus.RUNNING.value and last_ts is not None:
            seg_start = max(last_ts, window_start)
            seg_end = min(ts, window_end)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds()
        current = ev.to_status
        last_ts = ts
    if current == ExperimentStatus.RUNNING.value and last_ts is not None:
        seg_start = max(last_ts, window_start)
        seg_end = min(now, window_end)
        if seg_end > seg_start:
            total += (seg_end - seg_start).total_seconds()
    return total


def _delete_auto_points_for_day(
    db: Session,
    exp: Experiment,
    day_start: datetime,
    day_end: datetime,
    metrics: list[dict],
) -> int:
    """清掉某日全部 AUTO 点（暂停日/无运行日）；MANUAL 点不动。"""
    keys = [m["key"] for m in metrics]
    if not keys:
        return 0
    rows = db.scalars(
        select(ExperimentLog).where(
            ExperimentLog.experiment_id == exp.id,
            ExperimentLog.metric.in_(keys),
            ExperimentLog.source == "AUTO",
            ExperimentLog.timestamp >= day_start,
            ExperimentLog.timestamp < day_end,
        )
    ).all()
    for row in rows:
        db.delete(row)
    return len(rows)


def prune_orphan_auto_points(db: Session, exp: Experiment) -> int:
    """清除指标被移除（或改为 manual）后遗留的孤儿 AUTO 点；MANUAL 点不动。

    聚合只重算「当前可聚合指标」，被删掉的指标其旧 AUTO 点不会自然消失，
    因此编辑指标后显式清理，避免统计里冒出清单外的幽灵指标。
    """
    keep = {m["key"] for m in _aggregatable_metrics(exp.metrics)}
    rows = db.scalars(
        select(ExperimentLog).where(
            ExperimentLog.experiment_id == exp.id,
            ExperimentLog.user_id == exp.user_id,
            ExperimentLog.source == "AUTO",
        )
    ).all()
    removed = 0
    for row in rows:
        if row.metric not in keep:
            db.delete(row)
            removed += 1
    return removed


def _day_value(
    db: Session,
    source: str,
    day_start: datetime,
    day_end: datetime,
    day_str: str,
    user_id: int,
    tz_offset: int = 0,
) -> float | None:
    """某天某 source 的聚合值；无数据返回 None。

    day_str 为该本地日 YYYY-MM-DD（设备使用数据按本地日字符串存储），
    `day_start` / `day_end` 是同一本地日的 UTC 区间（`parse_day_range(day_str, tz_offset)`）。

    source 语法：
    - event_duration:LEARNING_START  → 当天该类型事件总时长（分钟，按时间线分段规则）
    - event_count:EXERCISE_START      → 当天该类型事件次数
    - state_avg:focus                → 当天状态均值
    - usage_duration:微信             → 当天该应用使用时长（分钟，设备采集）
    - usage_platform:android|pc      → 当天该类设备使用总时长（分钟，设备采集）
    - usage_total                    → 当天全部设备使用总时长（分钟）
    - 上述 usage_* 可加 @起-止 时段后缀 → 只统计该小时窗口（需时段数据）
    - event_duration_category:video  → 当天设备采集的该行为大类时长（分钟，按小时摊销）
    - event_count_category:video     → 当天该行为大类设备段次数

    设备类口径（usage_* 与 category_*）与记忆页的小时视图同源，见 `day_hours_service`。
    `tz_offset` 必须与 `day_start`/`day_end` 同源：`category_*` 要按**同一本地日**
    重新切一遍 DEVICE 事件，漏传就会退化成「按 UTC 日切」——日界差一个时区偏移，
    深夜那几小时的大类会算到隔壁天去（usage_* 按本地日字符串取数，不受影响）。
    """
    base, window = split_time_window(source)

    if base.startswith("event_duration_category:"):
        cat = base.split(":", 1)[1]
        seconds = day_hours_service.category_seconds_for_day(
            db, day_str, cat, tz_offset=tz_offset, user_id=user_id
        )
        return seconds / 60.0 if seconds is not None else None

    if base.startswith("event_count_category:"):
        cat = base.split(":", 1)[1]
        return day_hours_service.category_count_for_day(
            db, day_str, cat, tz_offset=tz_offset, user_id=user_id
        )

    if base == "usage_total" or base.startswith(("usage_platform:", "usage_duration:")):
        app = base.split(":", 1)[1] if base.startswith("usage_duration:") else None
        platform = base.split(":", 1)[1] if base.startswith("usage_platform:") else None
        if window is not None:
            seconds = device_service.usage_seconds_for_window(
                db, day_str, window[0], window[1], app=app, platform=platform,
                user_id=user_id,
            )
        else:
            seconds = device_service.usage_seconds_for_day(
                db, day_str, app=app, platform=platform, user_id=user_id
            )
        return float(seconds) / 60.0 if seconds is not None else None

    if source.startswith("event_duration:"):
        etype = source.split(":", 1)[1]
        events = timeline_service.load_day_events(db, day_start, day_end, user_id)
        if not events:
            return None  # 当天完全没记录 → 无数据，而非 0
        segments = timeline_service.build_segments(events, day_start, day_end)
        total = sum(
            s.duration_minutes for s in segments if s.event.type == etype
        )
        return float(total)

    if source.startswith("event_count:"):
        etype = source.split(":", 1)[1]
        events = timeline_service.load_day_events(db, day_start, day_end, user_id)
        if not events:
            return None  # 当天完全没记录 → 无数据
        return float(len([e for e in events if e.type == etype]))

    if source.startswith("state_avg:"):
        field = source.split(":", 1)[1]
        if field not in ("energy", "focus", "irritation"):
            return None
        values = [
            getattr(s, field)
            for s in db.scalars(
                select(StateRecord).where(
                    StateRecord.user_id == user_id,
                    StateRecord.timestamp >= day_start,
                    StateRecord.timestamp < day_end,
                )
            ).all()
        ]
        return float(fmean(values)) if values else None

    return None


def _aggregate_one_day(
    db: Session,
    exp: Experiment,
    day_start: datetime,
    day_end: datetime,
    metrics: list[dict],
    *,
    day_str: str,
    status_events: list[ExperimentStatusEvent] | None = None,
    now: datetime | None = None,
    tz_offset: int = 0,
) -> tuple[int, int, int]:
    """重算某实验某本地日的数据点，返回 (created, updated, deleted)。

    暂停期排除：该日与 RUNNING 区间完全无重叠时不产点，并清掉旧 AUTO 点；
    MANUAL 点永不被触碰；当天无源数据的 AUTO 点也会被删除。
    """
    now = now or datetime.now(timezone.utc)
    if status_events is None:
        status_events = _load_status_events(db, exp)
    overlap = _running_overlap_seconds(status_events, day_start, day_end, now)
    if overlap is not None and overlap <= 0:
        # 当天完全未运行（暂停/未开始）→ 不产点
        return 0, 0, _delete_auto_points_for_day(db, exp, day_start, day_end, metrics)

    created = updated = deleted = 0
    for m in metrics:
        key = m["key"]
        value = _day_value(
            db,
            m.get("source", ""),
            day_start,
            day_end,
            day_str,
            exp.user_id,
            tz_offset,
        )
        existing = db.scalar(
            select(ExperimentLog).where(
                ExperimentLog.experiment_id == exp.id,
                ExperimentLog.metric == key,
                ExperimentLog.source == "AUTO",
                ExperimentLog.timestamp >= day_start,
                ExperimentLog.timestamp < day_end,
            )
        )
        if value is None:
            if existing is not None:
                db.delete(existing)  # 当天已无源数据 → 清掉旧聚合点
                deleted += 1
            continue
        if existing is not None:
            if existing.value != value:
                existing.value = value
                existing.note = f"自动聚合（{m.get('source')}）"
                updated += 1
        else:
            db.add(
                ExperimentLog(
                    user_id=exp.user_id,
                    experiment_id=exp.id,
                    metric=key,
                    value=value,
                    note=f"自动聚合（{m.get('source')}）",
                    timestamp=day_start,
                    source="AUTO",
                )
            )
            created += 1
    return created, updated, deleted


def aggregate_logs(
    db: Session,
    exp: Experiment,
    *,
    tz_offset: int = 0,
    now: datetime | None = None,
) -> dict:
    """把实验运行期间的 events/states 按天聚合为 AUTO 数据点。

    幂等：同一天同一指标重算覆盖（AUTO 点），手动点不动。
    返回 {created, updated, deleted, days, metrics}。
    """
    metrics = _aggregatable_metrics(exp.metrics)
    if not metrics:
        return {"created": 0, "updated": 0, "deleted": 0, "days": 0, "metrics": []}

    start, end = _agg_window(exp, now=now)
    # 按用户本地时区逐日：从 start 所在本地日到 end 所在本地日
    start_local = start.astimezone(timezone(timedelta(minutes=tz_offset)))
    end_local = end.astimezone(timezone(timedelta(minutes=tz_offset)))
    first_day = start_local.date()
    last_day = end_local.date()
    total_days = (last_day - first_day).days + 1
    if total_days > MAX_AGGREGATE_DAYS:
        first_day = last_day - timedelta(days=MAX_AGGREGATE_DAYS - 1)
        total_days = MAX_AGGREGATE_DAYS

    created = updated = deleted = 0
    days_covered = 0
    now = now or datetime.now(timezone.utc)
    status_events = _load_status_events(db, exp)

    for i in range(total_days):
        day = first_day + timedelta(days=i)
        day_start, day_end = parse_day_range(day.isoformat(), tz_offset)
        c, u, d = _aggregate_one_day(
            db,
            exp,
            day_start,
            day_end,
            metrics,
            day_str=day.isoformat(),
            status_events=status_events,
            now=now,
            tz_offset=tz_offset,
        )
        created += c
        updated += u
        deleted += d
        days_covered += 1

    db.commit()
    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "days": days_covered,
        "metrics": [m["key"] for m in metrics],
    }


def _experiments_covering(
    db: Session, day_start: datetime, day_end: datetime, user_id: int
) -> list[Experiment]:
    """运行窗口与该本地日有交集的实验（已开始；含已完成的，保持历史数据一致）。"""
    return list(
        db.scalars(
            select(Experiment).where(
                Experiment.user_id == user_id,
                Experiment.started_at.is_not(None),
                Experiment.started_at < day_end,
                or_(Experiment.ended_at.is_(None), Experiment.ended_at > day_start),
            )
        ).all()
    )


def aggregate_day(
    db: Session, day: str, *, user_id: int, tz_offset: int = 0
) -> dict:
    """复盘时自动同步：把某本地日的数据写入**该账号**所有覆盖该日的实验。

    幂等；返回 {experiments, created, updated, deleted}。
    用于「生成日/周/月复盘时，把对应数据同步到实验数据点」。
    """
    day_start, day_end = parse_day_range(day, tz_offset)
    total = {"experiments": 0, "created": 0, "updated": 0, "deleted": 0}
    now = datetime.now(timezone.utc)
    for exp in _experiments_covering(db, day_start, day_end, user_id):
        metrics = _aggregatable_metrics(exp.metrics)
        if not metrics:
            continue
        c, u, d = _aggregate_one_day(
            db,
            exp,
            day_start,
            day_end,
            metrics,
            day_str=day,
            status_events=_load_status_events(db, exp),
            now=now,
            tz_offset=tz_offset,
        )
        total["experiments"] += 1
        total["created"] += c
        total["updated"] += u
        total["deleted"] += d
    db.commit()
    return total
