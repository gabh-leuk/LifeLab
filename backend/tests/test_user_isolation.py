"""跨用户隔离：一个账号的接口绝不能看到另一个账号的数据。

这是用户模块的安全底线测试。每批 `user_id` 显式化都往这里加该批的用例，
按批分组，方便对照「哪一批覆盖了哪些路由」。

做法统一为两条：
1. 写：以 A 身份造数据，切到 B 后列表为空、取详情/改/删 404；
2. 读：以 B 身份取聚合结果，里面不能混进 A 的原始行。

**默认身份是 1（见 conftest 的 STUB_USER_ID），所以「A」通常就是默认的 client。**
"""

from datetime import datetime, timezone

import pytest

from app.models.device import Device
from app.models.device_usage import DeviceUsageHourly
from app.models.event import Event, EventType
from app.models.event_type import CustomEventType
from app.models.problem import Problem
from app.models.profile import ProfileFact
from app.models.state import StateRecord
from app.models.thought import Thought
from app.services import memory_service

DAY = "2026-09-02"
NOON = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)


# ── 批 3.1：events / thoughts / states / summary / timeline ──────────────


def test_events_are_scoped_to_current_user(client, db_session, login_as):
    created = client.post("/events", json={"type": "LEARNING_START"}).json()

    login_as(2)
    assert client.get("/events").json() == []
    assert client.get(f"/events/{created['id']}").status_code == 404
    assert client.patch(
        f"/events/{created['id']}", json={"note": "越权改"}
    ).status_code == 404
    assert client.delete(f"/events/{created['id']}").status_code == 404

    # 越权写也不能落到别人头上：B 建的行必须归 B
    own = client.post("/events", json={"type": "GAME_START"}).json()
    assert own["user_id"] == 2


def test_thoughts_are_scoped_to_current_user(client, db_session, login_as):
    created = client.post("/thoughts", json={"content": "只有我能看"}).json()

    login_as(2)
    assert client.get("/thoughts").json() == []
    assert client.delete(f"/thoughts/{created['id']}").status_code == 404
    assert client.post("/thoughts", json={"content": "我的"}).json()["user_id"] == 2


def test_states_are_scoped_to_current_user(client, db_session, login_as):
    created = client.post(
        "/states", json={"energy": 3, "focus": 3, "irritation": 2}
    ).json()

    login_as(2)
    assert client.get("/states").json() == []
    assert client.delete(f"/states/{created['id']}").status_code == 404
    own = client.post("/states", json={"energy": 1, "focus": 1, "irritation": 5}).json()
    assert own["user_id"] == 2


def test_summary_is_scoped_to_current_user(client, db_session, login_as):
    db_session.add_all(
        [
            Event(user_id=1, timestamp=NOON, type=EventType.LEARNING_START.value),
            Thought(user_id=1, timestamp=NOON, content="A 的想法"),
            StateRecord(user_id=1, timestamp=NOON, energy=4, focus=4, irritation=1),
        ]
    )
    db_session.commit()

    assert client.get(f"/summary/{DAY}").json()["event_total"] == 1

    login_as(2)
    data = client.get(f"/summary/{DAY}").json()
    assert data["event_total"] == 0
    assert data["by_type"] == []
    assert data["thought_total"] == 0
    assert data["state_total"] == 0
    assert data["avg_energy"] is None


def test_timeline_is_scoped_to_current_user(client, db_session, login_as):
    db_session.add(
        Event(user_id=1, timestamp=NOON, type=EventType.LEARNING_START.value)
    )
    db_session.commit()

    assert len(client.get(f"/timeline/{DAY}").json()["segments"]) == 1

    login_as(2)
    body = client.get(f"/timeline/{DAY}").json()
    assert body["segments"] == []
    assert body["device_segments"] == []
    assert body["device_hours"] == []


def test_timeline_does_not_borrow_other_users_device_apps(
    client, db_session, login_as
):
    """A 的设备应用明细不能摊到 B 的时间轴段上。

    `_attach_device_apps` 早先没把 user 透传给 `day_app_usage_for_windows`，
    于是 B 请求时间轴时会拿到 A 的 hourly 行并挂到自己的段里——这条是那个的护栏。
    """
    device = Device(
        user_id=1, name="A 的电脑", platform="pc", token_hash="a" * 64
    )
    db_session.add(device)
    db_session.flush()
    db_session.add(
        DeviceUsageHourly(
            user_id=1,
            device_id=device.id,
            date=DAY,
            hour=9,
            app="Code.exe",
            label="Visual Studio Code",
            seconds=1800,
        )
    )
    db_session.commit()

    # A 自己看得到（先确认数据真的能被查出来，免得用例因为别的原因假绿）
    db_session.add(
        Event(user_id=1, timestamp=NOON, type=EventType.LEARNING_START.value)
    )
    db_session.commit()
    mine = client.get(f"/timeline/{DAY}").json()["segments"]
    assert [a["app"] for a in mine[0]["device_apps"]] == ["Code.exe"]

    # B 的同一时刻必须看不到
    login_as(2)
    db_session.add(
        Event(user_id=2, timestamp=NOON, type=EventType.LEARNING_START.value)
    )
    db_session.commit()
    theirs = client.get(f"/timeline/{DAY}").json()["segments"]
    assert len(theirs) == 1
    assert theirs[0]["device_apps"] == []


