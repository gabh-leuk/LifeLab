from app.models.memory import MemoryItem
from app.services import memory_service


def _seed(db_session):
    a = memory_service.store_memory(
        db_session, kind="thought", content="为什么要害怕认真做", embed_vectors=False,
        user_id=1,
    )
    b = memory_service.store_memory(
        db_session, kind="finding", content="晚睡后上午精力下降", embed_vectors=False,
        user_id=1,
    )
    return a, b


def test_store_memory_sqlite(db_session):
    item = memory_service.store_memory(
        db_session, kind="event", content="学 FastAPI", source_ref="evt:1",
        embed_vectors=False,
        user_id=1,
    )
    assert item.id is not None
    assert item.kind == "event"
    assert item.source_ref == "evt:1"
    # SQLite 无向量能力 → 不生成向量
    assert item.embedding is None


def test_list_memories(db_session):
    _seed(db_session)
    items = memory_service.list_memories(db_session, user_id=1)
    assert len(items) == 2
    assert all(isinstance(i, MemoryItem) for i in items)


def test_list_memories_filter_kind(db_session):
    _seed(db_session)
    items = memory_service.list_memories(db_session, kind="thought", user_id=1)
    assert len(items) == 1
    assert items[0].kind == "thought"


def test_search_sqlite_fallback_keywords(db_session):
    _seed(db_session)
    hits = memory_service.search_memory(db_session, "害怕", top_k=2, user_id=1)
    assert len(hits) >= 1
    top = hits[0]
    assert top.item.kind == "thought"
    assert top.similarity > 0
    assert top.final_score > 0

    # 无关词 → 无命中
    hits2 = memory_service.search_memory(db_session, "zzz不存在的词", top_k=2, user_id=1)
    assert hits2 == []


def test_search_empty_db(db_session):
    hits = memory_service.search_memory(db_session, "anything", user_id=1)
    assert hits == []


def test_ask_memory_answer_with_sources(db_session, monkeypatch):
    _seed(db_session)
    from app.services import memory_service as ms

    def fake_chat_json(system, user, **kw):
        return {"answer": "你害怕认真可能是因为怕失败(推测)。"}

    monkeypatch.setattr(ms, "chat_json", fake_chat_json)
    answer, sources = ms.ask_memory(db_session, "害怕", top_k=3, user_id=1)
    assert "害怕" in answer or "失败" in answer
    assert sources, "应返回引用来源"
    # 来源按检索相关度排序
    assert sources[0].item.kind == "thought"



def test_ask_memory_no_history(db_session, monkeypatch):
    from app.services import memory_service as ms

    called = []

    def fake_chat_json(system, user, **kw):
        called.append(1)
        return {"answer": "x"}

    monkeypatch.setattr(ms, "chat_json", fake_chat_json)
    answer, sources = ms.ask_memory(db_session, "随便问", top_k=3, user_id=1)
    assert "没有找到" in answer
    assert sources == []
    assert not called  # 无历史不调 LLM


def test_ask_memory_llm_failure_graceful(db_session, monkeypatch):
    _seed(db_session)
    from app.llm import LLMError
    from app.services import memory_service as ms

    def boom(system, user, **kw):
        raise LLMError("network down")

    monkeypatch.setattr(ms, "chat_json", boom)
    answer, sources = ms.ask_memory(db_session, "害怕", top_k=3, user_id=1)
    assert "归纳失败" in answer
    assert sources  # 来源仍在（可追溯）

# ── 自动同步（sync_from_sources，边界一） ───────────────────

from datetime import datetime, timezone

from app.models.event import Event, EventType
from app.models.finding import Finding
from app.models.review import DailyReview
from app.models.thought import Thought


