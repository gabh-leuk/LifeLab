import pytest


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_event_minimal(client):
    r = client.post("/events", json={"type": "LEARNING_START"})
    assert r.status_code == 201
    data = r.json()
    assert data["type"] == "LEARNING_START"
    assert data["source"] == "MANUAL"
    assert data["confidence"] == 1.0
    assert data["note"] is None
    assert data["user_id"] == 1
    assert "timestamp" in data


def test_create_event_with_note(client):
    r = client.post(
        "/events",
        json={"type": "GAME_START", "note": "打了把宇宙机器人", "confidence": 0.9},
    )
    assert r.status_code == 201
    assert r.json()["note"] == "打了把宇宙机器人"


def test_event_type_invalid_422(client):
    r = client.post("/events", json={"type": "NOT_A_TYPE"})
    assert r.status_code == 422


def test_list_events_empty(client):
    from app.main import app
    print("OVERRIDES:", list(app.dependency_overrides.keys()))
    r = client.get("/events")
    assert r.status_code == 200
    assert r.json() == []


def test_list_events_returns_created(client):
    client.post("/events", json={"type": "MEAL_START"})
    client.post("/events", json={"type": "SLEEP_START"})
    r = client.get("/events")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 2
    assert events[0]["type"] == "SLEEP_START"  # 按时间倒序


def test_list_events_by_date(client):
    client.post("/events", json={"type": "LEARNING_START"})
    today = pytest.importorskip("datetime").datetime.now().strftime("%Y-%m-%d")
    # 前端总是带本地时区偏移；此处按本机时区分天（东八区 = +480）
    tz_offset = int(
        pytest.importorskip("datetime")
        .datetime.now()
        .astimezone()
        .utcoffset()
        .total_seconds()
        // 60
    )
    r = client.get("/events", params={"date": today, "tz_offset": tz_offset})
    assert r.status_code == 200
    assert len(r.json()) == 1
    r2 = client.get("/events", params={"date": "1999-01-01", "tz_offset": tz_offset})
    assert r2.json() == []


def test_list_events_invalid_date_422(client):
    r = client.get("/events", params={"date": "not-a-date"})
    assert r.status_code == 422


