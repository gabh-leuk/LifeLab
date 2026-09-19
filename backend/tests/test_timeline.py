from datetime import datetime, timedelta, timezone

from app.models.event import Event, EventType
from app.models.state import StateRecord
from app.models.thought import Thought


def _ev(db_session, when, etype, ended_at=None):
    db_session.add(
        Event(
            user_id=1,
            timestamp=when,
            type=etype.value,
            source="MANUAL",
            confidence=1.0,
            ended_at=ended_at,
        )
    )
    db_session.commit()


def _thought(db_session, when, content="测试想法"):
    db_session.add(Thought(user_id=1, timestamp=when, content=content))
    db_session.commit()


def _state(db_session, when):
    db_session.add(
        StateRecord(user_id=1, timestamp=when, energy=3, focus=4, irritation=1)
    )
    db_session.commit()


DAY_START = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)


def test_two_events_form_continuous_segments(client, db_session):
    a = DAY_START.replace(hour=9)
    b = DAY_START.replace(hour=10, minute=30)
    _ev(db_session, a, EventType.LEARNING_START)
    _ev(db_session, b, EventType.GAME_START)

    r = client.get("/timeline/2026-09-02")
    assert r.status_code == 200
    body = r.json()
    segments = body["segments"]
    assert len(segments) == 2

    # 段1: 学习 9:00-10:30（被下一事件截断），推断结束
    s1 = segments[0]
    assert s1["event"]["type"] == "LEARNING_START"
    assert s1["end_inferred"] is True
    assert s1["end"] == "2026-09-02T10:30:00Z"
    assert s1["duration_minutes"] == 90

    # 段2: 游戏 10:30-当天末尾，推断结束
    s2 = segments[1]
    assert s2["event"]["type"] == "GAME_START"
    assert s2["end_inferred"] is True
    assert s2["end"] == "2026-09-03T00:00:00Z"


def test_explicit_ended_at_used_and_not_fuzzy(client, db_session):
    a = DAY_START.replace(hour=9)
    _ev(
        db_session,
        a,
        EventType.LEARNING_START,
        ended_at=DAY_START.replace(hour=10),
    )

    r = client.get("/timeline/2026-09-02")
    seg = r.json()["segments"][0]
    assert seg["end_inferred"] is False
    assert seg["end"] == "2026-09-02T10:00:00Z"
    assert seg["fuzzy"] is False


def test_long_inferred_segment_fuzzy(client, db_session):
    # 学习从 9:00 到下一个事件 14:00 → 5h 推断 → 模糊
    _ev(db_session, DAY_START.replace(hour=9), EventType.LEARNING_START)
    _ev(db_session, DAY_START.replace(hour=14), EventType.MEAL_START)

    seg = client.get("/timeline/2026-09-02").json()["segments"][0]
    assert seg["fuzzy"] is True
    assert seg["fuzzy_reason"] is not None


def test_sleep_never_fuzzy(client, db_session):
    # 睡觉 22:00 - 次日（当天末尾）→ 长但类型豁免
    _ev(db_session, DAY_START.replace(hour=22), EventType.SLEEP_START)

    seg = client.get("/timeline/2026-09-02").json()["segments"][0]
    assert seg["fuzzy"] is False
    assert seg["end_inferred"] is True


def test_short_segment_not_fuzzy(client, db_session):
    _ev(db_session, DAY_START.replace(hour=9), EventType.LEARNING_START)
    _ev(db_session, DAY_START.replace(hour=9, minute=40), EventType.GAME_START)

    seg = client.get("/timeline/2026-09-02").json()["segments"][0]
    assert seg["fuzzy"] is False


def test_thoughts_states_float_inside_segment(client, db_session):
    base = DAY_START.replace(hour=9)
    _ev(db_session, base, EventType.LEARNING_START)
    _ev(db_session, DAY_START.replace(hour=11), EventType.MEAL_START)

    _thought(db_session, base + timedelta(minutes=30))
    _state(db_session, base + timedelta(minutes=60))

    body = client.get("/timeline/2026-09-02").json()
    floatings = body["floatings"]
    assert len(floatings) == 2
    kinds = {f["kind"] for f in floatings}
    assert kinds == {"thought", "state"}
    # 时间 9:30 / 10:00，都在学习段（9:00-11:00）内
    for f in floatings:
        ts = datetime.fromisoformat(f["timestamp"])
        assert DAY_START.replace(hour=9) <= ts < DAY_START.replace(hour=11)


def test_thought_before_first_event_not_shown(client, db_session):
    _ev(db_session, DAY_START.replace(hour=10), EventType.LEARNING_START)
    _thought(db_session, DAY_START.replace(hour=9))  # 第一个事件之前

    body = client.get("/timeline/2026-09-02").json()
    assert body["floatings"] == []


def test_empty_day(client):
    r = client.get("/timeline/2099-01-01")
    assert r.status_code == 200
    assert r.json()["segments"] == []
    assert r.json()["floatings"] == []


