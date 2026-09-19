from datetime import date, datetime

from app.models.memory import MemoryItem
from app.services import memory_service as ms


def _item(kind: str, content: str, ref: str | None, created: str = "2026-09-01") -> MemoryItem:
    return MemoryItem(
        user_id=1,
        kind=kind,
        content=content,
        source_ref=ref,
        created_at=datetime.fromisoformat(created + "T00:00:00+00:00"),
    )


def test_anchor_date_from_source_ref():
    today = date(2026, 9, 10)
    assert ms._anchor_date(_item("insight", "x", "insight:2026-09-03")) == date(2026, 9, 3)
    # 周引用 → 该 ISO 周周一
    assert ms._anchor_date(_item("pattern", "x", "pattern:2026-W36")) == date(2026, 8, 31)
    assert ms._anchor_date(_item("insight", "x", "insight:2026-W36")) == date(2026, 8, 31)
    # 月引用 → 当月 1 号
    assert ms._anchor_date(_item("pattern", "x", "pattern:2026-09")) == date(2026, 9, 1)
    # 无 ref → 行创建日期
    assert ms._anchor_date(_item("finding", "x", "finding:7", "2026-08-20")) == date(2026, 8, 20)
    assert today is not None


def test_half_life_by_kind_and_granularity():
    day = _item("insight", "x", "insight:2026-09-03")
    week = _item("insight", "x", "insight:2026-W36")
    pattern = _item("pattern", "x", "pattern:2026-W36")
    finding = _item("finding", "x", "finding:1")
    assert ms._half_life_days(day) == 90
    assert ms._half_life_days(week) == 150
    assert ms._half_life_days(pattern) == 180
    assert ms._half_life_days(finding) == 365


def test_recency_decays_by_half_life():
    today = date(2026, 9, 10)
    assert ms._recency(today, today, 90) == 1.0
    assert abs(ms._recency(date(2026, 6, 12), today, 90) - 0.5) < 0.01  # 90 天半衰
    assert ms._recency(date(2026, 6, 12), today, 180) > 0.6


def test_rank_hits_applies_threshold():
    old = _item("insight", "低相关", "insight:2026-09-01")
    good = _item("insight", "高相关", "insight:2026-09-09")
    raw = [(old, 0.40), (good, 0.80)]
    hits = ms.rank_hits(raw, top_k=5, time_weight=0.2, min_similarity=0.56)
    assert [h.item.content for h in hits] == ["高相关"]


def test_rank_hits_time_weight_can_favor_recent():
    """同等相似度时，新内容胜出。"""
    old = _item("insight", "旧", "insight:2026-03-01")
    new = _item("insight", "新", "insight:2026-09-09")
    hits = ms.rank_hits([(old, 0.70), (new, 0.70)], top_k=5, time_weight=0.2)
    assert hits[0].item.content == "新"
    assert hits[0].final_score > hits[1].final_score


def test_rank_hits_relevant_old_can_beat_irrelevant_new():
    """时间权重是加法混合：高相关的旧内容不会被低相关的新内容埋掉。"""
    old_relevant = _item("pattern", "半年前验证过的高相关结论", "pattern:2026-W10")
    new_weak = _item("insight", "昨天但不太相关", "insight:2026-09-09")
    hits = ms.rank_hits(
        [(new_weak, 0.60), (old_relevant, 0.78)], top_k=5, time_weight=0.2
    )
    assert hits[0].item.content.startswith("半年前")


def test_rank_hits_top_k_and_fallback_without_threshold():
    items = [(_item("insight", f"i{i}", f"insight:2026-09-0{i}"), 0.5) for i in range(1, 6)]
    hits = ms.rank_hits(items, top_k=2, time_weight=0.2, min_similarity=None)
    assert len(hits) == 2
    # 相似度相同 → 新日期在前
    assert hits[0].anchor_date == date(2026, 9, 5)


def test_final_score_formula():
    item = _item("insight", "x", "insight:2026-09-10")
    hits = ms.rank_hits([(item, 0.80)], top_k=1, time_weight=0.25, today=date(2026, 9, 10))
    # recency=1.0 → 0.75*0.8 + 0.25*1.0 = 0.85
    assert abs(hits[0].final_score - 0.85) < 1e-9