def test_end_event_sets_ended_at(client):
    created = client.post("/events", json={"type": "LEARNING_START"}).json()
    eid = created["id"]
    r = client.patch(f"/events/{eid}/end", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["ended_at"] is not None
    assert data["type"] == "LEARNING_START"


def test_end_event_with_custom_time(client):
    from datetime import datetime, timedelta, timezone

    created = client.post("/events", json={"type": "GAME_START"}).json()
    eid = created["id"]
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    r = client.patch(f"/events/{eid}/end", json={"ended_at": future})
    assert r.status_code == 200
    # 返回的 ended_at 应带时区（naive 会被补成 UTC）
    ended = r.json()["ended_at"]
    assert ended is not None
    assert ended.endswith("Z") or "+" in ended


def test_end_event_before_start_422(client):
    created = client.post("/events", json={"type": "GAME_START"}).json()
    eid = created["id"]
    r = client.patch(
        f"/events/{eid}/end", json={"ended_at": "2000-01-01T00:00:00+00:00"}
    )
    assert r.status_code == 422


def test_end_event_not_found_404(client):
    r = client.patch("/events/99999/end", json={})
    assert r.status_code == 404


def test_get_event_by_id(client):
    created = client.post("/events", json={"type": "LEARNING_START"}).json()
    r = client.get(f"/events/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_event_not_found_404(client):
    assert client.get("/events/99999").status_code == 404


def test_delete_event(client):
    created = client.post("/events", json={"type": "LEARNING_START"}).json()
    eid = created["id"]
    r = client.delete(f"/events/{eid}")
    assert r.status_code == 204
    assert client.get(f"/events/{eid}").status_code == 404


def test_delete_event_junk_404(client):
    assert client.delete("/events/99999").status_code == 404


# ── 补记/预记（自定义时间戳） ──────────────────────────────

def _iso(dt):
    return dt.isoformat()


def test_create_event_with_past_timestamp(client):
    from datetime import datetime, timedelta, timezone

    past = datetime.now(timezone.utc) - timedelta(hours=2)
    r = client.post(
        "/events",
        json={"type": "LEARNING_START", "timestamp": _iso(past)},
    )
    assert r.status_code == 201
    # 返回时间应为传入时间（截断到分钟）
    assert r.json()["timestamp"].startswith(past.strftime("%Y-%m-%dT%H:%M"))


def test_create_event_with_future_timestamp(client):
    from datetime import datetime, timedelta, timezone

    future = datetime.now(timezone.utc) + timedelta(days=1)
    r = client.post(
        "/events",
        json={"type": "MEAL_START", "timestamp": _iso(future)},
    )
    assert r.status_code == 201
    assert r.json()["timestamp"].startswith(future.strftime("%Y-%m-%dT%H:%M"))


def test_create_event_timestamp_too_far_422(client):
    from datetime import datetime, timedelta, timezone

    too_old = datetime.now(timezone.utc) - timedelta(days=31)
    r = client.post(
        "/events",
        json={"type": "LEARNING_START", "timestamp": _iso(too_old)},
    )
    assert r.status_code == 422


def test_create_event_timestamp_without_tz_interpreted_utc(client):
    from datetime import datetime, timedelta, timezone

    # 无时区的 ISO 串 → 视为 UTC（服务器统一 UTC）；取当前小时整点保证在 ±30 天内
    naive = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    r = client.post(
        "/events",
        json={"type": "GAME_START", "timestamp": naive},
    )
    assert r.status_code == 201
    # 应被补成 UTC 且截断到分钟
    assert r.json()["timestamp"].endswith("Z") or "+" in r.json()["timestamp"]
    assert r.json()["timestamp"].startswith(naive[:16])


# ── 就地修订（补备注 / 加标签 / 改类型） ────────────────────

def test_update_event_note_and_tags(client):
    eid = client.post("/events", json={"type": "LEARNING_START"}).json()["id"]
    r = client.patch(f"/events/{eid}", json={"note": "学 FastAPI", "tags": ["番茄钟", "深度"]})
    assert r.status_code == 200
    data = r.json()
    assert data["note"] == "学 FastAPI"
    assert data["tags"] == ["番茄钟", "深度"]


def test_update_event_null_note_clears(client):
    created = client.post("/events", json={"type": "MEAL_START", "note": "午饭"}).json()
    r = client.patch(f"/events/{created['id']}", json={"note": None})
    assert r.status_code == 200
    assert r.json()["note"] is None


def test_update_event_explicit_empty_tags_clears(client):
    created = client.post(
        "/events", json={"type": "MEAL_START", "tags": ["a", "b"]}
    ).json()
    r = client.patch(f"/events/{created['id']}", json={"tags": []})
    assert r.status_code == 200
    assert r.json()["tags"] == []


def test_update_event_partial_leaves_other_fields(client):
    created = client.post(
        "/events", json={"type": "MEAL_START", "note": "午饭", "tags": ["x"]}
    ).json()
    r = client.patch(f"/events/{created['id']}", json={"tags": ["y"]})
    assert r.status_code == 200
    data = r.json()
    assert data["note"] == "午饭"  # 没传 note → 不动
    assert data["tags"] == ["y"]


def test_update_event_type_rederives_category(client):
    created = client.post("/events", json={"type": "LEARNING_START"}).json()
    assert created["category"] == "study_work"
    r = client.patch(f"/events/{created['id']}", json={"type": "GAME_START"})
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "GAME_START"
    assert data["category"] == "game"  # 未显式给 category → 跟随新类型重算


def test_update_event_type_with_explicit_category(client):
    created = client.post("/events", json={"type": "LEARNING_START"}).json()
    r = client.patch(
        f"/events/{created['id']}",
        json={"type": "OUT_START", "category": "other"},
    )
    assert r.status_code == 200
    assert r.json()["category"] == "other"  # 显式给的一律优先


def test_update_event_unknown_type_422(client):
    eid = client.post("/events", json={"type": "LEARNING_START"}).json()["id"]
    assert client.patch(f"/events/{eid}", json={"type": "NOPE"}).status_code == 422


def test_update_event_invalid_category_422(client):
    eid = client.post("/events", json={"type": "LEARNING_START"}).json()["id"]
    r = client.patch(f"/events/{eid}", json={"category": "not_a_category"})
    assert r.status_code == 422


def test_update_event_not_found_404(client):
    assert client.patch("/events/99999", json={"note": "x"}).status_code == 404


def test_record_stats_today_and_streak(client, db_session):
    """记录页的即时回报：今天条数 + 连续天数。"""
    from datetime import datetime, timedelta, timezone

    from app.models.event import Event

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    db_session.add_all(
        [
            Event(user_id=1, timestamp=now, type="LEARNING_START", category="study_work"),
            Event(user_id=1, timestamp=now, type="MEAL_START", category="meal"),
            Event(
                user_id=1,
                timestamp=now - timedelta(days=1),
                type="MEAL_START",
                category="meal",
            ),
        ]
    )
    db_session.commit()

    body = client.get(
        "/events/record-stats", params={"date": today, "tz_offset": 0}
    ).json()
    assert body["date"] == today
    assert body["today_count"] == 2
    assert body["streak_days"] == 2  # 今天 + 昨天，前天没有


def test_record_stats_defaults_to_today_and_empty(client):
    body = client.get("/events/record-stats").json()
    assert body["today_count"] == 0
    assert body["streak_days"] == 0


def test_record_stats_rejects_bad_date(client):
    r = client.get("/events/record-stats", params={"date": "not-a-date"})
    assert r.status_code == 422