def test_last_segment_boundary_is_day_end(client, db_session):
    """当天最后一个事件：end 应锚定到当天结束（day_end），而非下一天。"""
    a = DAY_START.replace(hour=9)
    _ev(db_session, a, EventType.LEARNING_START)
    b = DAY_START.replace(hour=14)
    _ev(db_session, b, EventType.GAME_START)

    segments = client.get("/timeline/2026-09-02").json()["segments"]
    assert len(segments) == 2
    # 第一段被下一事件截断
    assert segments[0]["end_boundary"] == "next_event"
    # 最后一段锚定当天末尾
    assert segments[1]["end_boundary"] == "day_end"
    assert segments[1]["end"] == "2026-09-03T00:00:00Z"


def test_detect_end_boundary(client, db_session):
    """显式结束时 end_boundary = explicit。"""
    _ev(db_session, DAY_START.replace(hour=9), EventType.LEARNING_START,
        ended_at=DAY_START.replace(hour=10))
    seg = client.get("/timeline/2026-09-02").json()["segments"][0]
    assert seg["end_boundary"] == "explicit"
    assert seg["end_inferred"] is False


def test_day_end_anchors_to_local_midnight(client, db_session):
    """东八区 (tz_offset=480)：当天最后一段应锚定到本地 24:00，
    而非 UTC 的 00:00（那会落到本地次日 08:00）。"""
    # 本地 2026-09-02 09:00 = UTC 01:00
    local9 = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
    _ev(db_session, local9, EventType.LEARNING_START)

    body = client.get("/timeline/2026-09-02?tz_offset=480").json()
    seg = body["segments"][0]
    # 本地次日 00:00 = UTC 09-02 16:00 → 前端 toLocaleTimeString 显示 00:00（本地午夜）
    assert seg["end_boundary"] == "day_end"
    assert seg["end"] == "2026-09-02T16:00:00Z"


def test_timeline_query_respects_local_day(client, db_session):
    """东八区查询 2026-09-02：只含本地当天的事件，不含本地 09-03 凌晨但 UTC 尚在 09-02 的记录。"""
    # 本地 09-02 23:30 = UTC 09-02 15:30 → 属于本地 09-02
    in_local_day = datetime(2026, 9, 2, 15, 30, tzinfo=timezone.utc)
    # 本地 09-03 00:30 = UTC 09-02 16:30 → 已属本地 09-03（UTC 仍是 09-02）
    next_local_day = datetime(2026, 9, 2, 16, 30, tzinfo=timezone.utc)
    _ev(db_session, in_local_day, EventType.LEARNING_START)
    _ev(db_session, next_local_day, EventType.GAME_START)

    body = client.get("/timeline/2026-09-02?tz_offset=480").json()
    types = [s["event"]["type"] for s in body["segments"]]
    assert types == ["LEARNING_START"]  # GAME 已落入本地 09-03，不在 09-02 显示

    # 且本地 09-02 最后一段结束 = 本地午夜（UTC 16:00）
    assert body["segments"][0]["end"] == "2026-09-02T16:00:00Z"


# ── 段内设备应用（按小时桶摊销） ────────────────────────────


def _ingest_hourly(client, date, entries):
    dev = client.post("/devices", json={"name": "电脑", "platform": "pc"}).json()
    r = client.post(
        "/ingest/usage/hourly",
        json={"date": date, "entries": entries},
        headers={"X-Device-Token": dev["token"]},
    )
    assert r.status_code == 200
    return dev


def test_segment_carries_device_apps(client, db_session):
    """事件段带出该时段使用的设备应用（Top 应用按时长降序）。"""
    _ingest_hourly(
        client, "2026-09-02",
        [
            {"app": "Code.exe", "label": "VS Code", "hour": 22, "seconds": 3600},
            {"app": "chrome.exe", "label": "Chrome", "hour": 22, "seconds": 1800},
        ],
    )
    _ev(db_session, DAY_START.replace(hour=22), EventType.GAME_START)

    seg = client.get("/timeline/2026-09-02").json()["segments"][0]
    assert seg["end_boundary"] == "day_end"
    apps = seg["device_apps"]
    assert [a["app"] for a in apps] == ["Code.exe", "chrome.exe"]
    assert apps[0]["platform"] == "pc"
    assert apps[0]["label"] == "VS Code"
    assert apps[0]["seconds"] == 3600  # 22:00-24:00 完整覆盖 22 点桶


def test_segment_device_apps_prorated_by_overlap(client, db_session):
    """段起止只覆盖某小时的一部分 → 按重叠比例摊销。"""
    _ingest_hourly(
        client, "2026-09-02",
        [{"app": "Code.exe", "hour": 22, "seconds": 3600}],
    )
    # 段 22:30 - 当天末尾：22 点桶只覆盖一半
    _ev(db_session, DAY_START.replace(hour=22, minute=30), EventType.GAME_START)

    seg = client.get("/timeline/2026-09-02").json()["segments"][0]
    assert seg["device_apps"][0]["seconds"] == 1800


def test_segment_without_device_data_has_empty_apps(client, db_session):
    _ev(db_session, DAY_START.replace(hour=9), EventType.LEARNING_START)
    seg = client.get("/timeline/2026-09-02").json()["segments"][0]
    assert seg["device_apps"] == []