# ── 批 3.2：problems / findings ────────────────────────────────────────


def test_problems_are_scoped_to_current_user(client, db_session, login_as):
    created = client.post("/problems", json={"title": "为什么睡前放不下手机？"}).json()

    assert len(client.get("/problems").json()) == 1

    login_as(2)
    assert client.get("/problems").json() == []
    assert client.get(f"/problems/{created['id']}").status_code == 404
    assert client.patch(
        f"/problems/{created['id']}", json={"note": "越权改"}
    ).status_code == 404
    assert client.delete(f"/problems/{created['id']}").status_code == 404
    assert client.post("/problems", json={"title": "我的问题"}).json()["user_id"] == 2


def test_findings_are_scoped_to_current_user(client, db_session, login_as):
    created = client.post(
        "/findings",
        json={"title": "手机放客厅那周专注更高", "observation": "干预期专注均值上升"},
    ).json()

    assert len(client.get("/findings").json()) == 1

    login_as(2)
    assert client.get("/findings").json() == []
    assert client.get(f"/findings/{created['id']}").status_code == 404
    assert client.patch(
        f"/findings/{created['id']}", json={"title": "越权改"}
    ).status_code == 404
    assert client.delete(f"/findings/{created['id']}").status_code == 404
    own = client.post(
        "/findings", json={"title": "我的发现", "observation": "只有我能看"}
    ).json()
    assert own["user_id"] == 2


# ── 批 3.3：profile（背景 + 晋升扫描） ────────────────────────────────


def test_profile_is_scoped_to_current_user(client, db_session, login_as):
    memory_service.store_memory(
        db_session,
        kind="pattern",
        content="晚上更容易刷手机",
        source_ref="pattern:2026-W36",
        embed_vectors=False,
        user_id=1,
    )
    memory_service.store_memory(
        db_session,
        kind="pattern",
        content="夜里刷手机时间偏长",
        source_ref="pattern:2026-W37",
        embed_vectors=False,
        user_id=1,
    )
    created = client.post(
        "/profile/facts", json={"category": "habit", "content": "习惯晚睡"}
    ).json()

    assert len(client.get("/profile/facts").json()) == 1
    # 扫描的「素材计数」也必须按用户算，否则别处的 pattern 会让候选凭空出现
    assert client.post("/profile/promotions/scan").json()["scanned_patterns"] == 2

    login_as(2)
    assert client.get("/profile/facts").json() == []
    assert client.post("/profile/promotions/scan").json()["scanned_patterns"] == 0
    assert client.patch(
        f"/profile/facts/{created['id']}", json={"content": "越权改"}
    ).status_code == 404
    assert client.delete(f"/profile/facts/{created['id']}").status_code == 404
    own = client.post(
        "/profile/facts", json={"category": "identity", "content": "我是我"}
    ).json()
    assert own["user_id"] == 2


# ── 批 3.4：memory（列表/检索/统计/级联）+ agent ──────────────────────


def test_memory_list_search_stats_are_scoped_to_current_user(
    client, db_session, login_as
):
    """SQLite 下检索走关键词兜底，测的是同一套 user 过滤。"""
    memory_service.store_memory(
        db_session,
        kind="insight",
        content="睡前把手机放客厅更容易入睡",
        source_ref="insight:2026-09-02",
        embed_vectors=False,
        user_id=1,
    )

    # 先确认 A 查得到（免得用例因为检索词不合适而假绿）
    assert len(client.get("/memory/items").json()) == 1
    assert client.post("/memory/search", json={"query": "睡前 手机"}).json()["hits"]
    assert client.get("/memory/stats").json()["total"] == 1

    login_as(2)
    assert client.get("/memory/items").json() == []
    assert client.post("/memory/search", json={"query": "睡前 手机"}).json()["hits"] == []
    assert client.get("/memory/stats").json()["total"] == 0
    # 来源表计数也要按用户算，否则别人的 events 会让统计虚高
    assert client.get("/memory/stats").json()["source_counts"]["events"] == 0


