from datetime import datetime, timedelta, timezone

from app.models.event import Event, EventSource
from app.models.review import DailyReview
from app.services import memory_service, period_review_service


def _seed_daily_review(db_session, date: str, summary: str = "今天还行") -> DailyReview:
    row = DailyReview(
        user_id=1,
        date=date,
        review_text=f"## {date}\n\n**总结**：{summary}",
        structured={"day_summary": summary, "patterns": ["晚睡后效率低(推测)"]},
        source_data={},
        model="test",
        status="ok",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _seed_insight(db_session, date: str, content: str = "番茄钟有效") -> None:
    memory_service.store_memory(
        db_session,
        kind="insight",
        content=content,
        source_ref=f"insight:{date}",
        embed_vectors=False,
        user_id=1,
    )


WEEK_JSON = {
    "period_summary": "本周整体节奏前紧后松。",
    "patterns": ["压力大时倾向熬夜(推测)", "运动后第二天专注更高"],
    "missed_insights": ["连续记录本身就能减少逃避"],
    "suggestions": ["下周把运动排在上午"],
}


def test_month_bounds():
    assert period_review_service.month_bounds("2026-09-10") == ("2026-09-01", "2026-09-30")
    assert period_review_service.month_bounds("2026-02-15") == ("2026-02-01", "2026-02-28")
    assert period_review_service.month_bounds("2026-12-31") == ("2026-12-01", "2026-12-31")

    assert period_review_service.period_bounds("week", "2026-09-09") == (
        "2026-W37",
        "2026-09-07",
        "2026-09-13",
    )
    assert period_review_service.period_bounds("month", "2026-09-09") == (
        "2026-09",
        "2026-09-01",
        "2026-09-30",
    )


def test_week_review_generates_and_stores_memory(client, db_session, monkeypatch):
    _seed_daily_review(db_session, "2026-09-07", "周一学习了三小时")
    _seed_insight(db_session, "2026-09-07", "上午更容易进入状态")
    captured = {}

    def fake_chat_json(system, user, **kw):
        captured["user"] = user
        return WEEK_JSON

    monkeypatch.setattr(
        "app.services.period_review_service.chat_json", fake_chat_json
    )
    r = client.post(
        "/reviews/period",
        json={"period_type": "week", "date": "2026-09-09", "tz_offset": 480},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["period_key"] == "2026-W37"
    assert data["status"] == "ok"
    assert "本周整体节奏" in data["review_text"]
    assert data["material_count"] == 2  # 1 篇日复盘 + 1 条洞察
    assert data["stale"] is False
    # 输入只读浓缩产物，不含原始流水
    assert "上午更容易进入状态" in captured["user"]
    assert "完整事件序列" not in captured["user"]

    patterns = client.get(
        "/memory/items?kind=pattern&source_ref=pattern:2026-W37"
    ).json()
    assert {p["content"] for p in patterns} == {
        "压力大时倾向熬夜(推测)",
        "运动后第二天专注更高",
    }
    missed = client.get(
        "/memory/items?kind=insight&source_ref=insight:2026-W37"
    ).json()
    assert [m["content"] for m in missed] == ["连续记录本身就能减少逃避"]


def test_week_review_idempotent_without_force(client, db_session, monkeypatch):
    _seed_daily_review(db_session, "2026-09-07")
    calls = []
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json",
        lambda *a, **k: calls.append(1) or WEEK_JSON,
    )
    r1 = client.post(
        "/reviews/period", json={"period_type": "week", "date": "2026-09-09"}
    )
    r2 = client.post(
        "/reviews/period", json={"period_type": "week", "date": "2026-09-09"}
    )
    assert r1.json()["id"] == r2.json()["id"]
    assert len(calls) == 1


def test_week_review_force_replaces_patterns(client, db_session, monkeypatch):
    _seed_daily_review(db_session, "2026-09-07")
    payloads = iter(
        [
            {**WEEK_JSON, "patterns": ["旧模式"], "missed_insights": []},
            {**WEEK_JSON, "patterns": ["新模式A", "新模式B"], "missed_insights": ["新遗漏"]},
        ]
    )
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json", lambda *a, **k: next(payloads)
    )
    client.post("/reviews/period", json={"period_type": "week", "date": "2026-09-09"})
    client.post(
        "/reviews/period",
        json={"period_type": "week", "date": "2026-09-09", "force": True},
    )
    patterns = client.get(
        "/memory/items?kind=pattern&source_ref=pattern:2026-W37"
    ).json()
    assert {p["content"] for p in patterns} == {"新模式A", "新模式B"}


