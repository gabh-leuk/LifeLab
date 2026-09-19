from datetime import datetime, timezone

from app.llm import LLMError
from app.models.event import Event, EventType
from app.models.period_review import PeriodReview
from app.models.thought import Thought
from app.services import memory_service, profile_service

GOOD_JSON = {
    "day_summary": "写了不少代码。",
    "highlights": ["完成同步"],
    "concerns": [],
    "patterns": [],
    "suggestions": [],
    "insights": [],
}

WEEK_JSON = {
    "period_summary": "本周稳定。",
    "patterns": [],
    "missed_insights": [],
    "suggestions": [],
}

MONTH_JSON = {
    "period_summary": "本月稳定。",
    "patterns": [],
    "missed_insights": [],
    "suggestions": [],
}


def _seed_day(db_session, date_str: str = "2026-09-02") -> None:
    base = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            Event(
                user_id=1,
                timestamp=base,
                type=EventType.LEARNING_START.value,
                note="写代码",
            ),
            Thought(user_id=1, timestamp=base, content="睡前刷手机还是停不下来"),
        ]
    )
    db_session.commit()


def test_daily_review_injects_history_context(client, db_session, monkeypatch):
    _seed_day(db_session)
    memory_service.store_memory(
        db_session,
        kind="insight",
        content="睡前手机使用影响睡眠质量",
        source_ref="insight:2026-09-01",
        embed_vectors=False,
        user_id=1,
    )
    profile_service.create_fact(db_session, category="habit", content="习惯晚睡", user_id=1)
    client.post("/problems", json={"title": "为什么睡前难以放下手机？"})

    monkeypatch.setattr(
        "app.services.context_service.chat_json",
        lambda system, user, **kw: {"query": "睡前手机与睡眠"},
    )
    captured = {}

    def fake_review(system, user, **kw):
        captured["user"] = user
        return GOOD_JSON

    monkeypatch.setattr("app.services.review_service.chat_json", fake_review)

    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"

    history = data["source_data"]["history"]
    assert history["query"] == "睡前手机与睡眠"
    assert history["problems"] >= 1
    assert any(h["ref"] == "insight:2026-09-01" for h in history["hits"])

    prompt = captured["user"]
    assert "【已有上下文】" in prompt
    assert "历史相关记忆" in prompt
    assert "睡前手机使用影响睡眠质量" in prompt
    assert "习惯晚睡" in prompt
    assert "为什么睡前难以放下手机？" in prompt


def test_daily_review_history_excludes_today_insight(client, db_session, monkeypatch):
    """当天洞察（重新生成时已存在）不得进入自己的历史上下文。"""
    _seed_day(db_session)
    memory_service.store_memory(
        db_session,
        kind="insight",
        content="今天生成的洞察不应出现",
        source_ref="insight:2026-09-02",
        embed_vectors=False,
        user_id=1,
    )
    memory_service.store_memory(
        db_session,
        kind="insight",
        content="昨天的洞察手机相关应该出现",
        source_ref="insight:2026-09-01",
        embed_vectors=False,
        user_id=1,
    )
    monkeypatch.setattr(
        "app.services.context_service.chat_json",
        lambda system, user, **kw: {"query": "手机 洞察"},
    )
    captured = {}

    def fake_review(system, user, **kw):
        captured["user"] = user
        return GOOD_JSON

    monkeypatch.setattr("app.services.review_service.chat_json", fake_review)

    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    refs = {h["ref"] for h in r.json()["source_data"]["history"]["hits"]}
    assert "insight:2026-09-01" in refs
    assert "insight:2026-09-02" not in refs
    assert "今天生成的洞察不应出现" not in captured["user"]
    assert "昨天的洞察手机相关应该出现" in captured["user"]


def test_query_extraction_failure_falls_back(client, db_session, monkeypatch):
    _seed_day(db_session)

    def boom(*a, **k):
        raise LLMError("query llm down")

    monkeypatch.setattr("app.services.context_service.chat_json", boom)
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON
    )

    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    # 回退查询来自当天文本（想法/事件类型）
    query = data["source_data"]["history"]["query"]
    assert query and query != "query llm down"