def test_memory_vector_search_is_scoped_to_current_user(
    client, db_session, login_as
):
    """向量路径的隔离（仅 PG 有向量列；SQLite 走文本兜底，故跳过）。"""
    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("向量检索仅 PG 可用")
    memory_service.store_memory(
        db_session,
        kind="insight",
        content="睡前把手机放客厅更容易入睡",
        source_ref="insight:2026-09-02",
        user_id=1,
    )

    assert client.post("/memory/search", json={"query": "睡前玩手机"}).json()["hits"]

    login_as(2)
    assert client.post("/memory/search", json={"query": "睡前玩手机"}).json()["hits"] == []


def test_deleting_own_source_clears_own_memory_index(client, db_session, login_as):
    """删记录要连带清掉**自己的**记忆索引行。

    漏传 user_id 时会按 user 1 去找，于是 B 删掉自己的事件后
    `event:<id>` 的索引行留在库里成为孤儿（指向已删除的记录）。
    """
    login_as(2)
    event = client.post("/events", json={"type": "LEARNING_START"}).json()
    memory_service.store_memory(
        db_session,
        kind="event",
        content="今天开始学 FastAPI",
        source_ref=f"event:{event['id']}",
        embed_vectors=False,
        user_id=2,
    )
    assert len(client.get("/memory/items").json()) == 1

    assert client.delete(f"/events/{event['id']}").status_code == 204
    assert client.get("/memory/items").json() == []


# ── 批 3.5：devices / app_rules / 设备事件重建 ─────────────────────────


def test_devices_are_scoped_to_current_user(client, db_session, login_as):
    """新建设备必须归当前用户（默认落 1 会把个人账号的设备挂到 demo）。"""
    mine = client.post(
        "/devices", json={"name": "演示电脑", "platform": "pc"}
    ).json()["device"]
    assert mine["user_id"] == 1

    login_as(2)
    theirs = client.post(
        "/devices", json={"name": "我的手机", "platform": "android"}
    ).json()["device"]
    assert theirs["user_id"] == 2
    assert [d["id"] for d in client.get("/devices").json()] == [theirs["id"]]

    # 越权吊销 404（不能删别人的设备，连带其使用数据）
    assert client.delete(f"/devices/{mine['id']}").status_code == 404


def test_app_rules_are_scoped_to_current_user(client, db_session, login_as):
    created = client.post(
        "/app-rules", json={"match_value": "secret_app.exe", "category": "game"}
    ).json()
    assert len(client.get("/app-rules").json()) == 1

    login_as(2)
    assert client.get("/app-rules").json() == []
    assert client.patch(
        f"/app-rules/{created['id']}", json={"category": "study_work"}
    ).status_code == 404
    assert client.delete(f"/app-rules/{created['id']}").status_code == 404
    own = client.post(
        "/app-rules", json={"match_value": "my_app.exe", "category": "game"}
    ).json()
    assert [r["id"] for r in client.get("/app-rules").json()] == [own["id"]]


def test_rebuild_range_captures_calling_user(client, monkeypatch, login_as):
    """后台任务拿不到请求依赖：重算的 user_id 必须在请求期捉住再传下去。"""
    seen = []
    monkeypatch.setattr(
        "app.services.rebuild_queue.rebuild_activity_range",
        lambda start, end, *, user_id, tz_offset=0: seen.append(user_id),
    )

    login_as(2)
    r = client.post("/devices/rebuild-activity-range?start=2026-09-05&end=2026-09-05")
    assert r.status_code == 202, r.text
    assert seen == [2]


def test_rebuild_for_date_is_scoped_to_given_user(db_session):
    """重算只看该账号的设备：给别人重算时既不能碰他的数据，也不能漏算自己的。"""
    from sqlalchemy import select

    from app.services import behavior_service

    device = Device(user_id=2, name="B 的电脑", platform="pc", token_hash="b" * 64)
    db_session.add(device)
    db_session.flush()
    db_session.add(
        DeviceUsageHourly(
            user_id=2,
            device_id=device.id,
            date=DAY,
            hour=9,
            app="Code.exe",
            label="Visual Studio Code",
            seconds=1800,
        )
    )
    db_session.commit()

    def _device_events():
        return db_session.scalars(
            select(Event).where(Event.source == "DEVICE")
        ).all()

    # 按 user 1 重算：user 2 的设备不在范围内，不该产生任何 DEVICE 事件
    behavior_service.rebuild_for_date(db_session, DAY, user_id=1, tz_offset=0)
    assert _device_events() == []

    result = behavior_service.rebuild_for_date(db_session, DAY, user_id=2, tz_offset=0)
    assert result["created"] == 1
    assert [e.user_id for e in _device_events()] == [2]


