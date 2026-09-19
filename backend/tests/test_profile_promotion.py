from app.llm import LLMError
from app.services import memory_service, profile_service
from app.services import profile_promotion_service as pps


def _pattern(db, week: str, content: str):
    return memory_service.store_memory(
        db,
        kind="pattern",
        content=content,
        source_ref=f"pattern:{week}",
        embed_vectors=False,
        user_id=1,
    )


def _insight(db, day: str, content: str):
    return memory_service.store_memory(
        db,
        kind="insight",
        content=content,
        source_ref=f"insight:{day}",
        embed_vectors=False,
        user_id=1,
    )


# ── 纯函数：粒度解析与聚类 ─────────────────────────────────


def test_granularity_parsing():
    assert pps._granularity("pattern:2026-W37") == ("week", "2026-W37")
    assert pps._granularity("insight:2026-09-07") == ("day", "2026-09-07")
    assert pps._granularity("pattern:2026-09") == ("month", "2026-09")
    assert pps._granularity("finding:3") is None
    assert pps._granularity(None) is None


def test_cluster_transitive_union():
    nodes = [
        pps._Node(id=i, kind="pattern", content=f"c{i}", ref=f"pattern:2026-W3{i}")
        for i in range(1, 5)
    ]
    # 1-2 与 2-3 相连 → {1,2,3}；4 独立
    pairs = {(1, 2): 0.8, (2, 3): 0.85, (1, 4): 0.2}
    clusters = pps._cluster(nodes, {k: v for k, v in pairs.items() if v >= 0.75})
    groups = sorted(sorted(m.id for m in c.members) for c in clusters)
    assert groups == [[1, 2, 3], [4]]


def test_representative_is_most_central():
    nodes = [
        pps._Node(id=1, kind="pattern", content="a", ref="pattern:2026-W36"),
        pps._Node(id=2, kind="pattern", content="b", ref="pattern:2026-W37"),
        pps._Node(id=3, kind="pattern", content="c", ref="pattern:2026-09"),
    ]
    cluster = pps._Cluster(
        members=nodes, span_keys=["2026-W36", "2026-W37"],
        pairs={(1, 2): 0.80, (1, 3): 0.90, (2, 3): 0.78},
    )
    # 1 的平均相似度 (0.80+0.90)/2 最高
    assert pps._representative(cluster).id == 1


# ── 候选构建：跨周/跨天门槛与排除 ──────────────────────────


def _fake_pairs(mapping: dict[tuple[int, int], float]):
    def _inner(db, kind, threshold, user_id=None):
        return {k: v for k, v in mapping.items() if v >= threshold}
    return _inner


def test_pattern_needs_two_distinct_weeks(db_session, monkeypatch):
    a = _pattern(db_session, "2026-W36", "物理隔离能降低手机冲动")
    b = _pattern(db_session, "2026-W37", "手机放客厅后使用冲动降低")
    c = _pattern(db_session, "2026-W36", "同日重复模式甲")
    d = _pattern(db_session, "2026-W36", "同日重复模式乙")
    monkeypatch.setattr(
        "app.services.profile_promotion_service._similar_pairs",
        _fake_pairs({(a.id, b.id): 0.80, (c.id, d.id): 0.90}),
    )
    cands = pps._build_candidates(
        db_session,
        kind="pattern",
        span_granularity="week",
        threshold=0.75,
        min_spans=2,
        promoted_refs=set(),
        fact_texts=set(),
        user_id=1,
    )
    assert len(cands) == 1
    assert sorted(cands[0]["spans"]) == ["2026-W36", "2026-W37"]
    assert {m["ref"] for m in cands[0]["members"]} == {
        "pattern:2026-W36",
        "pattern:2026-W37",
    }


def test_pattern_promoted_ref_excluded(db_session, monkeypatch):
    a = _pattern(db_session, "2026-W36", "模式甲跨周复现")
    b = _pattern(db_session, "2026-W37", "模式甲变体跨周复现")
    monkeypatch.setattr(
        "app.services.profile_promotion_service._similar_pairs",
        _fake_pairs({(a.id, b.id): 0.85}),
    )
    cands = pps._build_candidates(
        db_session,
        kind="pattern",
        span_granularity="week",
        threshold=0.75,
        min_spans=2,
        promoted_refs={"pattern:2026-W36"},  # 簇内成员已晋升
        fact_texts=set(),
        user_id=1,
    )
    assert cands == []


def test_pattern_duplicate_fact_text_excluded(db_session, monkeypatch):
    a = _pattern(db_session, "2026-W36", "完全相同的背景事实")
    b = _pattern(db_session, "2026-W37", "完全相同的背景事实")
    # 代表是距离最近的成员；两者文本相同，无论选谁都会被去重
    monkeypatch.setattr(
        "app.services.profile_promotion_service._similar_pairs",
        _fake_pairs({(a.id, b.id): 0.95}),
    )
    cands = pps._build_candidates(
        db_session,
        kind="pattern",
        span_granularity="week",
        threshold=0.75,
        min_spans=2,
        promoted_refs=set(),
        fact_texts={pps._normalize("完全相同的背景事实")},
        user_id=1,
    )
    assert cands == []


