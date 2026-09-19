from datetime import datetime, timezone

from app.models.event import Event, EventType
from app.models.thought import Thought


def _seed_day(db_session, date_str="2026-09-02"):
    base = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Event(user_id=1, timestamp=base, type=EventType.LEARNING_START.value, note="学 FastAPI"),
            Thought(user_id=1, timestamp=base, content="为什么要害怕认真"),
        ]
    )
    db_session.commit()


GOOD_JSON = {
    "day_summary": "上午学习，状态还行。",
    "highlights": ["完成学习"],
    "concerns": ["作息偏晚(推测)"],
    "patterns": ["学习后精力下降(推测)"],
    "suggestions": ["明天早点睡"],
}


def test_day_note_empty_when_missing(client):
    r = client.get("/day-notes/2026-09-02")
    assert r.status_code == 200
    data = r.json()
    assert data["date"] == "2026-09-02"
    assert data["content"] == ""
    assert data["updated_at"] is None


def test_day_note_roundtrip_and_update(client):
    r = client.put("/day-notes/2026-09-02", json={"content": "今天加班到很晚"})
    assert r.status_code == 200
    assert r.json()["content"] == "今天加班到很晚"
    assert r.json()["updated_at"] is not None

    r2 = client.put("/day-notes/2026-09-02", json={"content": "今天请假休息"})
    assert r2.json()["content"] == "今天请假休息"

    got = client.get("/day-notes/2026-09-02")
    assert got.json()["content"] == "今天请假休息"


def test_day_note_blank_clears(client):
    client.put("/day-notes/2026-09-02", json={"content": "临时记录"})
    r = client.put("/day-notes/2026-09-02", json={"content": "   "})
    assert r.json()["content"] == ""
    assert client.get("/day-notes/2026-09-02").json()["content"] == ""


def test_day_note_content_stripped(client):
    r = client.put("/day-notes/2026-09-02", json={"content": "  今晚早睡  "})
    assert r.json()["content"] == "今晚早睡"


def test_review_prompt_includes_day_note(client, db_session, monkeypatch):
    _seed_day(db_session)
    client.put(
        "/day-notes/2026-09-02",
        json={"content": "下午偏头痛，可能因为昨晚只睡了五小时"},
    )
    captured = {}

    def fake_chat_json(system, user, **kw):
        captured["user"] = user
        return GOOD_JSON

    monkeypatch.setattr("app.services.review_service.chat_json", fake_chat_json)
    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    assert "偏头痛" in captured["user"]
    assert r.json()["source_data"]["note"] == "下午偏头痛，可能因为昨晚只睡了五小时"


def test_review_from_note_only_still_calls_llm(client, db_session, monkeypatch):
    called = []

    def fake_chat_json(system, user, **kw):
        called.append(user)
        return GOOD_JSON

    monkeypatch.setattr("app.services.review_service.chat_json", fake_chat_json)
    client.put("/day-notes/2026-09-03", json={"content": "没记事件，但一直在纠结要不要换工作"})
    r = client.post("/reviews", json={"date": "2026-09-03"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert called  # 只有小结也应能复盘（小结是可选的输入而非闸门）