# ── 批 3.6：experiments（含 AUTO 聚合取数） ────────────────────────────


def test_experiments_are_scoped_to_current_user(client, db_session, login_as):
    created = client.post("/experiments", json={"name": "睡前手机实验"}).json()
    log = client.post(
        f"/experiments/{created['id']}/logs", json={"metric": "phone_min", "value": 60}
    ).json()
    assert len(client.get("/experiments").json()) == 1

    login_as(2)
    assert client.get("/experiments").json() == []
    assert client.get(f"/experiments/{created['id']}").status_code == 404
    assert client.patch(
        f"/experiments/{created['id']}", json={"name": "越权改"}
    ).status_code == 404
    assert client.get(f"/experiments/{created['id']}/logs").status_code == 404
    assert client.delete(
        f"/experiments/{created['id']}/logs/{log['id']}"
    ).status_code == 404
    assert client.get(f"/experiments/{created['id']}/stats").status_code == 404
    assert client.post(
        f"/experiments/{created['id']}/status", json={"status": "RUNNING"}
    ).status_code == 404
    assert client.post(
        f"/experiments/{created['id']}/conclude",
        json={
            "status": "CONCLUDED",
            "verdict": "SUPPORTED",
            "conclusion": "有效",
            "conclusion_reason": "专注均值上升",
        },
    ).status_code == 404
    assert client.delete(f"/experiments/{created['id']}").status_code == 404

    own = client.post("/experiments", json={"name": "我的实验"}).json()
    assert own["user_id"] == 2
    assert [e["id"] for e in client.get("/experiments").json()] == [own["id"]]


def test_auto_aggregate_reads_only_own_data(client, db_session, login_as):
    """AUTO 点取数必须按实验所有者：别人的状态数据不能给当前实验出点。

    `_day_value` 与 `_experiments_covering` 早先都写死 user 1 ——
    前者会让 B 的实验用 A 的状态数据出点，后者会让复盘同步写到 A 的实验上。
    """
    now = datetime.now(timezone.utc)
    db_session.add(
        StateRecord(user_id=2, timestamp=now, energy=5, focus=5, irritation=1)
    )
    db_session.commit()

    exp = client.post(
        "/experiments",
        json={
            "name": "专注度观察",
            "metrics": [
                {
                    "key": "focus",
                    "name": "专注",
                    "direction": "up_good",
                    "source": "state_avg:focus",
                }
            ],
        },
    ).json()
    client.post(f"/experiments/{exp['id']}/status", json={"status": "RUNNING"})

    # A 的状态数据不能给 B 的实验出点
    assert client.post(f"/experiments/{exp['id']}/aggregate").json()["created"] == 0

    # 补上自己的状态数据 → 出点（正向对照，证明指标/窗口本身是好的）
    db_session.add(
        StateRecord(user_id=1, timestamp=now, energy=3, focus=4, irritation=2)
    )
    db_session.commit()
    assert client.post(f"/experiments/{exp['id']}/aggregate").json()["created"] == 1


# ── 批 3.7：reviews / period reviews / day notes / analysis ────────────

REVIEW_JSON = {
    "day_summary": "上午学习。",
    "highlights": ["完成学习"],
    "concerns": [],
    "patterns": [],
    "suggestions": ["明天继续"],
}

WEEK_JSON = {
    "period_summary": "本周学习稳定。",
    "patterns": ["上午专注更高(推测)"],
    "missed_insights": [],
    "suggestions": ["保持"],
}


def test_reviews_are_scoped_to_current_user(client, db_session, login_as, monkeypatch):
    """复盘只读自己的原始记录，产物也只归自己。

    快照读错人时 B 的复盘里会出现 A 的事件，所以这里直接看落库的
    `source_data["events"]`，比只看 404 更能发现“读串了”。
    """
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: REVIEW_JSON
    )
    db_session.add(
        Event(user_id=1, timestamp=NOON, type=EventType.LEARNING_START.value)
    )
    db_session.commit()

    mine = client.post("/reviews", json={"date": DAY}).json()
    assert mine["source_data"]["events"][0]["type"] == "LEARNING_START"

    login_as(2)
    assert client.get(f"/reviews/{DAY}").status_code == 404
    assert client.delete(f"/reviews/{DAY}").status_code == 404

    theirs = client.post("/reviews", json={"date": DAY}).json()
    assert theirs["user_id"] == 2
    assert theirs["source_data"]["events"] == []

    login_as(1)
    assert client.get(f"/reviews/{DAY}").json()["user_id"] == 1