def test_sync_only_indexes_refined_sources(db_session):
    """边界一：sync 只同步 review/finding；thought/event 等原始记录永不入向量库。"""
    ts = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)
    db_session.add(Thought(user_id=1, content="今天试了番茄钟很有效", timestamp=ts))
    db_session.add(
        Event(
            user_id=1,
            timestamp=ts,
            type=EventType.LEARNING_START.value,
            note="学了三小时",
        )
    )
    db_session.add(
        DailyReview(
            user_id=1, date="2026-09-08", review_text="## 复盘\n\n今天效率不错",
            structured=None, source_data=None, model="t", status="ok",
        )
    )
    db_session.add(
        Finding(
            user_id=1, kind="OBSERVATION", title="睡眠与效率",
            observation="睡满 7 小时后上午效率更高",
            interpretation="睡眠不足是低效主因", confidence=0.7,
        )
    )
    db_session.commit()

    added = memory_service.sync_from_sources(db_session, user_id=1)
    assert added == 2
    assert memory_service.sync_from_sources(db_session, user_id=1) == 0  # 幂等

    items = memory_service.list_memories(db_session, user_id=1)
    refs = {i.source_ref for i in items}
    assert refs == {"review:1", "finding:1"}
    assert "thought:1" not in refs and "event:1" not in refs

    finding_item = next(i for i in items if i.kind == "finding")
    assert "睡眠与效率" in finding_item.content
    assert "睡满 7 小时后上午效率更高" in finding_item.content
    assert "推测：睡眠不足是低效主因" in finding_item.content


def test_sync_skips_blank_and_raw_only(db_session):
    db_session.add(
        Thought(
            user_id=1, content="只有想法，没有复盘和发现",
            timestamp=datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc),
        )
    )
    db_session.add(
        DailyReview(
            user_id=1, date="2026-09-08", review_text="   ",
            structured=None, source_data=None, model="t", status="ok",
        )
    )
    db_session.commit()
    assert memory_service.sync_from_sources(db_session, user_id=1) == 0


def test_delete_source_cascades_memory(db_session):
    f = Finding(
        user_id=1, kind="OBSERVATION", title="待删发现",
        observation="要删掉的测试发现", confidence=0.5,
    )
    db_session.add(f)
    db_session.commit()
    memory_service.sync_from_sources(db_session, user_id=1)
    items = memory_service.list_memories(db_session, user_id=1)
    assert any(i.source_ref == "finding:1" for i in items)

    removed = memory_service.remove_by_source(db_session, "finding", 1, user_id=1)
    assert removed == 1
    items = memory_service.list_memories(db_session, user_id=1)
    assert not any(i.source_ref == "finding:1" for i in items)


def test_manual_memory_api_rejects_raw_kinds(client):
    """边界一：手动写入接口只收提炼类 kind。"""
    raw = client.post(
        "/memory/items", json={"kind": "event", "content": "原始事件不该进向量库"}
    )
    assert raw.status_code == 422

    ok = client.post(
        "/memory/items", json={"kind": "insight", "content": "提炼后的洞察可以写入"}
    )
    assert ok.status_code == 201
    assert ok.json()["kind"] == "insight"


def test_remove_by_source_unknown(db_session):
    assert memory_service.remove_by_source(db_session, "thought", 999, user_id=1) == 0



def test_extract_insights_writes_memory(db_session, monkeypatch):
    from datetime import datetime, timezone

    from app.models.thought import Thought
    from app.services import memory_service as ms

    ts = datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)
    db_session.add(Thought(user_id=1, content="番茄钟对专注很有效", timestamp=ts))
    db_session.commit()

    def fake_chat_json(system, user, **kw):
        return {"insights": ["番茄钟能提升专注(推测)", "短休息比硬撑更有效率"]}

    monkeypatch.setattr(ms, "chat_json", fake_chat_json)
    created = ms.extract_insights(db_session, "2026-09-09", user_id=1)
    assert len(created) == 2
    assert all(i.kind == "insight" for i in created)
    refs = {i.source_ref for i in created}
    assert refs == {"insight:2026-09-09"}
    again = ms.extract_insights(db_session, "2026-09-09", user_id=1)
    assert len(again) <= 1


def test_extract_insights_no_data(db_session, monkeypatch):
    from app.services import memory_service as ms

    called = []

    def fake_chat_json(system, user, **kw):
        called.append(1)
        return {"insights": ["x"]}

    monkeypatch.setattr(ms, "chat_json", fake_chat_json)
    created = ms.extract_insights(db_session, "2026-01-01", user_id=1)
    assert created == []
    assert not called  # 无记录不调 LLM


# ── L2 周模式提炼（extract_patterns） ──────────────────────


def test_iso_week_bounds():
    from app.services import memory_service as ms

    # 2026-09-09 是周三 → 周一 09-07，周日 09-13，ISO 周 2026-W37
    monday, sunday, week = ms.iso_week_bounds("2026-09-09")
    assert (monday, sunday, week) == ("2026-09-07", "2026-09-13", "2026-W37")
    # 周一与周日同为该周
    assert ms.iso_week_bounds("2026-09-07")[2] == "2026-W37"
    assert ms.iso_week_bounds("2026-09-13")[2] == "2026-W37"
    # 跨周
    assert ms.iso_week_bounds("2026-09-14")[2] == "2026-W38"