def test_insight_needs_two_distinct_days(db_session, monkeypatch):
    a = _insight(db_session, "2026-09-01", "凌晨学习后白天精力下降")
    b = _insight(db_session, "2026-09-02", "凌晨学习导致白天低效")
    c = _insight(db_session, "2026-09-05", "同一日内的相近洞察甲")
    d = _insight(db_session, "2026-09-05", "同一日内的相近洞察乙")
    monkeypatch.setattr(
        "app.services.profile_promotion_service._similar_pairs",
        _fake_pairs({(a.id, b.id): 0.88, (c.id, d.id): 0.91}),
    )
    cands = pps._build_candidates(
        db_session,
        kind="insight",
        span_granularity="day",
        threshold=0.86,
        min_spans=2,
        promoted_refs=set(),
        fact_texts=set(),
        user_id=1,
    )
    assert len(cands) == 1
    assert sorted(cands[0]["spans"]) == ["2026-09-01", "2026-09-02"]


# ── 扫描接口：模式 + 精炼与回退 ────────────────────────────


def test_scan_endpoint_raw_mode(client, db_session, monkeypatch):
    a = _pattern(db_session, "2026-W36", "跨周模式甲")
    b = _pattern(db_session, "2026-W37", "跨周模式甲变体")
    monkeypatch.setattr(
        "app.services.profile_promotion_service._similar_pairs",
        _fake_pairs({(a.id, b.id): 0.80}),
    )
    r = client.post("/profile/promotions/scan?refine=false")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "raw"
    assert data["scanned_patterns"] == 2
    assert len(data["candidates"]) == 1
    cand = data["candidates"][0]
    assert cand["category"] == "habit"
    assert cand["source_kind"] == "pattern"
    assert set(cand["spans"]) == {"2026-W36", "2026-W37"}


def test_scan_endpoint_refine_success(client, db_session, monkeypatch):
    a = _pattern(db_session, "2026-W36", "跨周模式甲")
    b = _pattern(db_session, "2026-W37", "跨周模式甲变体")
    monkeypatch.setattr(
        "app.services.profile_promotion_service._similar_pairs",
        _fake_pairs({(a.id, b.id): 0.80}),
    )
    monkeypatch.setattr(
        "app.services.profile_promotion_service.chat_json",
        lambda *a, **k: {
            "facts": [
                {
                    "index": 0,
                    "category": "preference",
                    "content": "偏好把手机放在客厅充电（推测）",
                }
            ]
        },
    )
    data = client.post("/profile/promotions/scan").json()
    assert data["mode"] == "refined"
    assert data["candidates"][0]["category"] == "preference"
    assert data["candidates"][0]["content"] == "偏好把手机放在客厅充电（推测）"


def test_scan_endpoint_refine_failure_falls_back(client, db_session, monkeypatch):
    a = _pattern(db_session, "2026-W36", "跨周模式甲")
    b = _pattern(db_session, "2026-W37", "跨周模式甲变体")
    monkeypatch.setattr(
        "app.services.profile_promotion_service._similar_pairs",
        _fake_pairs({(a.id, b.id): 0.80}),
    )

    def boom(*a, **k):
        raise LLMError("refiner down")

    monkeypatch.setattr("app.services.profile_promotion_service.chat_json", boom)
    data = client.post("/profile/promotions/scan").json()
    assert data["mode"] == "raw"
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["category"] == "habit"  # 兜底分类


# ── 写入接口：确认后落库、重复跳过 ─────────────────────────


def test_commit_promotions_writes_ai_facts(client, db_session):
    payload = {
        "candidates": [
            {
                "content": "习惯凌晨学习，但会导致白天精力下降（推测）",
                "category": "habit",
                "confidence": 0.62,
                "source_ref": "pattern:2026-W37",
                "source_kind": "pattern",
                "spans": ["2026-W36", "2026-W37"],
                "avg_similarity": 0.81,
                "members": [],
            }
        ]
    }
    r = client.post("/profile/promotions/commit", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert len(body["fact_ids"]) == 1 and body["skipped"] == 0

    facts = client.get("/profile/facts").json()
    fact = next(f for f in facts if f["id"] == body["fact_ids"][0])
    assert fact["source"] == "ai"
    assert fact["category"] == "habit"
    assert fact["confidence"] == 0.62
    assert fact["source_ref"] == "pattern:2026-W37"
    assert fact["last_confirmed_at"] is not None

    # 已晋升的 ref 再次扫描时被排除
    assert profile_service.list_facts(db_session, user_id=1)


def test_commit_promotions_skips_duplicates(client, db_session):
    profile_service.create_fact(
        db_session, category="habit", content="重复的背景事实",
        user_id=1,
    )
    payload = {
        "candidates": [
            {
                "content": "重复的背景事实",
                "category": "habit",
                "confidence": 0.6,
                "source_kind": "pattern",
            },
            {
                "content": "新的晋升事实",
                "category": "habit",
                "confidence": 0.6,
                "source_kind": "insight",
            },
        ]
    }
    body = client.post("/profile/promotions/commit", json=payload).json()
    assert len(body["fact_ids"]) == 1 and body["skipped"] == 1
    contents = {f["content"] for f in client.get("/profile/facts").json()}
    assert contents == {"重复的背景事实", "新的晋升事实"}


def test_commit_requires_at_least_one_candidate(client):
    assert client.post("/profile/promotions/commit", json={"candidates": []}).status_code == 422
