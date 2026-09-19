"""日级小时聚合（day_hours_service）：时间轴小时视图与实验指标取值的唯一来源。

锁住三件事：

1. 小时秒数取自 `DeviceUsageHourly` —— 逐小时相加必须等于实验口径的 `usage_total`
   （改动前时间轴只由「人工段之外的设备段」构建，两边永远对不上账）
2. 跨小时的设备段按重叠摊销，且裁剪到当日边界
3. 实验的 `category_*` 与时间轴小时桶算出同一个数
"""

from datetime import datetime, timezone

from app.models.event import Event, EventSource
from app.services import day_hours_service, device_service
from app.utils import parse_day_range

DAY = "2026-09-11"


def _ms(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def _make_device(client, name="电脑", platform="pc"):
    r = client.post("/devices", json={"name": name, "platform": platform})
    assert r.status_code == 201
    return r.json()


def _ingest_hourly(client, token, date, entries):
    r = client.post(
        "/ingest/usage/hourly",
        json={"date": date, "entries": entries},
        headers={"X-Device-Token": token},
    )
    assert r.status_code == 200, r.text


def _ingest_sessions(client, token, date, entries):
    r = client.post(
        "/ingest/usage/sessions",
        json={"date": date, "entries": entries},
        headers={"X-Device-Token": token},
    )
    assert r.status_code == 200, r.text


def _add_device_event(db_session, start, end, category, note=None, user_id=1, device_id=None):
    """直接落一条 DEVICE 事件（绕开采集链路，精确控制跨小时/跨日边界）。"""
    db_session.add(
        Event(
            user_id=user_id,
            timestamp=start,
            type="DEVICE_ACTIVITY",
            source=EventSource.DEVICE.value,
            category=category,
            note=note,
            device_id=device_id,
            ended_at=end,
        )
    )
    db_session.commit()


def _hours(db_session, date=DAY, tz=0, user_id=1):
    day_start, day_end = parse_day_range(date, tz)
    return day_hours_service.build_day_hours(
        db_session, date, day_start, day_end, user_id=user_id
    )


# ── 秒数口径：取自按小时采集，逐小时相加 == usage_total ────────


def test_hour_seconds_reconcile_with_usage_total(client, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    _ingest_hourly(
        client, body["token"], DAY,
        [
            {"app": "Code.exe", "label": "VS Code", "hour": 9, "seconds": 1800},
            {"app": "chrome.exe", "label": "Chrome", "hour": 9, "seconds": 600},
            {"app": "Code.exe", "label": "VS Code", "hour": 10, "seconds": 3600},
        ],
    )
    # 会话时长（50 分钟）与 hourly 不一致：小时秒数必须听 hourly 的
    _ingest_sessions(
        client, body["token"], DAY,
        [{"app": "Code.exe", "label": "VS Code",
          "start_ms": _ms(2026, 9, 11, 9), "end_ms": _ms(2026, 9, 11, 9, 50)}],
    )
    assert client.post(f"/devices/rebuild-activity?date={DAY}&tz_offset=0").status_code == 200

    hours = client.get(f"/timeline/{DAY}?tz_offset=0").json()["device_hours"]
    assert [h["hour"] for h in hours] == [9, 10]
    assert [h["seconds"] for h in hours] == [2400, 3600]
    # 时间轴与实验口径对上账
    total = device_service.usage_seconds_for_day(db_session, DAY, user_id=1)
    assert sum(h["seconds"] for h in hours) == total == 6000


def test_hour_without_hourly_falls_back_to_activity_seconds(client, db_session):
    """只发会话不发按小时用量时，不能显示「一条可见的活动 + 0 秒」。"""
    _add_device_event(
        db_session,
        datetime(2026, 9, 11, 9, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 9, 30, tzinfo=timezone.utc),
        "video",
    )
    hours = _hours(db_session)
    assert len(hours) == 1
    assert hours[0].hour == 9
    assert hours[0].seconds == 1800
    assert hours[0].categories == ["video"]
    assert len(hours[0].activities) == 1


# ── 跨小时摊销与当日裁剪 ────────────────────────────────────


def test_cross_hour_segment_prorated_by_overlap(client, db_session):
    """09:40–10:20 各占 20 分钟；活动明细整段挂在起点小时，不切开。"""
    _add_device_event(
        db_session,
        datetime(2026, 9, 11, 9, 40, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 10, 20, tzinfo=timezone.utc),
        "study_work",
    )
    hours = _hours(db_session)
    assert [h.hour for h in hours] == [9, 10]
    assert [h.category_seconds["study_work"] for h in hours] == [1200, 1200]
    assert [len(h.activities) for h in hours] == [1, 0]


def test_segment_crossing_midnight_clipped_to_day(client, db_session):
    """23:40–次日 00:20 只有 20 分钟落在当日（旧口径按整段算 40 分钟）。"""
    _add_device_event(
        db_session,
        datetime(2026, 9, 11, 23, 40, tzinfo=timezone.utc),
        datetime(2026, 9, 12, 0, 20, tzinfo=timezone.utc),
        "study_work",
    )
    hours = _hours(db_session)
    assert [h.hour for h in hours] == [23]
    assert hours[0].category_seconds == {"study_work": 1200}
    assert day_hours_service.category_seconds_for_day(
        db_session, DAY, "study_work", user_id=1
    ) == 1200.0


def test_hour_segments_merge_across_devices(client, db_session):
    """两台设备的同一小时合并成一个桶，活动明细各带自己的平台。"""
    pc = _make_device(client, "电脑", "pc")
    phone = _make_device(client, "手机", "android")
    _add_device_event(
        db_session, datetime(2026, 9, 11, 21, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 21, 30, tzinfo=timezone.utc),
        "study_work", device_id=pc["device"]["id"],
    )
    _add_device_event(
        db_session, datetime(2026, 9, 11, 21, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 21, 20, tzinfo=timezone.utc),
        "social", device_id=phone["device"]["id"],
    )
    hours = _hours(db_session)
    assert len(hours) == 1
    assert hours[0].categories == ["study_work", "social"]  # 按摊销秒数降序
    assert {a.platform for a in hours[0].activities} == {"pc", "android"}


# ── 跨源对账：实验指标 == 小时桶 ─────────────────────────────


def test_metric_and_hour_buckets_agree(client, db_session):
    _add_device_event(
        db_session,
        datetime(2026, 9, 11, 9, 40, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 10, 20, tzinfo=timezone.utc),
        "study_work",
    )
    _add_device_event(
        db_session,
        datetime(2026, 9, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 15, 30, tzinfo=timezone.utc),
        "video",
    )
    hours = _hours(db_session)
    bucket_seconds = sum(h.category_seconds.get("study_work", 0) for h in hours)
    assert bucket_seconds == 2400
    # 实验取值读同一处
    assert day_hours_service.category_seconds_for_day(
        db_session, DAY, "study_work", user_id=1
    ) == bucket_seconds
    assert day_hours_service.category_count_for_day(
        db_session, DAY, "study_work", user_id=1
    ) == 1.0
    assert day_hours_service.category_count_for_day(
        db_session, DAY, "video", user_id=1
    ) == 1.0


def test_no_device_data_yields_no_hours(client, db_session):
    """无设备活动 → 无桶，且指标返回 None（无数据，而非 0）。"""
    client.post("/events", json={"type": "LEARNING_START"})
    assert _hours(db_session) == []
    assert day_hours_service.category_seconds_for_day(
        db_session, DAY, "video", user_id=1
    ) is None
    assert day_hours_service.category_count_for_day(
        db_session, DAY, "video", user_id=1
    ) is None


# ── 当日大类合计（周复盘设备行取值） ─────────────────────────


def test_category_totals_for_day_sums_all_categories(client, db_session):
    _add_device_event(
        db_session,
        datetime(2026, 9, 11, 9, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 9, 30, tzinfo=timezone.utc),
        "study_work",
    )
    _add_device_event(
        db_session,
        datetime(2026, 9, 11, 15, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 15, 10, tzinfo=timezone.utc),
        "video",
    )
    totals = day_hours_service.category_totals_for_day(db_session, DAY, user_id=1)
    assert totals == {"study_work": 1800, "video": 600}


def test_category_totals_prorate_cross_hour_segment(client, db_session):
    """09:40–10:20 合计仍是 40 分钟，但必须来自两个小时的摊销而非起点小时整段。"""
    _add_device_event(
        db_session,
        datetime(2026, 9, 11, 9, 40, tzinfo=timezone.utc),
        datetime(2026, 9, 11, 10, 20, tzinfo=timezone.utc),
        "study_work",
    )
    assert day_hours_service.category_totals_for_day(db_session, DAY, user_id=1) == {
        "study_work": 2400
    }
    hours = _hours(db_session)
    assert [h.category_seconds["study_work"] for h in hours] == [1200, 1200]


def test_category_totals_empty_without_device_activity(client, db_session):
    client.post("/events", json={"type": "LEARNING_START"})
    assert day_hours_service.category_totals_for_day(db_session, DAY, user_id=1) == {}


def test_hour_buckets_do_not_leak_other_days(client, db_session):
    """前一天的设备段不能算进当天的小时视图。"""
    _add_device_event(
        db_session,
        datetime(2026, 9, 10, 22, tzinfo=timezone.utc),
        datetime(2026, 9, 10, 23, tzinfo=timezone.utc),
        "video",
    )
    assert _hours(db_session) == []
    day_start, day_end = parse_day_range(DAY, 0)
    assert day_hours_service._device_events(db_session, day_start, day_end, 1) == []
