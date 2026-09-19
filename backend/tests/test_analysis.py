from app.llm import LLMError
from app.services.experiment_design_service import DESIGN_SYSTEM_PROMPT

GOOD_DISTILL = {
    "problem_candidates": [
        {
            "title": "为什么下午容易犯困？",
            "category": "精力",
            "note": "可能与午睡缺失或午餐结构有关",
            "confidence": 0.6,
        }
    ],
    "background_candidates": [
        {"category": "habit", "content": "午后效率明显低于上午(推测)", "confidence": 0.4},
        {"category": "bogus", "content": "分类非法应被丢弃", "confidence": 0.5},
        {"category": "habit", "content": "", "confidence": 0.5},
    ],
}


def test_distill_returns_validated_candidates(client, db_session, monkeypatch):
    client.post("/problems", json={"title": "为什么学习前会逃避？"})
    client.post("/profile/facts", json={"category": "habit", "content": "习惯晚睡"})
    captured = {}

    def fake_chat_json(system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return GOOD_DISTILL

    monkeypatch.setattr("app.services.analysis_service.chat_json", fake_chat_json)
    r = client.post(
        "/analysis/distill",
        json={"question": "我为什么下午犯困", "answer": "可能与睡眠不足有关(推测)。"},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["problem_candidates"]) == 1
    assert data["problem_candidates"][0]["title"] == "为什么下午容易犯困？"
    # 非法分类与空内容候选被丢弃
    assert len(data["background_candidates"]) == 1
    assert data["background_candidates"][0]["category"] == "habit"
    # 已有问题/背景进入 prompt，避免重复候选
    assert "为什么学习前会逃避？" in captured["user"]
    assert "习惯晚睡" in captured["user"]


def test_distill_llm_failure_returns_502(client, monkeypatch):
    def boom(*a, **k):
        raise LLMError("network down")

    monkeypatch.setattr("app.services.analysis_service.chat_json", boom)
    r = client.post("/analysis/distill", json={"question": "q", "answer": "a"})
    assert r.status_code == 502
    assert "network down" in r.json()["detail"]


def test_distill_rejects_empty_payload(client):
    assert client.post("/analysis/distill", json={"question": "", "answer": "a"}).status_code == 422
    assert client.post("/analysis/distill", json={"question": "q", "answer": ""}).status_code == 422


def test_commit_writes_problems_and_background_as_ai(client):
    r = client.post(
        "/analysis/commit",
        json={
            "problems": [
                {
                    "title": "为什么下午容易犯困？",
                    "category": "精力",
                    "note": "可能与午睡缺失有关",
                    "confidence": 0.6,
                }
            ],
            "background": [
                {
                    "category": "habit",
                    "content": "午后效率明显低于上午(推测)",
                    "confidence": 0.4,
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["problem_ids"]) == 1
    assert len(body["profile_fact_ids"]) == 1
    assert body["problems_skipped"] == 0
    assert body["background_skipped"] == 0

    problem = client.get("/problems").json()[0]
    assert problem["source"] == "ai"
    fact = client.get("/profile/facts").json()[0]
    assert fact["source"] == "ai"
    assert fact["confidence"] == 0.4
    assert fact["last_confirmed_at"] is not None  # 勾选写入 = 已确认


def test_commit_skips_duplicates(client):
    client.post("/problems", json={"title": "为什么下午容易犯困？"})
    client.post(
        "/profile/facts",
        json={"category": "habit", "content": "午后效率明显低于上午(推测)"},
    )
    r = client.post(
        "/analysis/commit",
        json={
            "problems": [{"title": "为什么下午容易犯困？", "confidence": 0.5}],
            "background": [
                {
                    "category": "habit",
                    "content": "午后效率明显低于上午(推测)",
                    "confidence": 0.4,
                }
            ],
        },
    )
    body = r.json()
    assert body["problem_ids"] == []
    assert body["profile_fact_ids"] == []
    assert body["problems_skipped"] == 1
    assert body["background_skipped"] == 1
    assert len(client.get("/problems").json()) == 1


def test_commit_skips_quota_overflow(client, monkeypatch):
    import app.services.profile_service as ps

    monkeypatch.setitem(ps.CATEGORY_LIMITS, "identity", 0)
    r = client.post(
        "/analysis/commit",
        json={
            "problems": [],
            "background": [
                {"category": "identity", "content": "一名后端工程师", "confidence": 0.9}
            ],
        },
    )
    body = r.json()
    assert body["profile_fact_ids"] == []
    assert body["background_skipped"] == 1
    assert client.get("/profile/facts").json() == []


def test_commit_empty_is_noop(client):
    r = client.post("/analysis/commit", json={"problems": [], "background": []})
    assert r.status_code == 200
    assert r.json() == {
        "problem_ids": [],
        "profile_fact_ids": [],
        "problems_skipped": 0,
        "background_skipped": 0,
    }
    assert client.get("/problems").json() == []
    assert client.get("/profile/facts").json() == []


# ── AI 实验设计（问题 → 草稿） ─────────────────────────────

GOOD_DRAFT = {
    "name": "睡前手机隔离",
    "question": "睡前把手机留在客厅能否改善睡眠？",
    "hypothesis": "睡前不把手机带进卧室会减少手机使用时长",
    "variable": "22:30 后手机留在客厅充电",
    "indicator": "手机时长与次日专注",
    "metrics": [
        {
            "key": "phone_min",
            "name": "手机时长",
            "unit": "分钟",
            "direction": "down_good",
            "source": "event_duration:PHONE_START",
        },
        {
            "key": "bogus",
            "name": "非法指标",
            "unit": None,
            "direction": "neutral",
            "source": "event_duration:NOT_A_TYPE",
        },
    ],
    "expected_days": 999,  # 超限 → 应被夹到 30
    "baseline_note": "当前夜间手机约 60-90 分钟",
    "rationale": "用手机时长做核心指标，自动聚合，减少手动负担。",
}


def test_experiment_draft_generates_and_validates(client, db_session, monkeypatch):
    p = client.post("/problems", json={"title": "为什么睡前难以放下手机？"}).json()
    client.post(
        "/findings",
        json={
            "problem_id": p["id"],
            "kind": "HYPOTHESIS",
            "title": "习惯触发",
            "observation": "刷手机时常说'忍不住'",
        },
    )
    captured = {}

    def fake_chat_json(system, user, **kw):
        captured["user"] = user
        return GOOD_DRAFT

    monkeypatch.setattr(
        "app.services.experiment_design_service.chat_json", fake_chat_json
    )

    r = client.post("/analysis/experiment-draft", json={"problem_id": p["id"]})
    assert r.status_code == 200
    data = r.json()
    assert data["problem_id"] == p["id"]
    assert data["name"] == "睡前手机隔离"
    # 非法 source 被丢弃，只留合法指标
    assert [m["key"] for m in data["metrics"]] == ["phone_min"]
    assert data["metrics"][0]["source"] == "event_duration:PHONE_START"
    # expected_days 夹到 30
    assert data["expected_days"] == 30
    # 依据可追溯：问题+发现进 prompt，证据带 finding ref
    assert "为什么睡前难以放下手机？" in captured["user"]
    assert "习惯触发" in captured["user"]
    assert any(e["kind"] == "finding" for e in data["evidence"])


def test_experiment_draft_falls_back_to_manual_metric(client, db_session, monkeypatch):
    p = client.post("/problems", json={"title": "为什么下午容易犯困？"}).json()
    monkeypatch.setattr(
        "app.services.experiment_design_service.chat_json",
        lambda *a, **k: {**GOOD_DRAFT, "metrics": [{"key": "x", "source": "bogus"}]},
    )
    r = client.post("/analysis/experiment-draft", json={"problem_id": p["id"]})
    assert r.status_code == 200
    metrics = r.json()["metrics"]
    assert len(metrics) == 1
    assert metrics[0]["source"] == "manual"


def test_experiment_draft_llm_failure_502(client, db_session, monkeypatch):
    p = client.post("/problems", json={"title": "测试问题"}).json()

    def boom(*a, **k):
        raise LLMError("designer down")

    monkeypatch.setattr("app.services.experiment_design_service.chat_json", boom)
    r = client.post("/analysis/experiment-draft", json={"problem_id": p["id"]})
    assert r.status_code == 502
    assert "designer down" in r.json()["detail"]


def test_experiment_draft_problem_not_found(client):
    assert client.post("/analysis/experiment-draft", json={"problem_id": 999}).status_code == 404


def test_experiment_create_rejects_bogus_metric_source(client):
    r = client.post(
        "/experiments",
        json={
            "name": "非法指标实验",
            "metrics": [
                {
                    "key": "x",
                    "name": "X",
                    "direction": "up_good",
                    "source": "event_duration:NOT_A_TYPE",
                }
            ],
        },
    )
    assert r.status_code == 422


def test_design_prompt_lists_current_record_types_and_categories():
    """prompt 的数据源清单由常量生成：撤下的旧键不能再出现，新类型/新大类必须在。

    这个清单曾经是手写的，漂过（还写着 GAME_START/PHONE_START，大类少了金融理财等），
    所以用测试钉住「与 creatable_builtins/ONLINE_CATEGORIES 一致」。
    """
    from app.behavior_categories import CATEGORIES, ONLINE_CATEGORIES
    from app.services.event_type_service import creatable_builtins

    prompt = DESIGN_SYSTEM_PROMPT
    assert "__EVENT_TYPES__" not in prompt and "__ONLINE_CATEGORIES__" not in prompt
    assert "GAME_START" not in prompt and "PHONE_START" not in prompt
    for key in creatable_builtins():
        assert key in prompt
    for cat in (c for c in CATEGORIES if c in ONLINE_CATEGORIES):
        assert cat in prompt
