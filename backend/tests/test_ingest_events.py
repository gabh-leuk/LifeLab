"""设备令牌通路的手动记录（安卓主屏小组件）。

`/ingest/*` 此前只写 `source=DEVICE` 的采集数据；这里验证新开的人工通路
（`source=MANUAL`）归属正确、校验与 `/events` 一致，以及记录统计的口径。
"""

from datetime import datetime, timedelta, timezone

from app.models.event import Event


def _device_token(client, name="我的手机", platform="android") -> str:
    r = client.post("/devices", json={"name": name, "platform": platform})
    assert r.status_code == 201
    return r.json()["token"]


def _record(client, token, type_="LEARNING_START", **extra):
    return client.post(
        "/ingest/events",
        json={"type": type_, **extra},
        headers={"X-Device-Token": token},
    )


def _stats(client, token):
    return client.get("/ingest/record-stats", headers={"X-Device-Token": token})


def test_device_token_can_record_event(client, db_session):
    token = _device_token(client)
    r = _record(client, token, note="看书")
    assert r.status_code == 201
    body = r.json()
    assert body["event"]["type"] == "LEARNING_START"
    # 人按的钮 → MANUAL，不是 DEVICE（设备通路只负责认证，不改变来源语义）
    assert body["event"]["source"] == "MANUAL"
    assert body["event"]["note"] == "看书"
    assert body["event"]["category"] == "study_work"
    # 归属由设备行自带，设备侧不需要用户会话
    assert db_session.get(Event, body["event"]["id"]).user_id == 1
    # 记一条就把回报带回来，小组件无需二次请求
    assert body["today_count"] == 1
    assert body["streak_days"] == 1


def test_ingest_events_needs_device_token(client):
    """登录令牌走不了这条通路：它不带 X-Device-Token。"""
    assert client.post("/ingest/events", json={"type": "LEARNING_START"}).status_code == 401
    assert client.post(
        "/ingest/events",
        json={"type": "LEARNING_START"},
        headers={"X-Device-Token": "not-a-real-token"},
    ).status_code == 401


def test_ingest_events_rejects_unknown_type(client):
    token = _device_token(client)
    assert _record(client, token, "NOT_A_TYPE").status_code == 422
    # 未知类型不该留下半条记录
    assert _stats(client, token).json()["today_count"] == 0


def test_record_stats_today_count_and_streak(client):
    token = _device_token(client)
    assert _stats(client, token).json()["streak_days"] == 0
    _record(client, token)
    _record(client, token, "MEAL_START")
    body = _stats(client, token).json()
    assert body["today_count"] == 2
    assert body["streak_days"] == 1


def test_streak_counts_back_from_yesterday_when_today_is_empty(client, db_session):
    """今天还没记时，连续天数从昨天起算 —— 否则每天零点一过先归零再跳回来。"""
    token = _device_token(client)
    now = datetime.now(timezone.utc)
    for days_ago in (1, 2, 3, 5):  # 第 4 天断档
        db_session.add(
            Event(
                user_id=1,
                timestamp=now - timedelta(days=days_ago),
                type="LEARNING_START",
                category="study_work",
            )
        )
    db_session.commit()

    body = _stats(client, token).json()
    assert body["today_count"] == 0
    assert body["streak_days"] == 3  # 第 4 天断了，只数到 3


def test_ingest_event_types_matches_login_path(client):
    """小组件的按钮清单与网页端同一实现（内置在前）。"""
    token = _device_token(client)
    device_types = client.get("/ingest/event-types", headers={"X-Device-Token": token})
    assert device_types.status_code == 200
    assert device_types.json() == client.get("/event-types").json()
    assert device_types.json()[0]["builtin"] is True
