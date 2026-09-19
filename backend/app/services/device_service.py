"""设备与采集服务：设备 token 认证 + 使用时长摄取/查询。

鉴权模型（轻量、为将来用户系统留位）：
- 每台设备创建时生成随机 token，库中只存 sha256
- 采集器请求带 X-Device-Token 头；服务端 hash 后查 devices 表
- user_id 从设备行读取，不再依赖全局常量
"""

import secrets
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.behavior_categories import RuleSet, classify_app, resolve_label
from app.models.device import Device
from app.models.device_usage import (
    DeviceAppSession,
    DeviceUsageHourly,
)
from app.models.event import Event, EventSource
from app.schemas.device import UsageHourlyEntry, UsageSessionEntry
from app.security import hash_token
from app.services.app_rule_service import load_rules

# PC 采集器把浏览器站点记为 `web:<域名>` 的普通 usage 行，与浏览器进程行并存。
# 无 app 过滤的聚合取数（usage_total / usage_platform:*）须排除这些细分行，
# 否则会与浏览器进程行重复计总时长；站点数据只经显式 usage_duration:web:<域名> 露出。
WEB_PREFIX = "web:"


def _app_selected(row_app: str, app: str | None) -> bool:
    """app 为 None 时取「进程级」总量：排除 web:* 站点细分行；指定 app 时精确匹配。"""
    if app is not None:
        return row_app == app
    return not row_app.startswith(WEB_PREFIX)


def _hash(token: str) -> str:
    return hash_token(token)