def test_history_retrieval_failure_does_not_break_review(
    client, db_session, monkeypatch
):
    _seed_day(db_session)

    def boom(*a, **k):
        raise RuntimeError("retrieval down")

    monkeypatch.setattr("app.services.memory_service.search_memory", boom)
    monkeypatch.setattr(
        "app.services.review_service.chat_json", lambda *a, **k: GOOD_JSON
    )

    r = client.post("/reviews", json={"date": "2026-09-02"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["source_data"]["history"]["hits"] == []


def test_week_review_injects_history(client, db_session, monkeypatch):
    db_session.add(
        PeriodReview(
            user_id=1,
            period_type="week",
            period_key="2026-W36",
            period_start="2026-08-31",
            period_end="2026-09-06",
            review_text="## 2026-W36",
            structured={"period_summary": "上周整体不错"},
            status="ok",
        )
    )
    db_session.commit()
    memory_service.store_memory(
        db_session,
        kind="pattern",
        content="睡前手机隔离能有效降低使用冲动",
        source_ref="pattern:2026-W36",
        embed_vectors=False,
        user_id=1,
    )
    memory_service.store_memory(
        db_session,
        kind="insight",
        content="本周洞察：手机隔离有反馈",
        source_ref="insight:2026-09-07",
        embed_vectors=False,
        user_id=1,
    )
    captured = {}

    def fake(system, user, **kw):
        captured["user"] = user
        return WEEK_JSON

    monkeypatch.setattr("app.services.period_review_service.chat_json", fake)

    r = client.post(
        "/reviews/period",
        json={"period_type": "week", "date": "2026-09-09", "tz_offset": 480},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"

    prompt = captured["user"]
    assert "【已有上下文】" in prompt
    assert "历史相关记忆" in prompt
    assert "睡前手机隔离能有效降低使用冲动" in prompt

    refs = {h["ref"] for h in data["source_data"]["history"]["hits"]}
    assert "pattern:2026-W36" in refs
    assert "insight:2026-09-07" not in refs  # 本周素材不进自己的历史段


def test_month_review_history_only_recalls_earlier_periods(
    client, db_session, monkeypatch
):
    """跨期召回：本期素材一条都不进历史段（含旧实现在 ref 黑名单里漏掉的月内日洞察），
    更早期间按「每期最多 2 条」给名额，输出按时间正序。"""
    for key, start, end in [
        ("2026-W36", "2026-08-31", "2026-09-06"),
        ("2026-W37", "2026-09-07", "2026-09-13"),
    ]:
        db_session.add(
            PeriodReview(
                user_id=1,
                period_type="week",
                period_key=key,
                period_start=start,
                period_end=end,
                review_text=f"## {key}",
                structured={"period_summary": "云栖山旅游那周，作息被打乱"},
                status="ok",
            )
        )
    db_session.commit()

    this_period = [
        ("insight", "insight:2026-09-03", "本月云栖山旅游第三天，回来下午补觉"),
        ("pattern", "pattern:2026-W37", "云栖山旅游期间作息被打乱(推测)"),
    ]
    earlier = [
        ("insight", "insight:2026-08-14", "上月云栖山旅游第一天，徒步爬山"),
        ("insight", "insight:2026-08-15", "上月云栖山旅游第二天，很晚才睡"),
        ("insight", "insight:2026-08-16", "上月云栖山旅游第三天，回来补觉"),
        ("pattern", "pattern:2026-08", "云栖山旅游期间作息被打乱(推测)"),
        ("insight", "insight:2026-07-20", "七月也去云栖山旅游，城市里逛"),
        ("insight", "insight:2026-07-21", "七月云栖山旅游第二天，拍照为主"),
        ("insight", "insight:2026-07-22", "七月云栖山旅游第三天，回来很累"),
    ]
    for kind, ref, content in this_period + earlier:
        memory_service.store_memory(
            db_session,
            kind=kind,
            content=content,
            source_ref=ref,
            embed_vectors=False,
            user_id=1,
        )

    monkeypatch.setattr(
        "app.services.period_review_service.chat_json",
        lambda system, user, **kw: MONTH_JSON,
    )

    r = client.post(
        "/reviews/period",
        json={"period_type": "month", "date": "2026-09-20", "tz_offset": 480},
    )
    assert r.status_code == 200
    hits = r.json()["source_data"]["history"]["hits"]
    refs = [h["ref"] for h in hits]

    # 本期一条都不进历史段
    assert not set(refs) & {ref for _, ref, _ in this_period}
    # 名额不给最近的那一期独吞：每个更早期间先各留 2 个
    august = [h for h in hits if h["date"].startswith("2026-08")]
    july = [h for h in hits if h["date"].startswith("2026-07")]
    assert len(august) >= 2
    assert len(july) >= 2
    # 预留之后还有额度就补满，不浪费（top_k = 5，候选足够）
    assert len(hits) == 5
    # 按时间正序输出（模型读到的应是先后序列）
    dates = [h["date"] for h in hits]
    assert dates == sorted(dates)


def test_month_review_injects_history(client, db_session, monkeypatch):
    for key, start, end, summary in [
        ("2026-W36", "2026-08-31", "2026-09-06", "第一周手机使用改善"),
        ("2026-W37", "2026-09-07", "2026-09-13", "第二周保持手机隔离"),
    ]:
        db_session.add(
            PeriodReview(
                user_id=1,
                period_type="week",
                period_key=key,
                period_start=start,
                period_end=end,
                review_text=f"## {key}",
                structured={"period_summary": summary},
                status="ok",
            )
        )
    db_session.commit()
    memory_service.store_memory(
        db_session,
        kind="pattern",
        content="手机使用与烦躁同增（上月模式）",
        source_ref="pattern:2026-08",
        embed_vectors=False,
        user_id=1,
    )
    captured = {}

    def fake(system, user, **kw):
        captured["user"] = user
        return MONTH_JSON

    monkeypatch.setattr("app.services.period_review_service.chat_json", fake)

    r = client.post(
        "/reviews/period",
        json={"period_type": "month", "date": "2026-09-20", "tz_offset": 480},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"

    prompt = captured["user"]
    assert "【已有上下文】" in prompt
    assert "手机使用与烦躁同增（上月模式）" in prompt

    refs = {h["ref"] for h in data["source_data"]["history"]["hits"]}
    assert "pattern:2026-08" in refs
    assert "pattern:2026-W36" not in refs  # 本月素材不进自己的历史段
    assert "pattern:2026-W37" not in refs