def test_week_review_no_material_skips_llm(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json",
        lambda *a, **k: calls.append(1) or WEEK_JSON,
    )
    r = client.post(
        "/reviews/period", json={"period_type": "week", "date": "2026-09-09"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "还没有可分析的素材" in r.json()["review_text"]
    assert r.json()["material_count"] == 0
    assert not calls


def test_month_review_needs_two_weeks(client, db_session, monkeypatch):
    _seed_daily_review(db_session, "2026-09-07")
    calls = []
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json",
        lambda *a, **k: calls.append(1) or WEEK_JSON,
    )
    # 只有第 1 周 → 不调 LLM
    r1 = client.post(
        "/reviews/period", json={"period_type": "month", "date": "2026-09-10"}
    )
    assert "还没有可分析的素材" in r1.json()["review_text"]
    assert not calls

    # 第 2 周也有日复盘 → 生成月复盘
    _seed_daily_review(db_session, "2026-09-14")
    client.post("/reviews/period", json={"period_type": "week", "date": "2026-09-09"})
    client.post("/reviews/period", json={"period_type": "week", "date": "2026-09-16"})
    r2 = client.post(
        "/reviews/period", json={"period_type": "month", "date": "2026-09-20"}
    )
    data = r2.json()
    assert data["period_key"] == "2026-09"
    assert data["material_count"] == 2
    assert len(calls) == 3  # 两周各一次 + 月一次
    assert "2026-W37" in data["source_data"]["weeks"]


def test_stale_flag_after_material_update(client, db_session, monkeypatch):
    review = _seed_daily_review(db_session, "2026-09-07")
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json", lambda *a, **k: WEEK_JSON
    )
    client.post("/reviews/period", json={"period_type": "week", "date": "2026-09-09"})

    got = client.get("/reviews/period/week/2026-09-09")
    assert got.status_code == 200
    assert got.json()["stale"] is False

    # 模拟"素材在周期复盘生成之后被更新"：用相对 now 的未来时间，避免硬编码日期随时间失效
    review.updated_at = datetime.now(timezone.utc) + timedelta(days=1)
    db_session.commit()

    got2 = client.get("/reviews/period/week/2026-09-09")
    assert got2.json()["stale"] is True


# ── 设备层：周折进日摘要行，月读周合计 ───────────────────────


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


def test_week_review_folds_device_into_day_line(client, db_session, monkeypatch):
    """设备合计与当天记录同行出现；只有设备、没有日复盘的日子单独成行。"""
    _seed_daily_review(db_session, "2026-09-07", "今天还行")
    _add_device_event(
        db_session,
        datetime(2026, 9, 7, 9, tzinfo=timezone.utc),
        datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        "study_work",
    )
    # 09-08 有设备活动但从没做过日复盘 —— 不单独出行就在周复盘里彻底看不见
    _add_device_event(
        db_session,
        datetime(2026, 9, 8, 15, tzinfo=timezone.utc),
        datetime(2026, 9, 8, 15, 40, tzinfo=timezone.utc),
        "video",
    )
    captured = {}
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json",
        lambda system, user, **kw: captured.update(user=user) or WEEK_JSON,
    )

    r = client.post(
        "/reviews/period",
        json={"period_type": "week", "date": "2026-09-09", "tz_offset": 480},
    )
    assert r.status_code == 200
    prompt = captured["user"]
    assert "｜设备：学习工作 1h" in prompt
    assert "- 2026-09-08 设备：视频 40分" in prompt
    assert r.json()["source_data"]["device_summary"] == {
        "study_work": 3600,
        "video": 2400,
    }


def test_week_review_device_summary_none_without_device_data(client, db_session, monkeypatch):
    _seed_daily_review(db_session, "2026-09-07")
    captured = {}
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json",
        lambda system, user, **kw: captured.update(user=user) or WEEK_JSON,
    )
    r = client.post(
        "/reviews/period", json={"period_type": "week", "date": "2026-09-09"}
    )
    assert r.json()["source_data"]["device_summary"] is None
    assert "设备：" not in captured["user"]


def _seed_week_review(db_session, key, start, end, device_summary):
    period_review_service._upsert(
        db_session,
        period_type="week",
        period_key=key,
        period_start=start,
        period_end=end,
        review_text=f"## {key}\n\n**总结**：这一周还行",
        structured={"period_summary": f"{key} 还行"},
        source_data={"device_summary": device_summary},
        status="ok",
        user_id=1,
    )


def test_month_review_reads_week_device_summary(client, db_session, monkeypatch):
    """月不重算 31 天设备数据，直接读周复盘 source_data 里的合计。"""
    _seed_week_review(
        db_session, "2026-W37", "2026-09-07", "2026-09-13",
        {"study_work": 43200, "video": 14400},
    )
    _seed_week_review(db_session, "2026-W38", "2026-09-14", "2026-09-20", None)
    captured = {}
    monkeypatch.setattr(
        "app.services.period_review_service.chat_json",
        lambda system, user, **kw: captured.update(user=user) or WEEK_JSON,
    )

    r = client.post(
        "/reviews/period", json={"period_type": "month", "date": "2026-09-20"}
    )
    assert r.status_code == 200
    assert r.json()["period_key"] == "2026-09"
    assert "周设备合计：学习工作 12h 视频 4h" in captured["user"]
    # 没有设备的周不渲染这一行，也不应报错
    assert captured["user"].count("周设备合计") == 1


def test_period_review_get_404(client):
    assert client.get("/reviews/period/week/2026-09-09").status_code == 404
    assert client.get("/reviews/period/month/2026-09-09").status_code == 404
    assert client.get("/reviews/period/bad/2026-09-09").status_code == 422


def test_period_review_bad_date(client):
    r = client.post(
        "/reviews/period", json={"period_type": "week", "date": "bad-date"}
    )
    assert r.status_code == 422