def test_period_reviews_are_scoped_to_current_user(
    client, db_session, login_as, monkeypatch
):
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json", lambda *a, **k: WEEK_JSON
    )
    memory_service.store_memory(
        db_session,
        kind="insight",
        content="睡前把手机放客厅更容易入睡",
        source_ref=f"insight:{DAY}",
        embed_vectors=False,
        user_id=1,
    )

    assert (
        client.post(
            "/reviews/period", json={"period_type": "week", "date": DAY}
        ).json()["user_id"]
        == 1
    )
    assert client.get(f"/reviews/period/week/{DAY}").status_code == 200

    login_as(2)
    assert client.get(f"/reviews/period/week/{DAY}").status_code == 404

    # B 也能生成，但素材只有自己的：A 的洞察不能让他“有素材”（否则就是读串了）
    theirs = client.post(
        "/reviews/period", json={"period_type": "week", "date": DAY}
    ).json()
    assert theirs["user_id"] == 2
    assert theirs["structured"] is None


def test_day_notes_are_scoped_to_current_user(client, db_session, login_as):
    client.put(f"/day-notes/{DAY}", json={"content": "A 的小结"})
    assert client.get(f"/day-notes/{DAY}").json()["content"] == "A 的小结"

    login_as(2)
    assert client.get(f"/day-notes/{DAY}").json()["content"] == ""
    client.put(f"/day-notes/{DAY}", json={"content": "B 的小结"})

    login_as(1)
    assert client.get(f"/day-notes/{DAY}").json()["content"] == "A 的小结"


def test_analysis_commit_writes_to_current_user(client, db_session, login_as):
    """勾选落库必须归当前用户（默认落 1 会把个人账号的剖析结果写进 demo）。"""
    login_as(2)
    body = client.post(
        "/analysis/commit",
        json={
            "problems": [{"title": "为什么睡前放不下手机？"}],
            "background": [{"category": "habit", "content": "习惯晚睡"}],
        },
    ).json()
    assert len(body["problem_ids"]) == 1
    assert db_session.get(Problem, body["problem_ids"][0]).user_id == 2
    assert db_session.get(ProfileFact, body["profile_fact_ids"][0]).user_id == 2

    login_as(1)
    assert client.get("/problems").json() == []
    assert client.get("/profile/facts").json() == []


def test_analysis_distill_reads_only_own_problems(
    client, db_session, login_as, monkeypatch
):
    """去重清单只列自己的问题：否则 B 剖析时会被 A 的问题挡住（误判重复）。"""
    captured = {}

    def fake_chat_json(system, user, **kw):
        captured["user"] = user
        return {"problem_candidates": [], "background_candidates": []}

    monkeypatch.setattr("app.services.analysis_service.chat_json", fake_chat_json)
    client.post("/problems", json={"title": "只有 A 的问题"})

    client.post("/analysis/distill", json={"question": "问", "answer": "答"})
    assert "只有 A 的问题" in captured["user"]

    login_as(2)
    client.post("/analysis/distill", json={"question": "问", "answer": "答"})
    assert "只有 A 的问题" not in captured["user"]


# ── 批 3.8：event types（自定义类型与类型解析） ─────────────────────────


def test_event_types_are_scoped_to_current_user(client, db_session, login_as):
    """自定义类型属于建它的人，且**解析类型 key 时也不能借用别人的**。

    解析漏传 user 时，B 拿自己的自定义类型记事件会被判成「未知的事件类型」，
    而 A 的自定义 key 反倒能被 B 用上。
    """
    mine = client.post(
        "/event-types", json={"label": "复盘", "category": "study_work"}
    ).json()
    my_key = mine["key"]
    custom = db_session.get(CustomEventType, mine["id"])
    assert custom.user_id == 1
    assert client.post("/events", json={"type": my_key}).status_code == 201

    login_as(2)
    keys = [t["key"] for t in client.get("/event-types").json()]
    assert my_key not in keys
    assert client.patch(
        f"/event-types/{mine['id']}", json={"label": "越权改"}
    ).status_code == 404
    assert client.delete(f"/event-types/{mine['id']}").status_code == 404
    # 别人的自定义类型不能拿来记事件
    assert client.post("/events", json={"type": my_key}).status_code == 422

    theirs = client.post(
        "/event-types", json={"label": "我的类型", "category": "study_work"}
    ).json()
    assert db_session.get(CustomEventType, theirs["id"]).user_id == 2
    assert [t["key"] for t in client.get("/event-types").json()].count(theirs["key"]) == 1
    assert client.post("/events", json={"type": theirs["key"]}).status_code == 201
