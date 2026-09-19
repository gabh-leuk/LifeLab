from datetime import datetime, time, timedelta, timezone

from fastapi import HTTPException


def _fixed_offset_tz(offset_minutes: int) -> timezone:
    """客户端本地时区 → 固定偏移 timezone（±14h 内，分钟精度）。"""
    offset_minutes = max(-14 * 60, min(14 * 60, int(offset_minutes)))
    return timezone(timedelta(minutes=offset_minutes))


def parse_day_range(
    date: str, tz_offset_minutes: int = 0
) -> tuple[datetime, datetime]:
    """解析 'YYYY-MM-DD'，返回本地日 [当天00:00, 次日00:00) 对应的 UTC 区间。

    tz_offset_minutes = 客户端 UTC 偏移（分钟），如东八区 = 480。
    这样"一天"按用户本地时区分界，而非固定 UTC 0 点。
    """
    try:
        # 刻意先解析成熟 naive 日期，再套用户本地时区（下方 timezone()）
        local = datetime.strptime(date, "%Y-%m-%d")  # noqa: DTZ007 - naive 是刻意的，后续套时区
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    tz = _fixed_offset_tz(tz_offset_minutes)
    # local date 00:00 + 偏移 → UTC 时刻
    day_start = datetime.combine(local.date(), time.min, tzinfo=tz).astimezone(
        timezone.utc
    )
    day_end = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=tz).astimezone(
        timezone.utc
    )
    return day_start, day_end


def ensure_aware(dt: datetime) -> datetime:
    """SQLite 读出的 datetime 无时区（naive），统一视为 UTC 补上时区。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def local_today() -> str:
    """服务器本地日期（YYYY-MM-DD）。

    「今天」的默认值：设备令牌通路（安卓小组件）与网页端都从这里取，
    与 `routers/ingest.py` 的日界线偏移是同一前提（后端与用户同机同时区）。
    """
    return datetime.now().astimezone().date().isoformat()


def fmt_minutes(seconds: float) -> str:
    """秒 → 紧凑中文时长（`<1分` / `40分` / `3h` / `1h5分`）。

    复盘 prompt 与周/月设备合计都要把秒数说成人话，共用一处以免两边口径漂移。
    """
    minutes = round(seconds / 60)
    if minutes < 1:
        return "<1分"
    if minutes < 60:
        return f"{minutes}分"
    h, m = divmod(minutes, 60)
    return f"{h}h{m}分" if m else f"{h}h"