def test_extract_patterns_from_insights(db_session, monkeypatch):
    from app.services import memory_service as ms

    for d in ("2026-09-07", "2026-09-09"):
        ms.store_memory(
            db_session, kind="insight", content=f"{d} 熬夜后效率下降",
            source_ref=f"insight:{d}", embed_vectors=False,
            user_id=1,
        )

    def fake_chat_json(system, user, **kw):
        assert "2026-09-07" in user
        return {"patterns": ["压力大时倾向熬夜(推测)", "熬夜导致次日效率下降"]}

    monkeypatch.setattr(ms, "chat_json", fake_chat_json)
    week, monday, sunday, created = ms.extract_patterns(db_session, "2026-09-09", user_id=1)
    assert week == "2026-W37"
    assert (monday, sunday) == ("2026-09-07", "2026-09-13")
    assert len(created) == 2
    assert all(i.kind == "pattern" for i in created)
    assert {i.source_ref for i in created} == {"pattern:2026-W37"}

    # 幂等：二次调用不重复写
    again = ms.extract_patterns(db_session, "2026-09-09", user_id=1)
    assert len(again[3]) == 2
    assert len(ms.get_patterns(db_session, "2026-W37", user_id=1)) == 2


def test_extract_patterns_force_overwrites(db_session, monkeypatch):
    from app.services import memory_service as ms

    ms.store_memory(
        db_session, kind="insight", content="洞察", source_ref="insight:2026-09-09",
        embed_vectors=False,
        user_id=1,
    )
    responses = iter(
        [{"patterns": ["旧模式"]}, {"patterns": ["新模式A", "新模式B"]}]
    )
    monkeypatch.setattr(ms, "chat_json", lambda *a, **k: next(responses))
    ms.extract_patterns(db_session, "2026-09-09", user_id=1)
    assert [p.content for p in ms.get_patterns(db_session, "2026-W37", user_id=1)] == ["旧模式"]

    _, _, _, created = ms.extract_patterns(db_session, "2026-09-09", force=True, user_id=1)
    assert [p.content for p in created] == ["新模式A", "新模式B"]
    old = ms.get_patterns(db_session, "2026-W37", user_id=1)
    assert [p.content for p in old] == ["新模式A", "新模式B"]


def test_extract_patterns_no_material(db_session, monkeypatch):
    from app.services import memory_service as ms

    called = []
    monkeypatch.setattr(ms, "chat_json", lambda *a, **k: called.append(1) or {"patterns": []})
    week, _, _, created = ms.extract_patterns(db_session, "2026-01-01", user_id=1)
    assert week == "2026-W01"
    assert created == []
    assert not called  # 本周无素材不调 LLM


def test_extract_patterns_api(db_session, client, monkeypatch):
    from app.services import memory_service as ms

    ms.store_memory(
        db_session, kind="insight", content="番茄钟有效", source_ref="insight:2026-09-09",
        embed_vectors=False,
        user_id=1,
    )
    monkeypatch.setattr(ms, "chat_json", lambda *a, **k: {"patterns": ["上午更容易专注(推测)"]})

    r = client.post("/memory/extract-week", json={"date": "2026-09-09", "tz_offset": 480})
    assert r.status_code == 200
    body = r.json()
    assert body["week"] == "2026-W37"
    assert len(body["patterns"]) == 1
    assert body["patterns"][0]["has_embedding"] is False
    assert "embedding" not in body["patterns"][0]  # 不返回原始向量

    r2 = client.get("/memory/extract-week/2026-09-09")
    assert r2.status_code == 200
    assert r2.json()["patterns"][0]["content"] == "上午更容易专注(推测)"

    r3 = client.get("/memory/extract-week/bad-date")
    assert r3.status_code == 422


def test_memory_read_excludes_embedding_and_flags_vector(db_session, client):
    item = memory_service.store_memory(
        db_session, kind="event", content="学 FastAPI", source_ref="event:99",
        embed_vectors=False,
        user_id=1,
    )
    r = client.get("/memory/items")
    assert r.status_code == 200
    row = next(x for x in r.json() if x["id"] == item.id)
    assert row["has_embedding"] is False
    assert "embedding" not in row


