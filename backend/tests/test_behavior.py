"""设备使用 → 自动时间段行为（会话合并、人工优先判重、DEVICE 事件、时间轴叠加）。"""

from datetime import datetime, timezone

from app.behavior_categories import classify_app, normalize_domain
from app.models.device_usage import DeviceAppSession
from app.models.event import Event
from app.utils import ensure_aware


def _ms(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


def _make_device(client, name="电脑", platform="pc"):
    r = client.post("/devices", json={"name": name, "platform": platform})
    assert r.status_code == 201
    return r.json()


def _ingest_sessions(client, token, date, entries, tz=0):
    return client.post(
        "/ingest/usage/sessions",
        json={"date": date, "entries": entries},
        headers={"X-Device-Token": token},
    )


def _iso(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).isoformat()


# ── 分类规则 ────────────────────────────────────────────────


def test_classify_app_and_domain():
    assert classify_app("chrome.exe") == "browsing"
    assert classify_app("Code.exe", "Visual Studio Code") == "study_work"
    assert classify_app("com.tencent.mm", "微信") == "social"
    assert classify_app("com.bilibili.app", "哔哩哔哩") == "video"
    assert classify_app("unknown-app") == "other_online"
    # web:<域名> 走域名规则
    assert classify_app("web:www.bilibili.com") == "video"
    assert classify_app("web:github.com") == "study_work"
    assert classify_app("web:unknown-site.example") == "browsing"


def test_classify_phone_packages_and_new_categories():
    """手机端只有英文包名，中文标签匹配不到包名 → 词表里得写英文串。"""
    assert classify_app("com.sinovatech.unicom.ui", "中国联通") == "life_service"
    assert classify_app("com.okinc.okex.gp", "欧易") == "finance"
    assert classify_app("mark.via.gp", "Via") == "browsing"
    assert classify_app("com.zhihu.android", "知乎") == "forum"
    # 论坛社区从「社交」分家，域名侧同口径
    assert classify_app("web:www.zhihu.com") == "forum"
    assert classify_app("web:douban.com") == "forum"
    assert classify_app("web:weibo.com") == "social"
    assert classify_app("web:12306.cn") == "life_service"
    assert classify_app("web:binance.com") == "finance"
    # "via" 不能当子串关键词：否则含 via 的无关应用全被误判成浏览器
    assert classify_app("com.example.trivial") != "browsing"


def test_normalize_domain_merges_subdomains():
    assert normalize_domain("www.bilibili.com") == "bilibili.com"
    assert normalize_domain("m.bilibili.com") == "bilibili.com"
    assert normalize_domain("www.taobao.com.cn") == "taobao.com.cn"
    assert normalize_domain("sub.example.co.uk") == "example.co.uk"


# ── 会话上报 → DEVICE 事件 ──────────────────────────────────


def test_ingest_sessions_creates_behavior_segments(client, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    r = _ingest_sessions(
        client, body["token"], "2026-09-11",
        [
            {"app": "com.bilibili.app", "label": "哔哩哔哩", "start_ms": _ms(2026, 9, 11, 21), "end_ms": _ms(2026, 9, 11, 22, 30)},
            # 同类相邻（gap=0）→ 合并
            {"app": "com.bilibili.app", "label": "哔哩哔哩", "start_ms": _ms(2026, 9, 11, 22, 30), "end_ms": _ms(2026, 9, 11, 23)},
            # 不同类 → 另起一段
            {"app": "com.tencent.mm", "label": "微信", "start_ms": _ms(2026, 9, 11, 23), "end_ms": _ms(2026, 9, 11, 23, 30)},
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json()["received"] == 3
    assert r.json()["segments"] == 2  # 视频段 + 社交段

    events = db_session.query(Event).filter(Event.source == "DEVICE").all()
    by_cat = {e.category: e for e in events}
    assert set(by_cat) == {"video", "social"}
    assert by_cat["video"].note == "哔哩哔哩"
    assert ensure_aware(by_cat["video"].timestamp) == datetime(2026, 9, 11, 21, tzinfo=timezone.utc)
    assert ensure_aware(by_cat["video"].ended_at) == datetime(2026, 9, 11, 23, tzinfo=timezone.utc)


def test_ingest_sessions_is_idempotent(client, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    entries = [
        {"app": "Code.exe", "label": "VS Code", "start_ms": _ms(2026, 9, 11, 9), "end_ms": _ms(2026, 9, 11, 11)},
    ]
    _ingest_sessions(client, body["token"], "2026-09-11", entries)
    _ingest_sessions(client, body["token"], "2026-09-11", entries)
    assert db_session.query(Event).filter(Event.source == "DEVICE").count() == 1
    assert db_session.query(DeviceAppSession).count() == 1


def test_ingest_sessions_validates_times(client):
    body = _make_device(client)
    r = _ingest_sessions(
        client, body["token"], "2026-09-11",
        [{"app": "x", "start_ms": 1000, "end_ms": 1000}],
    )
    assert r.status_code == 422


# ── 人工优先（同类重复）与叠加（不同类保留） ───────────────


def test_same_category_manual_wins(client, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    # 人工「学习」覆盖 14:00-15:00
    ev = client.post(
        "/events",
        json={"type": "LEARNING_START", "timestamp": _iso(2026, 9, 11, 14)},
    ).json()
    client.patch(f"/events/{ev['id']}/end", json={"ended_at": _iso(2026, 9, 11, 15)})

    # 设备也在 14:00-15:00 观测到 study_work → 视为重复，应被丢弃
    _ingest_sessions(
        client, body["token"], "2026-09-11",
        [{"app": "Code.exe", "label": "VS Code", "start_ms": _ms(2026, 9, 11, 14), "end_ms": _ms(2026, 9, 11, 15)}],
    )
    device_events = db_session.query(Event).filter(Event.source == "DEVICE").all()
    assert device_events == []


def test_different_category_overlay_kept(client, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    # 人工「上床」23:00 起（容器）
    client.post(
        "/events",
        json={"type": "BED_START", "timestamp": _iso(2026, 9, 11, 23)},
    )
    # 设备真实活动：睡前刷视频
    _ingest_sessions(
        client, body["token"], "2026-09-11",
        [{"app": "com.bilibili.app", "label": "哔哩哔哩", "start_ms": _ms(2026, 9, 11, 23), "end_ms": _ms(2026, 9, 11, 23, 40)}],
    )
    device_events = db_session.query(Event).filter(Event.source == "DEVICE").all()
    assert len(device_events) == 1 and device_events[0].category == "video"

    tl = client.get("/timeline/2026-09-11?tz_offset=0").json()
    assert tl["device_segments"] == []  # 被人工段包含
    seg = tl["segments"][0]
    assert seg["event"]["type"] == "BED_START"
    assert [a["category"] for a in seg["device_activities"]] == ["video"]


# ── 时间轴：无人工作用时独立成段 ────────────────────────────


def test_timeline_device_segment_when_uncovered(client, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    _ingest_sessions(
        client, body["token"], "2026-09-11",
        [{"app": "Code.exe", "label": "VS Code", "start_ms": _ms(2026, 9, 11, 9), "end_ms": _ms(2026, 9, 11, 10)}],
    )
    tl = client.get("/timeline/2026-09-11?tz_offset=0").json()
    assert len(tl["device_segments"]) == 1
    seg = tl["device_segments"][0]
    assert seg["kind"] == "device"
    assert seg["event"]["category"] == "study_work"
    assert seg["duration_minutes"] == 60


def test_device_segment_only_carries_its_own_device_apps(client, monkeypatch):
    """同一小时电脑手机都在用：设备段只挂自己那台设备的应用。

    以前这里不按设备过滤，段的应用是「该时间窗内所有设备的并集」——
    于是电脑段会挂上手机上在玩什么（王者荣耀出现在写代码的那一段里）。
    """
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    pc = _make_device(client)
    phone = _make_device(client, name="手机", platform="android")
    _ingest_hourly(
        client, pc["token"], "2026-09-11",
        [{"app": "Code.exe", "label": "VS Code", "hour": 9, "seconds": 3600}],
    )
    _ingest_hourly(
        client, phone["token"], "2026-09-11",
        [{"app": "com.tencent.mm", "label": "微信", "hour": 9, "seconds": 3600}],
    )
    # 只有电脑报了会话 → 只有电脑出设备段
    _ingest_sessions(
        client, pc["token"], "2026-09-11",
        [{"app": "Code.exe", "label": "VS Code",
          "start_ms": _ms(2026, 9, 11, 9), "end_ms": _ms(2026, 9, 11, 10)}],
    )

    seg = client.get("/timeline/2026-09-11?tz_offset=0").json()["device_segments"][0]
    assert seg["platform"] == "pc"
    assert [a["app"] for a in seg["device_apps"]] == ["Code.exe"]


def test_manual_segment_apps_merge_both_devices(client, monkeypatch):
    """人工段是跨端容器（「上床」里电脑手机都算）→ 两端应用都挂上来，各带平台。"""
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    pc = _make_device(client)
    phone = _make_device(client, name="手机", platform="android")
    client.post(
        "/events",
        json={"type": "BED_START", "timestamp": _iso(2026, 9, 11, 23)},
    )
    _ingest_hourly(
        client, pc["token"], "2026-09-11",
        [{"app": "Code.exe", "label": "VS Code", "hour": 23, "seconds": 1800}],
    )
    _ingest_hourly(
        client, phone["token"], "2026-09-11",
        [{"app": "com.tencent.mm", "label": "微信", "hour": 23, "seconds": 1800}],
    )

    seg = client.get("/timeline/2026-09-11?tz_offset=0").json()["segments"][0]
    assert {a["platform"] for a in seg["device_apps"]} == {"pc", "android"}


# ── hourly 降级重建 ─────────────────────────────────────────


def test_rebuild_activity_from_hourly_fallback(client):
    body = _make_device(client)
    client.post(
        "/ingest/usage/hourly",
        json={"date": "2026-09-11", "entries": [{"app": "Code.exe", "label": "VS Code", "hour": 22, "seconds": 1800}]},
        headers={"X-Device-Token": body["token"]},
    )
    r = client.post("/devices/rebuild-activity?date=2026-09-11&tz_offset=0")
    assert r.status_code == 200
    assert r.json()["created"] == 1
    tl = client.get("/timeline/2026-09-11?tz_offset=0").json()
    assert len(tl["device_segments"]) == 1
    assert tl["device_segments"][0]["event"]["category"] == "study_work"


def test_delete_device_cleans_device_events(client, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    _ingest_sessions(
        client, body["token"], "2026-09-11",
        [{"app": "Code.exe", "label": "VS Code", "start_ms": _ms(2026, 9, 11, 9), "end_ms": _ms(2026, 9, 11, 10)}],
    )
    assert db_session.query(Event).filter(Event.source == "DEVICE").count() == 1
    assert client.delete(f"/devices/{body['device']['id']}").status_code == 204
    assert db_session.query(Event).filter(Event.source == "DEVICE").count() == 0


# ── 时间轴：设备段按小时合并（粗粒度扫读） ──────────────────


def _ingest_hourly(client, token, date, entries):
    r = client.post(
        "/ingest/usage/hourly",
        json={"date": date, "entries": entries},
        headers={"X-Device-Token": token},
    )
    assert r.status_code == 200, r.text


def test_device_hours_merges_same_hour_segments(client, monkeypatch):
    """同一小时内切换大类（视频→社交）→ 合并成一条 DeviceHour，应用时长累加。

    秒数以按小时采集（`DeviceUsageHourly`）为准：3600 + 1800 = 5400，
    与实验 `usage_total` 同口径。
    """
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    _ingest_hourly(
        client, body["token"], "2026-09-11",
        [
            {"app": "Code.exe", "label": "VS Code", "hour": 21, "seconds": 3600},
            {"app": "chrome.exe", "label": "Chrome", "hour": 21, "seconds": 1800},
        ],
    )
    _ingest_sessions(
        client, body["token"], "2026-09-11",
        [
            {"app": "com.bilibili.app", "label": "哔哩哔哩", "start_ms": _ms(2026, 9, 11, 21), "end_ms": _ms(2026, 9, 11, 21, 30)},
            {"app": "com.tencent.mm", "label": "微信", "start_ms": _ms(2026, 9, 11, 21, 30), "end_ms": _ms(2026, 9, 11, 22)},
        ],
    )

    tl = client.get("/timeline/2026-09-11?tz_offset=0").json()
    assert len(tl["device_segments"]) == 2  # 原始段仍是两条
    hours = tl["device_hours"]
    assert len(hours) == 1  # 合并后一条
    h = hours[0]
    assert h["hour"] == 21
    assert h["seconds"] == 5400
    assert h["start"] == "2026-09-11T21:00:00Z"
    assert h["end"] == "2026-09-11T22:00:00Z"
    assert set(h["categories"]) == {"video", "social"}
    # 大类按摊销秒数给（两个各占半小时）
    assert h["category_seconds"] == {"video": 1800, "social": 1800}
    # 应用来自 hourly：整点桶，不摊销
    assert [(a["app"], a["seconds"]) for a in h["apps"]] == [
        ("Code.exe", 3600),
        ("chrome.exe", 1800),
    ]
    # 每个应用带当前大类（前端 chip 就地编辑的默认值）
    assert [a["category"] for a in h["apps"]] == ["study_work", "browsing"]
    # 两条会话活动都落在 21 点
    assert len(h["activities"]) == 2


def test_device_hours_prorated_across_hours(client, monkeypatch):
    """跨小时的段按重叠摊销到两个桶，秒数之和 == 段时长（无 hourly 时退回摊销值）。"""
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    _ingest_sessions(
        client, body["token"], "2026-09-11",
        [{"app": "Code.exe", "label": "VS Code", "start_ms": _ms(2026, 9, 11, 21, 40), "end_ms": _ms(2026, 9, 11, 22, 20)}],
    )
    hours = client.get("/timeline/2026-09-11?tz_offset=0").json()["device_hours"]
    assert [h["hour"] for h in hours] == [21, 22]
    # 21:40–22:20 → 每边 20 分钟
    assert [h["category_seconds"]["study_work"] for h in hours] == [1200, 1200]
    assert sum(h["seconds"] for h in hours) == 2400
    # 活动明细整段挂在起点小时（段是视觉单位，不切开）
    assert [len(h["activities"]) for h in hours] == [1, 0]


def test_device_hours_merged_across_devices(client, monkeypatch):
    """两台设备同一小时 → 合并成一条，活动明细各带自己的平台。"""
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    pc = _make_device(client, "电脑", "pc")
    phone = _make_device(client, "手机", "android")
    _ingest_sessions(
        client, pc["token"], "2026-09-11",
        [{"app": "Code.exe", "label": "VS Code", "start_ms": _ms(2026, 9, 11, 21), "end_ms": _ms(2026, 9, 11, 21, 30)}],
    )
    _ingest_sessions(
        client, phone["token"], "2026-09-11",
        [{"app": "com.tencent.mm", "label": "微信", "start_ms": _ms(2026, 9, 11, 21), "end_ms": _ms(2026, 9, 11, 21, 20)}],
    )
    hours = client.get("/timeline/2026-09-11?tz_offset=0").json()["device_hours"]
    assert len(hours) == 1
    h = hours[0]
    assert h["hour"] == 21
    assert h["seconds"] == 3000  # 1800 + 1200，无 hourly 时退回摊销值
    assert h["categories"] == ["study_work", "social"]  # 按秒数降序
    assert {a["platform"] for a in h["activities"]} == {"pc", "android"}


def test_device_hours_empty_without_device_data(client, db_session):
    client.post("/events", json={"type": "LEARNING_START"})
    tl = client.get("/timeline/2026-09-11?tz_offset=0").json()
    assert tl["device_hours"] == []
    assert tl["device_segments"] == []


# ── 实验数据源：按行为大类 ──────────────────────────────────


def _make_exp(client, metrics):
    r = client.post("/experiments", json={"name": "类别实验", "metrics": metrics})
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, db_session, exp_id, start, end):
    from app.models.experiment import (
        Experiment,
        ExperimentStatus,
        ExperimentStatusEvent,
    )

    row = db_session.get(Experiment, exp_id)
    row.started_at = start
    row.ended_at = end
    row.status = ExperimentStatus.RUNNING.value
    db_session.add(
        ExperimentStatusEvent(
            user_id=1,
            experiment_id=exp_id,
            from_status=None,
            to_status="RUNNING",
            created_at=start,
        )
    )
    db_session.commit()
    client.post(f"/experiments/{exp_id}/aggregate?tz_offset=0")
    logs = client.get(f"/experiments/{exp_id}/logs").json()
    return {r["metric"]: r["value"] for r in logs if r["source"] == "AUTO"}


def test_metric_source_category_whitelist(client):
    exp = _make_exp(
        client,
        [
            {"key": "video_min", "name": "视频时长", "direction": "down_good", "source": "event_duration_category:video"},
            {"key": "study_cnt", "name": "学习次数", "source": "event_count_category:study_work"},
        ],
    )
    assert [m["source"] for m in exp["metrics"]] == [
        "event_duration_category:video",
        "event_count_category:study_work",
    ]
    bad = client.post(
        "/experiments",
        json={"name": "x", "metrics": [{"key": "x", "name": "x", "source": "event_duration_category:nope"}]},
    )
    assert bad.status_code == 422


def test_aggregate_category_metrics(client, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.ingest._local_tz_offset_minutes", lambda: 0)
    body = _make_device(client)
    _ingest_sessions(
        client, body["token"], "2026-09-11",
        [
            {"app": "com.bilibili.app", "label": "哔哩哔哩", "start_ms": _ms(2026, 9, 11, 21), "end_ms": _ms(2026, 9, 11, 22)},
            {"app": "com.tencent.mm", "label": "微信", "start_ms": _ms(2026, 9, 11, 22), "end_ms": _ms(2026, 9, 11, 22, 30)},
        ],
    )
    exp = _make_exp(
        client,
        [
            {"key": "video_min", "name": "视频时长", "direction": "down_good", "source": "event_duration_category:video"},
            {"key": "video_cnt", "name": "视频段数", "source": "event_count_category:video"},
        ],
    )
    by = _run(
        client, db_session, exp["id"],
        datetime(2026, 9, 11, tzinfo=timezone.utc),
        datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    assert by["video_min"] == 60.0  # 3600s
    assert by["video_cnt"] == 1.0
