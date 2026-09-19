"""演示账号的 AI 封锁边界。

demo 一旦公网可写，任何人都能反复触发 AI 生成，每次都是真实的 DeepSeek 调用
（10 天日报 = 10 次），额度会被陌生人和爬虫烧掉。而演示并不需要现场跑 AI：
产物已经预置好，静态展示反而更快更好看。所以口径是 **读全开、写放行、AI 全封**。

这里逐个钉住被封锁的端点——漏掉一个就是一个烧额度的口子；同样重要的是钉住
那些「看起来像 AI、其实不该封」的端点，封错了或者封多了都是故障。
"""

import pytest

from app.deps import AI_DEMO_NOTICE

# (method, path, body)；body=None 表示不带请求体
AI_ENDPOINTS = [
    ("post", "/reviews", {"date": "2026-09-01"}),
    ("post", "/reviews/period", {"period_type": "week", "date": "2026-09-01"}),
    ("post", "/memory/ask", {"question": "最近状态怎么样"}),
    ("post", "/memory/extract", {"date": "2026-09-01"}),
    ("post", "/memory/extract-week", {"date": "2026-09-01"}),
    ("post", "/memory/items", {"kind": "insight", "content": "手工写一条"}),
    ("post", "/memory/sync", None),
    ("post", "/analysis/distill", {"question": "q", "answer": "a"}),
    ("post", "/analysis/experiment-draft", {"problem_id": 1}),
    ("post", "/profile/promotions/scan", None),
    ("post", "/agent/run", {"query": "最近状态怎么样"}),
]


@pytest.mark.parametrize(
    "method,path,body", AI_ENDPOINTS, ids=[e[1] for e in AI_ENDPOINTS]
)
def test_ai_endpoints_blocked_for_demo(client, as_demo, method, path, body):
    as_demo()
    call = getattr(client, method)
    r = call(path) if body is None else call(path, json=body)
    assert r.status_code == 403, r.text
    # 拒绝信息要写成人话并给出替代 —— 裸 403 对访客太冷
    assert r.json()["detail"] == AI_DEMO_NOTICE


def test_normal_user_is_not_blocked(client):
    """封锁只认 is_demo。普通账号不该被这个守卫拦下（其余报错另算）。"""
    for method, path, body in AI_ENDPOINTS:
        call = getattr(client, method)
        r = call(path) if body is None else call(path, json=body)
        assert r.status_code != 403, f"{path} 被误封：{r.text}"


def test_readonly_extract_gets_stay_open_for_demo(client, as_demo):
    """这两个 GET 是纯读 memory_items，不碰 LLM。

    只按「POST 才危险」的思路封会漏，但只按「名字像 AI」的思路封会**误封**：
    封了它们，访客就看不到预置的洞察与周模式了。单独钉住。
    """
    as_demo()
    assert client.get("/memory/extract/2026-09-01").status_code == 200
    assert client.get("/memory/extract-week/2026-09-01").status_code == 200


def test_demo_can_still_record_and_read(client, as_demo):
    """写放行：交互要真实，HR 能点记录。读也全开。"""
    as_demo()
    assert client.post("/events", json={"type": "LEARNING_START"}).status_code == 201
    assert client.post("/thoughts", json={"content": "演示账号写下的一条随想"}).status_code == 201
    assert client.post(
        "/states", json={"energy": 3, "focus": 4, "irritation": 1}
    ).status_code == 201
    assert client.get("/events").status_code == 200
    assert client.get("/thoughts").status_code == 200


def test_demo_can_still_search_memory(client, as_demo):
    """检索只走本地 Ollama 向量化（无云 key 时退化为关键词），零外部成本 → 不封。"""
    as_demo()
    assert client.post("/memory/search", json={"query": "晚睡"}).status_code == 200