def create_device(db: Session, *, name: str, platform: str, user_id: int) -> tuple[Device, str]:
    """创建设备并返回 (device, 明文 token)。明文只在本次调用出现。"""
    token = secrets.token_urlsafe(32)
    device = Device(
        user_id=user_id,
        name=name.strip(),
        platform=platform,
        token_hash=_hash(token),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device, token


def get_device_by_token(db: Session, token: str) -> Device | None:
    return db.scalar(
        select(Device).where(Device.token_hash == _hash(token))
    )


def list_devices(db: Session, user_id: int) -> Sequence[Device]:
    return db.scalars(
        select(Device).where(Device.user_id == user_id).order_by(Device.id)
    ).all()


def delete_device(db: Session, device_id: int, user_id: int) -> bool:
    device = db.scalar(
        select(Device).where(Device.id == device_id, Device.user_id == user_id)
    )
    if device is None:
        return False
    for model in (DeviceUsageHourly, DeviceAppSession):
        for row in db.scalars(
            select(model).where(model.device_id == device.id)
        ).all():
            db.delete(row)
    # 该设备自动生成的 DEVICE 事件也一并清理（避免留下无源时间轴段）
    for row in db.scalars(
        select(Event).where(
            Event.user_id == user_id,
            Event.source == EventSource.DEVICE.value,
            Event.device_id == device.id,
        )
    ).all():
        db.delete(row)
    db.delete(device)
    db.commit()
    return True


def touch_device(db: Session, device: Device) -> None:
    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()


def ingest_usage_hourly(
    db: Session, device: Device, date: str, entries: list[UsageHourlyEntry]
) -> tuple[int, int]:
    """按 (device, date) 整体替换当天小时记录，返回 (received, replaced)。

    同一 (hour, app) 多条取总和并 clamp 到 3600（一小时上限）。
    """
    old_rows = db.scalars(
        select(DeviceUsageHourly).where(
            DeviceUsageHourly.user_id == device.user_id,
            DeviceUsageHourly.device_id == device.id,
            DeviceUsageHourly.date == date,
        )
    ).all()
    replaced = len(old_rows)
    for row in old_rows:
        db.delete(row)
    if old_rows:
        db.flush()  # 先落删除，避免同日同 (hour, app) 唯一约束在插入时冲突

    merged: dict[tuple[int, str], UsageHourlyEntry] = {}
    for entry in entries:
        app = entry.app.strip()
        if not app:
            continue
        key = (entry.hour, app)
        if key in merged:
            prev = merged[key]
            merged[key] = UsageHourlyEntry(
                app=app,
                label=entry.label or prev.label,
                hour=entry.hour,
                seconds=min(3600, prev.seconds + entry.seconds),
                switches=min(100000, prev.switches + entry.switches),
            )
        else:
            merged[key] = entry.model_copy(
                update={"app": app, "seconds": min(3600, entry.seconds)}
            )

    for entry in merged.values():
        db.add(
            DeviceUsageHourly(
                user_id=device.user_id,
                device_id=device.id,
                date=date,
                hour=entry.hour,
                app=entry.app,
                label=entry.label,
                seconds=entry.seconds,
                switches=entry.switches,
            )
        )
    touch_device(db, device)
    return len(merged), replaced


def ingest_sessions(
    db: Session, device: Device, date: str, entries: list[UsageSessionEntry]
) -> tuple[int, int]:
    """按 (device, date) 整体替换当天的精确会话，返回 (received, replaced)。

    同一 (app, start) 多条取并集（end 取最大、seconds 取最大），避免唯一约束冲突。
    类别由服务端映射后回填（采集端不需要懂分类）；存的是**当时**的判定结果，
    重建时不会直接采信（见 behavior_service.load_sessions）。
    """
    rules = load_rules(db, device.user_id)
    old_rows = db.scalars(
        select(DeviceAppSession).where(
            DeviceAppSession.user_id == device.user_id,
            DeviceAppSession.device_id == device.id,
            DeviceAppSession.date == date,
        )
    ).all()
    replaced = len(old_rows)
    for row in old_rows:
        db.delete(row)
    if old_rows:
        db.flush()

    merged: dict[tuple[str, int], UsageSessionEntry] = {}
    for entry in entries:
        app = entry.app.strip()
        if not app or entry.end_ms <= entry.start_ms:
            continue
        key = (app, entry.start_ms)
        prev = merged.get(key)
        if prev is None:
            merged[key] = entry.model_copy(update={"app": app})
        else:
            merged[key] = UsageSessionEntry(
                app=app,
                label=entry.label or prev.label,
                start_ms=prev.start_ms,
                end_ms=max(prev.end_ms, entry.end_ms),
            )

    for entry in merged.values():
        start = datetime.fromtimestamp(entry.start_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(entry.end_ms / 1000, tz=timezone.utc)
        db.add(
            DeviceAppSession(
                user_id=device.user_id,
                device_id=device.id,
                platform=device.platform,
                date=date,
                app=entry.app,
                label=entry.label,
                category=classify_app(entry.app, entry.label, rules),
                start_ts=start,
                end_ts=end,
                seconds=min(86400, int((end - start).total_seconds())),
            )
        )
    touch_device(db, device)
    return len(merged), replaced


def _usage_devices(db: Session, platform: str | None, user_id: int) -> list[Device]:
    stmt = select(Device).where(Device.user_id == user_id)
    if platform is not None:
        stmt = stmt.where(Device.platform == platform)
    return list(db.scalars(stmt).all())


def usage_hourly_for_day(
    db: Session, date: str, user_id: int
) -> list[DeviceUsageHourly]:
    return list(
        db.scalars(
            select(DeviceUsageHourly)
            .where(
                DeviceUsageHourly.user_id == user_id,
                DeviceUsageHourly.date == date,
            )
            .order_by(DeviceUsageHourly.hour.asc(), DeviceUsageHourly.seconds.desc())
        ).all()
    )


def _day_hourly_rows(
    db: Session, date: str, device_ids: list[int], user_id: int
) -> list[DeviceUsageHourly]:
    if not device_ids:
        return []
    return list(
        db.scalars(
            select(DeviceUsageHourly).where(
                DeviceUsageHourly.user_id == user_id,
                DeviceUsageHourly.device_id.in_(device_ids),
                DeviceUsageHourly.date == date,
            )
        ).all()
    )


def _apps_in_window(
    rows: Sequence[DeviceUsageHourly],
    platform_by_device: dict[int, str],
    start_h: float,
    end_h: float,
    *,
    top: int,
    rules: RuleSet | None = None,
    device_id: int | None = None,
) -> list[dict]:
    """把 hourly 行摊销到本地小时窗口 [start_h, end_h)，按 (platform, app) 取 Top N。

    设备数据只有小时桶、不知道小时内分布：按窗口与该桶的重叠比例摊销
    （contrib = seconds * overlap，overlap 单位是小时，整点命中即 1.0）。
    排除 web:* 细分行（避免与浏览器进程行重复计总时长）。
    多设备同一 (hour, app) 按 platform 分开，避免把两端同名应用并成一条。
    `app` 保持采集到原名（规则编辑要用它做匹配值），展示名走 `label`。

    `device_id` 给了就只摊那台设备的行（设备段用——段本来就是一台设备的），
    给 None 表示窗口内所有设备一起摊（人工段是跨端容器）。
    """
    if end_h <= start_h:
        return []
    acc: dict[tuple[str, str], dict] = {}
    for r in rows:
        if device_id is not None and r.device_id != device_id:
            continue
        if not _app_selected(r.app, None):
            continue
        overlap = max(0.0, min(end_h, float(r.hour) + 1) - max(start_h, float(r.hour)))
        if overlap <= 0:
            continue
        platform = platform_by_device.get(r.device_id, "")
        cur = acc.get((platform, r.app))
        if cur is None:
            acc[(platform, r.app)] = {
                "platform": platform,
                "app": r.app,
                "label": resolve_label(r.app, r.label, rules),
                "seconds": r.seconds * overlap,
                "category": classify_app(r.app, r.label, rules),
            }
        else:
            cur["seconds"] += r.seconds * overlap
            if cur["label"] is None:
                cur["label"] = resolve_label(r.app, r.label, rules)
    out = sorted(acc.values(), key=lambda a: a["seconds"], reverse=True)[:top]
    for a in out:
        a["seconds"] = round(a["seconds"])
    return out


def hourly_apps_for_day(
    db: Session, date: str, *, top: int = 4, user_id: int
) -> list[dict]:
    """某日按小时的应用明细（每时段 Top N），仅含有数据的时段。

    供日复盘：不做摊销，直接按 hour 分组；排除 web:* 细分行。
    """
    devices = _usage_devices(db, None, user_id)
    if not devices:
        return []
    platform_by_device = {d.id: d.platform for d in devices}
    rows = _day_hourly_rows(db, date, [d.id for d in devices], user_id)
    rules = load_rules(db, user_id)
    by_hour: dict[int, list[DeviceUsageHourly]] = {}
    for r in rows:
        by_hour.setdefault(r.hour, []).append(r)
    result: list[dict] = []
    for hour in sorted(by_hour):
        apps = _apps_in_window(
            by_hour[hour], platform_by_device, hour, hour + 1, top=top, rules=rules
        )
        if apps:
            result.append({"hour": hour, "apps": apps})
    return result


def day_app_usage_for_windows(
    db: Session,
    date: str,
    windows: Sequence[tuple[float, float]],
    *,
    top: int = 4,
    user_id: int,
    device_ids: Sequence[int | None] | None = None,
) -> list[list[dict]]:
    """批量：每个本地小时窗口 [start_h, end_h) 的应用明细（Top N），与入参对齐。

    供时间轴：段起止先转成本地小时浮点（相对当日本地 00:00），一次加载 hourly 行、内存摊销。

    `device_ids` 与 `windows` 等长，逐窗口给设备过滤：某位给 id 就只摊那台设备
    （设备段——段本身就是一台设备），给 None 表示合并窗口内所有设备（人工段）。
    不传则全部窗口都不过滤。
    """
    devices = _usage_devices(db, None, user_id)
    if not devices:
        return [[] for _ in windows]
    platform_by_device = {d.id: d.platform for d in devices}
    rows = _day_hourly_rows(db, date, [d.id for d in devices], user_id)
    if not rows:
        return [[] for _ in windows]
    rules = load_rules(db, user_id)
    filters = list(device_ids) if device_ids is not None else [None] * len(windows)
    return [
        _apps_in_window(
            rows,
            platform_by_device,
            start_h,
            end_h,
            top=top,
            rules=rules,
            device_id=filters[i],
        )
        for i, (start_h, end_h) in enumerate(windows)
    ]


def usage_seconds_for_day(
    db: Session,
    date: str,
    app: str | None = None,
    platform: str | None = None,
    *,
    user_id: int,
) -> int | None:
    """某本地日的使用总秒数；当天无任何记录返回 None。

    过滤维度可组合：app（应用标识精确匹配）、platform（android/pc）。
    注意 None 与 0 的区别：当天有数据但没匹配到 = 0；当天无任何采集 = None。
    """
    devices = _usage_devices(db, platform, user_id)
    if not devices:
        return None
    device_ids = [d.id for d in devices]
    hourly = _day_hourly_rows(db, date, device_ids, user_id)
    if not hourly:
        return None
    return sum(r.seconds for r in hourly if _app_selected(r.app, app))


def usage_seconds_for_window(
    db: Session,
    date: str,
    start_hour: int,
    end_hour: int,
    app: str | None = None,
    platform: str | None = None,
    *,
    user_id: int,
) -> int | None:
    """某本地日 [start_hour, end_hour) 小时窗口内的使用总秒数。

    只看 hourly 数据：过滤后的设备当天**无一**有 hourly 行 → None（无数据，
    不产实验数据点）；有 hourly 但窗口内为空 → 0。
    """
    devices = _usage_devices(db, platform, user_id)
    if not devices:
        return None
    rows = _day_hourly_rows(db, date, [d.id for d in devices], user_id)
    if not rows:
        return None
    return sum(
        r.seconds
        for r in rows
        if start_hour <= r.hour < end_hour and _app_selected(r.app, app)
    )


def hourly_seconds_for_day(
    db: Session, date: str, *, user_id: int
) -> dict[int, int]:
    """某日各本地小时的使用总秒数（排除 web:* 细分行）。

    与 `usage_seconds_for_day` 同源同口径（都读 `DeviceUsageHourly`、都排除 web:），
    所以逐小时相加 == 当天 `usage_total` —— 时间轴的小时视图与实验指标靠它对上账。
    没有数据的小时不出现在返回值里。
    """
    out: dict[int, int] = {}
    for r in usage_hourly_for_day(db, date, user_id):
        if not _app_selected(r.app, None):
            continue
        out[r.hour] = out.get(r.hour, 0) + r.seconds
    return out


def _merge_app(
    apps: dict[str, dict],
    app: str,
    label: str | None,
    seconds: int,
    switches: int,
    category: str,
) -> None:
    """把一个 (app, seconds, switches) 累加进按应用聚合表（保留首个非空 label）。

    `category` 每次都由**当前规则**推导（不是库里的陈旧快照），前端就地编辑
    面板要用它回填下拉默认值。
    """
    cur = apps.get(app)
    if cur is None:
        apps[app] = {
            "app": app,
            "label": label,
            "seconds": seconds,
            "switches": switches,
            "category": category,
        }
    else:
        cur["seconds"] += seconds
        cur["switches"] += switches
        if cur["label"] is None:
            cur["label"] = label


def usage_overview_for_day(
    db: Session, date: str, user_id: int, top_apps: int = 8
) -> list[dict]:
    """某日各设备的使用概览（供只读展示）。

    每台当天有数据的设备返回：平台/名称、总秒数、24 槽按小时合计、
    按应用合计（Top N）、按网站合计（Top N，仅浏览器采集的 web:* 行）。
    设备按总时长降序。
    total_seconds 只含进程行（web:* 是浏览器时长的细分，避免重复）。
    """
    devices = _usage_devices(db, None, user_id)
    if not devices:
        return []
    rules = load_rules(db, user_id)
    device_ids = [d.id for d in devices]
    hourly = _day_hourly_rows(db, date, device_ids, user_id)
    hourly_by_device: dict[int, list[DeviceUsageHourly]] = {}
    for r in hourly:
        hourly_by_device.setdefault(r.device_id, []).append(r)

    overview: list[dict] = []
    for device in devices:
        hrows = hourly_by_device.get(device.id)
        if not hrows:
            continue  # 该设备当天无数据
        apps: dict[str, dict] = {}
        websites: dict[str, dict] = {}
        buckets = [0] * 24
        hour_apps: dict[int, dict[str, dict]] = {}
        for r in hrows:
            shown = resolve_label(r.app, r.label, rules)
            category = classify_app(r.app, r.label, rules)
            if r.app.startswith(WEB_PREFIX):
                _merge_app(websites, r.app, shown, r.seconds, r.switches, category)
            else:
                buckets[r.hour] += r.seconds
                _merge_app(apps, r.app, shown, r.seconds, r.switches, category)
                _merge_app(
                    hour_apps.setdefault(r.hour, {}),
                    r.app,
                    shown,
                    r.seconds,
                    r.switches,
                    category,
                )
        hours = [
            {
                "hour": h,
                "seconds": s,
                # 该小时的应用明细（Top N），供分小时条悬浮展示
                "apps": sorted(
                    hour_apps.get(h, {}).values(),
                    key=lambda a: a["seconds"],
                    reverse=True,
                )[:top_apps],
            }
            for h, s in enumerate(buckets)
        ]

        app_list = sorted(apps.values(), key=lambda a: a["seconds"], reverse=True)[:top_apps]
        site_list = sorted(
            websites.values(), key=lambda a: a["seconds"], reverse=True
        )[:top_apps]
        overview.append(
            {
                "device_id": device.id,
                "name": device.name,
                "platform": device.platform,
                # total 只算进程行（web:* 是浏览器时长的细分，计进去会重复）
                "total_seconds": sum(a["seconds"] for a in apps.values()),
                "hours": hours,
                "apps": app_list,
                "websites": site_list,
            }
        )

    overview.sort(key=lambda d: d["total_seconds"], reverse=True)
    return overview


def uncategorized_apps(
    db: Session,
    start: str,
    end: str,
    *,
    min_seconds: int = 60,
    limit: int = 50,
    user_id: int,
) -> list[dict]:
    """区间内「仍没被认出大类」的应用，按时长降序（供逐条标记）。

    判类用**当前规则**：标记完再查，已认出的会自动从清单里消失。
    只收 `other_online`（词表兜底）或根本没有可读名称的——`web:` 站点即使
    没命中域名词表也会落 `browsing`，不算「未识别」（站点规则在网站列表里加）。
    `start`/`end` 为 YYYY-MM-DD（含两端），由路由层校验格式。
    """
    rows = db.scalars(
        select(DeviceAppSession).where(
            DeviceAppSession.user_id == user_id,
            DeviceAppSession.date >= start,
            DeviceAppSession.date <= end,
        )
    ).all()
    rules = load_rules(db, user_id)
    agg: dict[str, dict] = {}
    for r in rows:
        if r.app.startswith(WEB_PREFIX):
            continue  # 站点走网站列表就地标记，不混进应用清单
        category = classify_app(r.app, r.label, rules)
        if category != "other_online" and r.label:
            continue
        cur = agg.get(r.app)
        if cur is None:
            agg[r.app] = {
                "app": r.app,
                "label": r.label,
                "platform": r.platform,
                "seconds": r.seconds,
                "sessions": 1,
                "category": category,
            }
        else:
            cur["seconds"] += r.seconds
            cur["sessions"] += 1
            if cur["label"] is None:
                cur["label"] = r.label
    out = [a for a in agg.values() if a["seconds"] >= min_seconds]
    out.sort(key=lambda a: a["seconds"], reverse=True)
    return out[:limit]


def known_apps(
    db: Session,
    start: str,
    end: str,
    *,
    limit: int = 60,
    user_id: int,
) -> list[dict]:
    """区间内用过的全部应用/网站 key，按时长降序。

    **与 `uncategorized_apps` 的口径故意不同**：这里要的是「能被指标引用的 key 全集」，
    所以 `web:<域名>` 站点行**要收**（`uncategorized_apps` 为了展示把它们滤掉了），
    也不按分类/可读名称过滤。

    `app` 原样返回（`com.tencent.mm` / `web:youtube.com`）—— 指标的
    `usage_duration:<app>` 是拿它去和库里的 `app` 列精确比的，**显示名取不到数**。
    `label` 只用来给人/LLM 看。`start`/`end` 为 YYYY-MM-DD（含两端）。
    """
    devices = _usage_devices(db, None, user_id)
    if not devices:
        return []
    platform_by_device = {d.id: d.platform for d in devices}
    rows = db.scalars(
        select(DeviceUsageHourly)
        .where(
            DeviceUsageHourly.user_id == user_id,
            DeviceUsageHourly.device_id.in_(list(platform_by_device)),
            DeviceUsageHourly.date >= start,
            DeviceUsageHourly.date <= end,
        )
        .order_by(DeviceUsageHourly.date.desc())
    ).all()

    agg: dict[str, dict] = {}
    for r in rows:  # 按日期倒序 → 第一个非空 label 就是最近用过的名字
        cur = agg.get(r.app)
        if cur is None:
            agg[r.app] = {
                "app": r.app,
                "label": r.label,
                "platform": platform_by_device.get(r.device_id),
                "seconds": r.seconds,
            }
        else:
            cur["seconds"] += r.seconds
            if cur["label"] is None:
                cur["label"] = r.label
    out = sorted(agg.values(), key=lambda a: a["seconds"], reverse=True)
    return out[:limit]
