from app.services import memory_service, profile_service


def _make(client, category="habit", content="习惯晚睡，通常凌晨一点后入睡"):
    return client.post("/profile/facts", json={"category": category, "content": content})


def test_create_and_list_profile_fact(client):
    r = _make(client)
    assert r.status_code == 201
    data = r.json()
    assert data["category"] == "habit"
    assert data["source"] == "manual"
    assert data["status"] == "active"
    assert data["last_confirmed_at"] is not None

    rows = client.get("/profile/facts").json()
    assert [f["content"] for f in rows] == ["习惯晚睡，通常凌晨一点后入睡"]


def test_create_rejects_bad_category_and_blank(client):
    assert _make(client, category="unknown").status_code == 422
    assert client.post(
        "/profile/facts", json={"category": "habit", "content": "   "}
    ).status_code == 422


def test_duplicate_rejected_409(client):
    assert _make(client).status_code == 201
    dup = _make(client, content="习惯晚睡 ， 通常凌晨一点后入睡")  # 规范化后相同
    assert dup.status_code == 409
    assert "duplicate" in dup.json()["detail"]


def test_update_refreshes_confirmation(client):
    fact = _make(client).json()
    r = client.patch(
        f"/profile/facts/{fact['id']}",
        json={"content": "工作日通常凌晨一点后入睡"},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "工作日通常凌晨一点后入睡"
    assert r.json()["last_confirmed_at"] is not None


def test_archive_excludes_from_context(client):
    fact = _make(client).json()
    r = client.patch(f"/profile/facts/{fact['id']}", json={"status": "archived"})
    assert r.status_code == 200
    assert r.json()["status"] == "archived"

    assert client.get("/profile/facts").json() == []
    archived = client.get("/profile/facts?include_archived=true").json()
    assert len(archived) == 1


def test_delete_profile_fact(client):
    fact = _make(client).json()
    assert client.delete(f"/profile/facts/{fact['id']}").status_code == 204
    assert client.patch(
        f"/profile/facts/{fact['id']}", json={"status": "archived"}
    ).status_code == 404


def test_category_quota(client, monkeypatch):
    import app.services.profile_service as ps

    monkeypatch.setitem(ps.CATEGORY_LIMITS, "identity", 1)
    assert _make(client, category="identity", content="一名后端工程师").status_code == 201
    over = _make(client, category="identity", content="同时也是终身学习者")
    assert over.status_code == 409
    assert "上限" in over.json()["detail"]


def test_prompt_context_groups_active_only(client, db_session):
    _make(client, category="identity", content="一名后端工程师")
    _make(client, category="habit", content="习惯晚睡")
    archived = _make(client, category="preference", content="喜欢安静环境").json()
    client.patch(f"/profile/facts/{archived['id']}", json={"status": "archived"})

    ctx = profile_service.prompt_context(db_session, user_id=1)
    assert "[身份] 一名后端工程师" in ctx
    assert "[习惯] 习惯晚睡" in ctx
    assert "喜欢安静环境" not in ctx


def test_ask_memory_injects_background(db_session, monkeypatch):
    """边界二：ask 必须注入画像背景，且与召回片段分开标注。"""
    profile_service.create_fact(
        db_session, category="identity", content="一名后端工程师",
        user_id=1,
    )
    from datetime import datetime, timezone

    from app.models.thought import Thought

    db_session.add(
        Thought(
            user_id=1,
            content="今天写了很多代码",
            timestamp=datetime(2026, 9, 9, tzinfo=timezone.utc),
        )
    )
    db_session.commit()
    memory_service.store_memory(
        db_session, kind="insight", content="编码让我快乐", embed_vectors=False,
        user_id=1,
    )

    captured = {}

    def fake_chat_json(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return {"answer": "你是一名后端工程师。"}

    monkeypatch.setattr(memory_service, "chat_json", fake_chat_json)
    answer, sources = memory_service.ask_memory(db_session, "编码相关的问题", top_k=3, user_id=1)
    assert "后端工程师" in answer
    assert "一名后端工程师" in captured["system"]
    assert "关于用户的长期背景" in captured["system"]
    assert sources


def test_ask_memory_background_only_can_answer(db_session, monkeypatch):
    """无召回片段但有画像时也应能回答（背景是常驻上下文）。"""
    profile_service.create_fact(
        db_session, category="identity", content="一名后端工程师",
        user_id=1,
    )
    called = []

    def fake_chat_json(system, user, **kw):
        called.append(system)
        return {"answer": "你是工程师。"}

    monkeypatch.setattr(memory_service, "chat_json", fake_chat_json)
    answer, sources = memory_service.ask_memory(db_session, "我是谁", top_k=3, user_id=1)
    assert called
    assert sources == []
    assert "没有找到" not in answer
