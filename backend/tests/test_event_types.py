"""自定义事件类型：清单合并、key 生成、CRUD、归档、以及「库里一有自定义类型 GET /events 不能 500」。"""

from app.behavior_categories import CATEGORIES, ONLINE_CATEGORIES

BUILTIN_KEYS = [
    "LEARNING_START",
    "MEAL_START",
    "BED_START",
    "OUT_START",
    "EXERCISE_START",
    "CHORES_START",
    "SOCIAL_OFFLINE_START",
    "OTHER_START",
]


def _create(client, **payload):
    body = {"label": "冥想", "category": "other", **payload}
    r = client.post("/event-types", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ── 清单：内置合成 + 排除不可记录项 ─────────────────────────


def test_list_event_types_builtin_only(client):
    items = client.get("/event-types").json()
    assert [i["key"] for i in items] == BUILTIN_KEYS
    assert all(i["builtin"] and i["id"] is None for i in items)
    assert [i["order"] for i in items] == list(range(len(BUILTIN_KEYS)))
    assert items[0]["label"] == "学习" and items[0]["category"] == "study_work"


def test_list_event_types_excludes_unrecordable(client):
    keys = {i["key"] for i in client.get("/event-types").json()}
    # 在线行为（手机/游戏）由采集自动生成，旧键 SLEEP_START 已废弃
    assert keys.isdisjoint({"PHONE_START", "GAME_START", "SLEEP_START", "DEVICE_ACTIVITY"})


def test_record_buttons_are_offline_only(client):
    """记录按钮只留「设备看不到」的事：大类须落在线下类。

    唯一例外是「学习」（study_work）——线下读书/上课设备观测不到；就算与设备
    study_work 段同时发生，同类重叠时人工优先，不会重复计时。
    """
    items = client.get("/event-types").json()
    cats = {i["category"] for i in items}
    assert cats <= (set(CATEGORIES) - ONLINE_CATEGORIES) | {"study_work"}
    assert cats.isdisjoint({"game", "video", "browsing", "reading", "social"})
    assert {"exercise", "chores", "social_offline"} <= cats


def test_hidden_builtin_still_recordable_by_api(client):
    """撤下按钮 ≠ 禁止写入：旧键（GAME_START）与旧指标源仍需可解析，否则历史链路断。"""
    r = client.post("/events", json={"type": "GAME_START", "note": "历史键兼容"})
    assert r.status_code == 201, r.text
    assert r.json()["category"] == "game"


# ── 创建 ────────────────────────────────────────────────────


def test_create_custom_type_generates_safe_key(client):
    item = _create(client, label="冥想", category="other")
    assert item["key"].startswith("custom_")
    assert item["key"] != "custom_"
    # 不能以 _START 结尾：前端 eventTypeLabel 会按后缀去尾，否则标签变裸 key
    assert not item["key"].endswith("_START")
    assert item["builtin"] is False and isinstance(item["id"], int)
    assert item["label"] == "冥想" and item["category"] == "other"


def test_custom_type_appended_after_builtins(client):
    _create(client, label="冥想")
    items = client.get("/event-types").json()
    assert [i["key"] for i in items[: len(BUILTIN_KEYS)]] == BUILTIN_KEYS
    assert items[-1]["builtin"] is False
    assert items[-1]["order"] == len(BUILTIN_KEYS)


def test_create_custom_type_rejects_unknown_category(client, db_session):
    r = client.post("/event-types", json={"label": "冥想", "category": "nope"})
    assert r.status_code == 422


def test_create_custom_type_rejects_blank_label(client, db_session):
    r = client.post("/event-types", json={"label": "   ", "category": "other"})
    assert r.status_code == 422


def test_create_custom_type_rejects_bad_icon(client, db_session):
    r = client.post(
        "/event-types", json={"label": "冥想", "category": "other", "icon": "有中文"}
    )
    assert r.status_code == 422


# ── 用自定义类型记录（守住「GET /events 500」的坑） ─────────


def test_custom_type_event_round_trip(client):
    custom = _create(client, label="冥想", category="other")

    created = client.post(
        "/events", json={"type": custom["key"], "note": "十分钟"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["type"] == custom["key"]
    # 大类由类型推导（未显式传 category）
    assert created.json()["category"] == "other"

    listed = client.get("/events")
    assert listed.status_code == 200, listed.text
    assert [e["type"] for e in listed.json()] == [custom["key"]]


def test_custom_type_respects_explicit_category(client):
    custom = _create(client, label="冥想", category="other")
    created = client.post(
        "/events", json={"type": custom["key"], "category": "exercise"}
    )
    assert created.status_code == 201
    assert created.json()["category"] == "exercise"


def test_unknown_type_rejected(client, db_session):
    assert client.post("/events", json={"type": "NOPE_START"}).status_code == 422
    assert client.post("/events", json={"type": "custom_deadbeef"}).status_code == 422


# ── 修订 / 归档 ─────────────────────────────────────────────


def test_update_custom_type(client):
    custom = _create(client, label="冥想", category="other")
    r = client.patch(
        f"/event-types/{custom['id']}",
        json={"label": "打坐", "category": "exercise", "icon": "FlowerLotus"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "打坐"
    assert r.json()["category"] == "exercise"
    assert r.json()["icon"] == "FlowerLotus"
    # key 不随改名变化：事件的 type 快照仍指得回来
    assert r.json()["key"] == custom["key"]


def test_update_custom_type_not_found(client, db_session):
    assert client.patch("/event-types/999", json={"label": "x"}).status_code == 404


def test_archive_hides_type_but_keeps_history(client):
    custom = _create(client, label="冥想", category="other")
    ev = client.post("/events", json={"type": custom["key"]}).json()

    assert client.delete(f"/event-types/{custom['id']}").status_code == 204

    # 默认清单不含
    assert custom["key"] not in {i["key"] for i in client.get("/event-types").json()}
    # 显式要归档的能看到
    archived = {i["key"]: i for i in client.get("/event-types?include_archived=true").json()}
    assert archived[custom["key"]]["archived"] is True
    # 不能再新建该类型
    assert client.post("/events", json={"type": custom["key"]}).status_code == 422
    # 历史事件照旧可读（type/category 是快照）
    got = client.get(f"/events/{ev['id']}")
    assert got.status_code == 200
    assert got.json()["type"] == custom["key"]
    assert got.json()["category"] == "other"


def test_archive_not_found(client, db_session):
    assert client.delete("/event-types/999").status_code == 404


# ── 实验指标源接受自定义类型 ────────────────────────────────


def test_metric_source_accepts_custom_type(client):
    custom = _create(client, label="冥想", category="other")
    ok = client.post(
        "/experiments",
        json={
            "name": "冥想实验",
            "metrics": [
                {
                    "key": "med_min",
                    "name": "冥想时长",
                    "source": f"event_duration:{custom['key']}",
                }
            ],
        },
    )
    assert ok.status_code == 201, ok.text
    # 内置白名单仍然生效（拼错的内置类型照样拒）
    bad = client.post(
        "/experiments",
        json={
            "name": "x",
            "metrics": [{"key": "x", "name": "x", "source": "event_duration:LEARNNG_START"}],
        },
    )
    assert bad.status_code == 422
