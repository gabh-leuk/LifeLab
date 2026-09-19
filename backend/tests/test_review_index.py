"""复盘索引（GET /reviews）—— 助手页「复盘」标签的数据源。

日/周/月统一成一个列表（kind + key + 摘要 + stale），正文仍走各自的详情端点。
"""

from app.models.period_review import PeriodReview
from app.models.review import DailyReview


def _seed_daily(db_session, date: str, summary: str = "今天还行", user_id: int = 1):
    row = DailyReview(
        user_id=user_id,
        date=date,
        review_text=f"## {date}\n\n**总结**：{summary}",
        structured={"day_summary": summary},
        source_data={},
        model="test",
        status="ok",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _seed_week(db_session, user_id: int = 1):
    row = PeriodReview(
        user_id=user_id,
        period_type="week",
        period_key="2026-W37",
        period_start="2026-09-07",
        period_end="2026-09-13",
        review_text="## 2026-W37（09-07 ~ 09-13）\n\n**总结**：本周前紧后松。",
        structured={"period_summary": "本周前紧后松。"},
        source_data={},
        model="test",
        status="ok",
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_review_index_empty(client):
    assert client.get("/reviews").json() == []


def test_review_index_lists_daily_with_summary_and_label(client, db_session):
    _seed_daily(db_session, "2026-09-02", "上午学习，傍晚游戏。")
    items = client.get("/reviews").json()
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "day"
    assert item["key"] == "2026-09-02"
    assert item["label"] == "2026-09-02 周三"
    # 摘要取 structured.day_summary，不是 markdown 标题那行
    assert item["summary"] == "上午学习，傍晚游戏。"
    assert item["status"] == "ok"
    assert item["stale"] is False
    # 日复盘自己就是日期，不需要 period_start
    assert item["period_start"] is None


def test_review_index_lists_period_reviews(client, db_session):
    _seed_week(db_session)
    week = [i for i in client.get("/reviews").json() if i["kind"] == "week"]
    assert len(week) == 1
    assert week[0]["key"] == "2026-W37"
    assert week[0]["label"] == "2026-W37（2026-09-07 ~ 2026-09-13）"
    # 详情端点收日期不收 period_key → 索引必须带一个落在期间内的日期
    assert week[0]["period_start"] == "2026-09-07"
    # 摘要退回到正文第一行正文（跳过 markdown 标题与 ** 粗体标记）
    assert week[0]["summary"] == "总结：本周前紧后松。"


def test_review_index_sorted_by_recency_across_kinds(client, db_session):
    _seed_daily(db_session, "2026-09-02")
    week = _seed_week(db_session)
    kind_by_key = {i["key"]: i["kind"] for i in client.get("/reviews").json()}
    assert kind_by_key == {"2026-09-02": "day", week.period_key: "week"}
    # 后插入的周复盘排在前面
    assert client.get("/reviews").json()[0]["key"] == week.period_key


def test_review_index_is_scoped_to_current_user(client, db_session, login_as):
    _seed_daily(db_session, "2026-09-02")
    _seed_week(db_session)
    assert len(client.get("/reviews").json()) == 2

    login_as(2)
    assert client.get("/reviews").json() == []
