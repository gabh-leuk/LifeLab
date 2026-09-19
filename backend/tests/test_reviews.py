from datetime import datetime, timezone

from app.models.event import Event, EventSource, EventType
from app.models.state import StateRecord
from app.models.thought import Thought
from app.services import device_service


def _seed_day(db_session, date_str="2026-09-02"):
    base = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Event(user_id=1, timestamp=base, type=EventType.LEARNING_START.value, note="学 FastAPI"),
            Event(user_id=1, timestamp=base, type=EventType.GAME_START.value),
            Thought(user_id=1, timestamp=base, content="为什么要害怕认真"),
            StateRecord(user_id=1, timestamp=base, energy=4, focus=3, irritation=1),
        ]
    )
    db_session.commit()


GOOD_JSON = {
    "day_summary": "上午学习，傍晚游戏，状态整体不错。",
    "highlights": ["完成学习", "记录了想法"],
    "concerns": ["游戏时段可能过长(推测)"],
    "patterns": ["学习后精力下降(推测)"],
    "suggestions": ["明天上午继续学习"],
}


def test_generate_review_with_llm(client, db_session, monkeypatch):
    _seed_day(db_session)
    captured = {}

    def fake_chat_json(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return GOOD_JSON

    monkeypatch.setattr("app.services.review_service.chat_json", fake_chat_json)

    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "上午学习" in data["structured"]["day_summary"]
    assert "## 2026-09-02" in data["review_text"]
    assert "由 LLM 生成" in data["review_text"]
    assert data["source_data"]["events"][0]["type"] == "LEARNING_START"
    assert "完整事件序列" in captured["user"]


def test_generate_review_idempotent_no_force(client, db_session, monkeypatch):
    _seed_day(db_session)
    monkeypatch.setattr("app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON)

    r1 = client.post("/reviews", json={"date": "2026-09-02"})
    r2 = client.post("/reviews", json={"date": "2026-09-02"})
    assert r1.json()["review_text"] == r2.json()["review_text"]
    assert r2.json()["id"] == r1.json()["id"]


def test_generate_review_force_regenerates(client, db_session, monkeypatch):
    _seed_day(db_session)
    calls = []

    def fake(system, user, **kw):
        calls.append(1)
        return GOOD_JSON

    monkeypatch.setattr("app.services.review_service.chat_json", fake)
    client.post("/reviews", json={"date": "2026-09-02"})
    client.post("/reviews", json={"date": "2026-09-02"})
    assert len(calls) == 1
    client.post("/reviews", json={"date": "2026-09-02", "force": True})
    assert len(calls) == 2


def test_generate_review_llm_failure_saved_as_error(client, db_session, monkeypatch):
    _seed_day(db_session)
    from app.llm import LLMError

    def boom(system, user, **kw):
        raise LLMError("network down")

    monkeypatch.setattr("app.services.review_service.chat_json", boom)
    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "error"
    assert "network down" in (data["error"] or "")


def test_generate_review_retry_after_error(client, db_session, monkeypatch):
    """LLM 失败落 error 后，再次请求应自动重试（不被 error 记录挡住）。"""
    from app.llm import LLMError

    _seed_day(db_session)
    calls = []

    def flaky(system, user, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise LLMError("network down")
        return GOOD_JSON

    monkeypatch.setattr("app.services.review_service.chat_json", flaky)

    r1 = client.post("/reviews", json={"date": "2026-09-02"})
    assert r1.json()["status"] == "error"

    r2 = client.post("/reviews", json={"date": "2026-09-02"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "ok"
    assert len(calls) == 2  # 第二次真的重新调了 LLM


def test_generate_review_llm_invalid_json_saved_as_error(client, db_session, monkeypatch):
    _seed_day(db_session)
    monkeypatch.setattr(
        "app.services.review_service.chat_json",
        lambda *a, **k: {"day_summary": 123},  # 缺字段+类型错
    )
    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_generate_review_empty_day_no_llm_call(client, db_session, monkeypatch):
    called = []

    def fake(system, user, **kw):
        called.append(1)
        return GOOD_JSON

    monkeypatch.setattr("app.services.review_service.chat_json", fake)
    r = client.post("/reviews", json={"date": "2026-09-01"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "没有任何记录" in data["review_text"]
    assert not called  # 空数据不调 LLM


def test_get_review_404_when_missing(client):
    assert client.get("/reviews/2026-09-02").status_code == 404


def test_get_review_after_generate(client, db_session, monkeypatch):
    _seed_day(db_session)
    monkeypatch.setattr("app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON)
    client.post("/reviews", json={"date": "2026-09-02"})
    r = client.get("/reviews/2026-09-02")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_generate_review_stores_day_insights(client, db_session, monkeypatch):
    """复盘与 L1 洞察共用一次 LLM 调用，洞察直接落记忆索引。"""
    _seed_day(db_session)
    payload = {
        **GOOD_JSON,
        "insights": ["晚上学习效率低于下午(推测)", "记录想法能降低逃避倾向"],
    }
    calls = []
    monkeypatch.setattr(
        "app.services.review_service.chat_json",
        lambda system, user, **kw: calls.append(1) or payload,
    )

    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert len(calls) == 1  # 只调了一次 LLM

    got = client.get("/memory/extract/2026-09-02")
    assert got.status_code == 200
    contents = {i["content"] for i in got.json()["insights"]}
    assert contents == {"晚上学习效率低于下午(推测)", "记录想法能降低逃避倾向"}


def test_regenerate_review_replaces_insights(client, db_session, monkeypatch):
    """重新生成复盘时旧洞察被整体替换，不残留、不重复。"""
    _seed_day(db_session)
    payloads = iter(
        [
            {**GOOD_JSON, "insights": ["旧洞察A", "旧洞察B"]},
            {**GOOD_JSON, "insights": ["新洞察C"]},
        ]
    )
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: next(payloads)
    )

    client.post("/reviews", json={"date": "2026-09-02"})
    client.post("/reviews", json={"date": "2026-09-02", "force": True})

    got = client.get("/memory/extract/2026-09-02")
    contents = [i["content"] for i in got.json()["insights"]]
    assert contents == ["新洞察C"]


def test_generate_review_includes_device_usage(client, db_session, monkeypatch):
    """日复盘把「按小时聚合的设备数据」喂给 LLM，并留档到 source_data。"""
    _seed_day(db_session)
    dev = client.post("/devices", json={"name": "电脑", "platform": "pc"}).json()
    client.post(
        "/ingest/usage/hourly",
        json={
            "date": "2026-09-02",
            "entries": [
                {"app": "Code.exe", "label": "VS Code", "hour": 9, "seconds": 3600},
            ],
        },
        headers={"X-Device-Token": dev["token"]},
    )
    captured = {}
    monkeypatch.setattr(
        "app.services.review_service.chat_json",
        lambda system, user, **kw: captured.update(user=user) or GOOD_JSON,
    )

    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    assert "设备真实活动" in captured["user"]
    assert "VS Code" in captured["user"]

    du = r.json()["source_data"]["device_hours"]
    assert du and du[0]["hour"] == 9
    assert du[0]["seconds"] == 3600
    assert du[0]["apps"][0]["label"] == "VS Code"
    assert du[0]["apps"][0]["seconds"] == 3600


def test_generate_review_without_device_data_ok(client, db_session, monkeypatch):
    """无设备数据时复盘照常生成，prompt 不含设备块。"""
    _seed_day(db_session)
    captured = {}
    monkeypatch.setattr(
        "app.services.review_service.chat_json",
        lambda system, user, **kw: captured.update(user=user) or GOOD_JSON,
    )
    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    assert r.json()["source_data"]["device_hours"] == []
    assert "设备真实活动" not in captured["user"]


# ── 设备口径：复盘必须与时间轴/实验同源，不自算第二条路径 ────────


def _add_device_event(db_session, start, end, category, user_id=1):
    db_session.add(
        Event(
            user_id=user_id,
            timestamp=start,
            type="DEVICE_ACTIVITY",
            source=EventSource.DEVICE.value,
            category=category,
            ended_at=end,
        )
    )
    db_session.commit()


def _ingest_hourly(client, token, date, entries):
    r = client.post(
        "/ingest/usage/hourly",
        json={"date": date, "entries": entries},
        headers={"X-Device-Token": token},
    )
    assert r.status_code == 200, r.text


def test_review_device_seconds_reconcile_with_usage_total(client, db_session, monkeypatch):
    """复盘快照的设备秒数逐小时相加 == 实验口径 `usage_seconds_for_day`。

    旧实现按 `ended_at - timestamp` 整段计时、不裁剪到当日，两边对不上账
    （跨小时的段整份记给起点小时，跨日的段把次日时长也算进来）。
    """
    _seed_day(db_session)
    dev = client.post("/devices", json={"name": "电脑", "platform": "pc"}).json()
    _ingest_hourly(
        client, dev["token"], "2026-09-02",
        [
            {"app": "Code.exe", "label": "VS Code", "hour": 9, "seconds": 2400},
            {"app": "Code.exe", "label": "VS Code", "hour": 10, "seconds": 1200},
            {"app": "Code.exe", "label": "VS Code", "hour": 23, "seconds": 1200},
        ],
    )
    # 跨小时：09:40–10:20 各 20 分钟；跨日：23:40–次日 00:20 只算当日 20 分钟
    _add_device_event(
        db_session,
        datetime(2026, 9, 2, 9, 40, tzinfo=timezone.utc),
        datetime(2026, 9, 2, 10, 20, tzinfo=timezone.utc),
        "study_work",
    )
    _add_device_event(
        db_session,
        datetime(2026, 9, 2, 23, 40, tzinfo=timezone.utc),
        datetime(2026, 9, 3, 0, 20, tzinfo=timezone.utc),
        "study_work",
    )
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON
    )

    r = client.post("/reviews", json={"date": "2026-09-02"})
    du = {h["hour"]: h for h in r.json()["source_data"]["device_hours"]}

    # 秒数之和与共用口径一致
    assert sum(h["seconds"] for h in du.values()) == device_service.usage_seconds_for_day(
        db_session, "2026-09-02", user_id=1
    ) == 4800
    # 跨小时按重叠摊销，而非整段记给起点小时（旧口径 09 会记满 2400）
    assert du[9]["category_seconds"] == {"study_work": 1200}
    assert du[10]["category_seconds"] == {"study_work": 1200}
    # 跨日段裁到当日
    assert du[23]["category_seconds"] == {"study_work": 1200}


def test_review_device_prompt_keeps_activities_out_of_prompt(client, db_session, monkeypatch):
    """段落明细留在快照供追溯，但不进 prompt —— 逐条罗列与小时行重复且撑长 prompt。"""
    _seed_day(db_session)
    _add_device_event(
        db_session,
        datetime(2026, 9, 2, 9, tzinfo=timezone.utc),
        datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc),
        "study_work",
    )
    captured = {}
    monkeypatch.setattr(
        "app.services.review_service.chat_json",
        lambda system, user, **kw: captured.update(user=user) or GOOD_JSON,
    )
    r = client.post("/reviews", json={"date": "2026-09-02"})

    hours = r.json()["source_data"]["device_hours"]
    assert hours[0]["activities"][0]["category"] == "study_work"
    assert hours[0]["activities"][0]["ended_at"].startswith("2026-09-02T09:30")
    # prompt 里只有小时行
    assert "09:00 总 30分 · 学习工作 30分" in captured["user"]


def test_review_device_only_day_still_calls_llm(client, db_session, monkeypatch):
    """只有设备数据、没有人工记录的一天，仍要走 LLM。

    空判定读的是 `snapshot["device_hours"]`。这是换键时的暗雷：改成别的键之后
    「用户忘了记录、但采集器测到了一整天」会被判成"没有任何记录"而直接短路。
    """
    dev = client.post("/devices", json={"name": "电脑", "platform": "pc"}).json()
    _ingest_hourly(
        client, dev["token"], "2026-09-02",
        [{"app": "Code.exe", "label": "VS Code", "hour": 9, "seconds": 1800}],
    )
    _add_device_event(
        db_session,
        datetime(2026, 9, 2, 9, tzinfo=timezone.utc),
        datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc),
        "study_work",
    )
    captured = {}
    monkeypatch.setattr(
        "app.services.review_service.chat_json",
        lambda system, user, **kw: captured.update(user=user) or GOOD_JSON,
    )

    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["structured"] is not None          # 短路占位行会是 None
    assert "无法复盘" not in r.json()["review_text"]
    assert "设备真实活动" in captured["user"]           # LLM 确实收到了设备块