# ── 去重（文本规范化 + 向量阈值） ──────────────────────────


def test_dedup_exact_text_same_kind(db_session):
    a = memory_service.store_memory(
        db_session, kind="insight", content="番茄钟能提升专注",
        source_ref="insight:2026-09-01", embed_vectors=False,
        user_id=1,
    )
    b = memory_service.store_memory(
        db_session, kind="insight", content="番茄钟能提升专 注",  # 空白差异
        source_ref="insight:2026-09-02", embed_vectors=False,
        user_id=1,
    )
    assert b is not None and b.id == a.id
    assert len(memory_service.list_memories(db_session, kind="insight", user_id=1)) == 1


def test_dedup_does_not_merge_different_kinds(db_session):
    a = memory_service.store_memory(
        db_session, kind="insight", content="睡前手机影响睡眠",
        source_ref="insight:2026-09-01", embed_vectors=False,
        user_id=1,
    )
    b = memory_service.store_memory(
        db_session, kind="pattern", content="睡前手机影响睡眠",
        source_ref="pattern:2026-W36", embed_vectors=False,
        user_id=1,
    )
    assert a.id != b.id
    assert len(memory_service.list_memories(db_session, user_id=1)) == 2


def test_dedup_disabled_allows_duplicates(db_session):
    a = memory_service.store_memory(
        db_session, kind="insight", content="重复内容测试",
        source_ref="insight:2026-09-01", embed_vectors=False,
        user_id=1,
    )
    b = memory_service.store_memory(
        db_session, kind="insight", content="重复内容测试",
        source_ref="insight:2026-09-02", embed_vectors=False, dedup=False,
        user_id=1,
    )
    assert a.id != b.id
    assert len(memory_service.list_memories(db_session, kind="insight", user_id=1)) == 2


def test_replace_by_ref_still_replaces_when_identical(db_session):
    """重新提炼出相同内容时，旧条目先删后写不误判为重复。"""
    first = memory_service.replace_by_ref(
        db_session, kind="insight", source_ref="insight:2026-09-01",
        contents=["一模一样的洞察"],
        user_id=1,
    )
    again = memory_service.replace_by_ref(
        db_session, kind="insight", source_ref="insight:2026-09-01",
        contents=["一模一样的洞察"],
        user_id=1,
    )
    assert len(first) == 1 and len(again) == 1
    assert again[0].content == "一模一样的洞察"
    assert len(memory_service.list_memories(db_session, kind="insight", user_id=1)) == 1
    # 写入的是新行（旧行已删），而非去重命中旧行
    assert again[0].id is not None


# ── 检索诊断与统计（可视化） ────────────────────────────────


def test_memory_stats_endpoint(client, db_session):
    memory_service.store_memory(
        db_session, kind="insight", content="洞察一", source_ref="insight:2026-09-09",
        embed_vectors=False,
        user_id=1,
    )
    memory_service.store_memory(
        db_session, kind="pattern", content="模式一", source_ref="pattern:2026-W37",
        embed_vectors=False,
        user_id=1,
    )
    db_session.add(Event(user_id=1, timestamp=datetime(2026, 9, 9, tzinfo=timezone.utc),
                         type=EventType.LEARNING_START.value))
    db_session.commit()

    r = client.get("/memory/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert data["embedded"] == 0  # SQLite 无向量
    kinds = {k["kind"]: k["count"] for k in data["by_kind"]}
    assert kinds == {"insight": 1, "pattern": 1}
    assert data["source_counts"]["events"] == 1


def test_search_debug_lists_all_candidates_with_scores(client, db_session):
    _seed(db_session)
    r = client.post("/memory/search/debug", json={"query": "害怕", "limit": 50})
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "keyword"  # SQLite 走兜底路径
    assert data["threshold"] > 0
    assert 0 <= data["time_weight"] <= 1
    assert len(data["hits"]) >= 1
    top = data["hits"][0]
    assert top["item"]["kind"] == "thought"
    for key in ("similarity", "final_score", "recency", "half_life_days", "passed_threshold"):
        assert key in top
    # 全部候选按 final_score 单调不增
    scores = [h["final_score"] for h in data["hits"]]
    assert scores == sorted(scores, reverse=True)


def test_search_debug_negative_query_returns_nothing(client, db_session):
    _seed(db_session)
    r = client.post("/memory/search/debug", json={"query": "zzz不存在", "limit": 50})
    assert r.json()["hits"] == []
