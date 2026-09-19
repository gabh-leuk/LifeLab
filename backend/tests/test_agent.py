"""Agent 状态图测试（M6）。

测试跑在 SQLite 上，所以 checkpointer 一律换成 `InMemorySaver`。真 Postgres
checkpointer 的那条性质（**换进程**也能恢复中断态）由手工冒烟覆盖，
这里只测图的语义与红线。

假 LLM 沿用旧约定：monkeypatch 掉 service 模块里的 `chat_with_tools`，
返回 `(content, tool_calls)` 或一串这样的序列。
"""

import sys
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import seed_demo

from app.agent import graph as agent_graph
from app.agent.tools import READ_TOOLS, TOOLS, WRITE_TOOL_FNS, WRITE_TOOLS
from app.llm import LLMError
from app.models.agent_thread import AgentThread
from app.models.day_note import DayNote
from app.models.finding import Finding
from app.models.problem import Problem
from app.models.review import DailyReview
from app.schemas.agent import AgentStateResponse

WRITE_ARGS = '{"title":"测试发现","observation":"一条观察到的事实"}'


def _call(name, args="{}", cid="c1"):
    return {"id": cid, "name": name, "arguments": args}


def _cfg(thread_id):
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": agent_graph.RECURSION_LIMIT,
    }


def _run(db, query, thread_id, user_id=1, tz_offset=0):
    return agent_graph.get_graph(db).invoke(
        {
            "messages": [{"role": "user", "content": query}],
            "user_id": user_id,
            "tz_offset": tz_offset,
            "turns": 0,
        },
        _cfg(thread_id),
    )


def _resume(db, thread_id, **decision):
    return agent_graph.get_graph(db).invoke(
        Command(resume=decision), _cfg(thread_id)
    )


@pytest.fixture(autouse=True)
def mem_checkpointer(monkeypatch):
    """每个测试一个干净的内存 checkpointer（不碰 Postgres）。"""
    saver = InMemorySaver()
    monkeypatch.setattr(agent_graph, "get_checkpointer", lambda: saver)
    return saver


@pytest.fixture
def llm(monkeypatch):
    """按脚本依次返回；也可以直接给一个函数（用于抛异常）。"""

    def _set(script):
        if callable(script):
            monkeypatch.setattr(agent_graph, "chat_with_tools", script)
            return
        box = {"i": 0}

        def fake(messages, tools, **kw):
            i = min(box["i"], len(script) - 1)
            box["i"] += 1
            return script[i]

        monkeypatch.setattr(agent_graph, "chat_with_tools", fake)

    return _set


# ── 只读：直接跑，不打扰用户 ────────────────────────────────


def test_read_tool_runs_without_interrupt(db_session, llm):
    llm([(None, [_call("list_experiments")]), ("你有两个实验。", None)])

    res = _run(db_session, "我有哪些实验", "t-read")

    assert "__interrupt__" not in res
    assert [s["tool"] for s in res["steps"]] == ["list_experiments"]
    assert res["steps"][0]["approved"] is None
    assert agent_graph.last_answer(res["messages"]) == "你有两个实验。"


# ── 写库：必须停下等确认，且确认前一个字节都不许落库 ─────────


def test_write_tool_interrupts_and_writes_nothing(db_session, llm):
    llm([(None, [_call("create_finding", WRITE_ARGS)]), ("记下了。", None)])

    res = _run(db_session, "把这条记下来", "t-write")

    assert "__interrupt__" in res
    assert res["pending"][0]["name"] == "create_finding"
    assert res["pending"][0]["summary"]  # 给人看的摘要
    assert db_session.scalar(select(Finding)) is None


def test_approve_persists_finding(db_session, llm):
    llm([(None, [_call("create_finding", WRITE_ARGS)]), ("已记录。", None)])
    _run(db_session, "记下来", "t-approve")

    res = agent_graph.get_graph(db_session).invoke(
        Command(resume={"approve": True}), _cfg("t-approve")
    )

    finding = db_session.scalar(select(Finding))
    assert finding is not None
    assert finding.user_id == 1
    assert res["steps"][-1]["approved"] is True
    assert agent_graph.last_answer(res["messages"]) == "已记录。"


def test_reject_writes_nothing_and_continues(db_session, llm):
    llm([(None, [_call("create_finding", WRITE_ARGS)]), ("好的，不记录。", None)])
    _run(db_session, "记下来", "t-reject")

    res = agent_graph.get_graph(db_session).invoke(
        Command(resume={"approve": False}), _cfg("t-reject")
    )

    assert db_session.scalar(select(Finding)) is None
    assert res["steps"][-1]["approved"] is False
    assert res["steps"][-1]["result"]["denied"] is True
    # 拒绝也要回一条 tool 消息，否则下一轮 LLM 会因 tool_call 未被应答而 400
    assert agent_graph.last_answer(res["messages"]) == "好的，不记录。"


def test_approval_args_are_revalidated_server_side(db_session, llm):
    """审批卡可以改参数，但落库内容必须重走服务端校验 —— 不能信前端。"""
    llm([(None, [_call("create_finding", WRITE_ARGS)]), ("好。", None)])
    _run(db_session, "记下来", "t-inject")

    res = agent_graph.get_graph(db_session).invoke(
        Command(resume={"approve": True, "args": {"title": "", "observation": ""}}),
        _cfg("t-inject"),
    )

    assert db_session.scalar(select(Finding)) is None  # 空 title 过不了 FindingCreate
    assert "error" in res["steps"][-1]["result"]


# ── 状态归属：真在 checkpointer 里，不在闭包里 ──────────────


def test_state_survives_a_fresh_graph(db_session, llm):
    """中断发生在一个 graph 实例，恢复在另一个实例 —— 模拟两个 HTTP 请求。"""
    llm([(None, [_call("create_finding", WRITE_ARGS)]), ("已记录。", None)])
    _run(db_session, "记下来", "t-resume")

    other = agent_graph.get_graph(db_session)  # 重新 compile
    assert other.get_state(_cfg("t-resume")).next  # 待确认态还在

    other.invoke(Command(resume={"approve": True}), _cfg("t-resume"))
    assert db_session.scalar(select(Finding)) is not None


def test_threads_are_isolated(db_session, llm):
    llm([("只回一句。", None)])
    _run(db_session, "甲", "t-a")
    llm([("另一句。", None)])
    _run(db_session, "乙", "t-b")

    values = agent_graph.get_graph(db_session).get_state(_cfg("t-a")).values
    assert [m["content"] for m in values["messages"] if m["role"] == "user"] == ["甲"]


def test_llm_error_is_graceful(db_session, llm):
    def boom(messages, tools, **kw):
        raise LLMError("额度用尽")

    llm(boom)
    res = _run(db_session, "你好", "t-err")

    assert "__interrupt__" not in res
    assert "失败" in agent_graph.last_answer(res["messages"])


# ── 第二步：复盘与剖析搬进来的工具 ──────────────────────────

REVIEW_CALL = '{"period_type":"day","date":"2026-09-10"}'
PROBLEM_CALL = '{"title":"为什么晚上停不下来","category":"habit"}'
NOTE_CALL = '{"content":"今天很累","date":"2026-09-10"}'


def test_generate_review_interrupts_and_writes_nothing(db_session, llm):
    """复盘也是 AI 产物：没点确认之前，daily_reviews 里不能有行。"""
    llm([(None, [_call("generate_review", REVIEW_CALL)]), ("写好了。", None)])

    res = _run(db_session, "帮我复盘 9 月 10 号", "t-rev")

    assert "__interrupt__" in res
    assert res["pending"][0]["name"] == "generate_review"
    assert res["pending"][0]["summary"] == "生成日复盘（2026-09-10）"
    assert db_session.scalar(select(DailyReview)) is None


def test_approve_generates_review(db_session, llm):
    llm([(None, [_call("generate_review", REVIEW_CALL)]), ("写好了。", None)])
    _run(db_session, "复盘", "t-rev-ok")

    _resume(db_session, "t-rev-ok", approve=True)

    row = db_session.scalar(select(DailyReview))
    assert row is not None
    assert (row.date, row.user_id) == ("2026-09-10", 1)


def test_create_problem_is_gated_and_tagged_ai(db_session, llm):
    llm([(None, [_call("create_problem", PROBLEM_CALL)]), ("记下了。", None)])
    _run(db_session, "把这条记成我的一个问题", "t-prob")
    assert db_session.scalar(select(Problem)) is None

    _resume(db_session, "t-prob", approve=True)

    row = db_session.scalar(select(Problem))
    assert row is not None
    assert (row.user_id, row.source, row.status) == (1, "ai", "OPEN")


def test_save_day_note_is_gated(db_session, llm):
    llm([(None, [_call("save_day_note", NOTE_CALL)]), ("存好了。", None)])
    _run(db_session, "记一下今天的小结", "t-note")
    assert db_session.scalar(select(DayNote)) is None

    _resume(db_session, "t-note", approve=True)

    row = db_session.scalar(select(DayNote))
    assert row is not None
    assert (row.date, row.content) == ("2026-09-10", "今天很累")


def test_distill_is_read_only(db_session, llm, monkeypatch):
    """剖析的提炼只出候选，不落库 —— 落库要等逐条 create_problem。"""
    monkeypatch.setattr(
        "app.services.analysis_service.chat_json",
        lambda *a, **k: {
            "problem_candidates": [{"title": "为什么逃避", "confidence": 0.6}],
            "background_candidates": [
                {"category": "habit", "content": "压力大时熬夜", "confidence": 0.4}
            ],
        },
    )
    llm(
        [
            (
                None,
                [
                    _call(
                        "distill_analysis",
                        '{"question":"我为什么逃避","answer":"可能怕失败"}',
                    )
                ],
            ),
            ("提炼好了。", None),
        ]
    )

    res = _run(db_session, "把这次对话提炼成问题", "t-distill")

    assert "__interrupt__" not in res
    step = res["steps"][0]
    assert step["tool"] == "distill_analysis" and step["approved"] is None
    assert step["result"]["problem_candidates"][0]["title"] == "为什么逃避"
    assert db_session.scalar(select(Problem)) is None


def test_design_experiment_is_read_only(db_session, llm, monkeypatch):
    db_session.add(Problem(user_id=1, title="为什么晚上停不下来", status="OPEN"))
    db_session.commit()
    monkeypatch.setattr(
        "app.services.experiment_design_service.chat_json",
        lambda *a, **k: {
            "name": "早睡实验",
            "question": "提前上床会改善精力吗",
            "hypothesis": "会",
            "metrics": [{"key": "usage_total", "direction": "down"}],
            "expected_days": 14,
        },
    )
    llm([(None, [_call("design_experiment", '{"problem_id":1}')]), ("草稿好了。", None)])

    res = _run(db_session, "把问题 1 变成实验", "t-design")

    assert "__interrupt__" not in res
    assert res["steps"][0]["result"]["name"] == "早睡实验"


def test_missing_problem_gives_tool_error_not_a_crash(db_session, llm):
    llm([(None, [_call("design_experiment", '{"problem_id":999}')]), ("没找到。", None)])

    res = _run(db_session, "把问题 999 变成实验", "t-design-404")

    assert "__interrupt__" not in res
    assert "error" in res["steps"][0]["result"]


def test_infer_metric_sources_is_read_only(db_session, llm, monkeypatch):
    """识别工具只返回 source，不弹审批、不落库（①b）。"""
    monkeypatch.setattr(
        "app.services.metric_source_service.chat_json",
        lambda *a, **k: {"sources": {"phone": "usage_platform:android@22-24"}},
    )
    llm(
        [
            (
                None,
                [
                    _call(
                        "infer_metric_sources",
                        '{"metrics":[{"key":"phone","name":"睡前手机时长"}]}',
                    )
                ],
            ),
            ("识别好了。", None),
        ]
    )

    res = _run(db_session, "这些指标该从哪取数", "t-infer")

    assert "__interrupt__" not in res
    assert "infer_metric_sources" in READ_TOOLS
    assert "infer_metric_sources" not in WRITE_TOOLS
    step = res["steps"][0]
    assert step["tool"] == "infer_metric_sources" and step["approved"] is None
    assert step["result"]["sources"]["phone"] == "usage_platform:android@22-24"


def test_infer_metric_sources_without_metrics_is_a_tool_error(db_session, llm):
    """空 metrics 直接返回 error —— 不该白烧一次 LLM 调用。"""
    llm([(None, [_call("infer_metric_sources", "{}")]), ("要指标名。", None)])

    res = _run(db_session, "识别一下", "t-infer-empty")

    assert "error" in res["steps"][0]["result"]


def test_tz_offset_is_stored_in_state(db_session, llm):
    """复盘按本地时区分天，而恢复是另一个请求 —— 时区必须进 state。"""
    llm([("好。", None)])
    _run(db_session, "你好", "t-tz", tz_offset=480)

    values = agent_graph.get_graph(db_session).get_state(_cfg("t-tz")).values
    assert values["tz_offset"] == 480


# ── HTTP 层 ─────────────────────────────────────────────────


def test_run_endpoint_creates_and_lists_thread(client, llm):
    llm([("你好呀。", None)])

    body = client.post("/agent/run", json={"query": "你好"}).json()

    assert body["status"] == "answer"
    assert body["thread_id"].startswith("u1-")
    assert [t["thread_id"] for t in client.get("/agent/threads").json()] == [
        body["thread_id"]
    ]
    restored = client.get(f"/agent/threads/{body['thread_id']}").json()
    assert [m["content"] for m in restored["messages"]] == ["你好", "你好呀。"]


def test_run_endpoint_interrupt_then_resume(client, llm):
    llm([(None, [_call("create_finding", WRITE_ARGS)]), ("已记录。", None)])

    body = client.post("/agent/run", json={"query": "记下来"}).json()
    assert body["status"] == "interrupt"
    assert body["pending"][0]["name"] == "create_finding"

    done = client.post(
        "/agent/resume", json={"thread_id": body["thread_id"], "approve": True}
    ).json()
    assert done["status"] == "answer"
    assert done["steps"][-1]["approved"] is True


def test_resume_without_pending_returns_409(client, llm):
    llm([("直接答。", None)])
    thread_id = client.post("/agent/run", json={"query": "你好"}).json()["thread_id"]

    r = client.post("/agent/resume", json={"thread_id": thread_id, "approve": True})

    assert r.status_code == 409


def test_other_users_thread_is_404(client, llm, login_as):
    llm([("你好呀。", None)])
    thread_id = client.post("/agent/run", json={"query": "你好"}).json()["thread_id"]

    login_as(2)

    assert client.get(f"/agent/threads/{thread_id}").status_code == 404
    assert (
        client.post(
            "/agent/resume", json={"thread_id": thread_id, "approve": True}
        ).status_code
        == 404
    )
    # 也不能拿别人的 thread_id 接着聊
    assert (
        client.post("/agent/run", json={"query": "继续", "thread_id": thread_id}).status_code
        == 404
    )


def test_agent_is_blocked_for_demo(client, as_demo):
    as_demo()

    assert client.post("/agent/run", json={"query": "你好"}).status_code == 403
    assert client.post("/agent/resume", json={"thread_id": "x", "approve": True}).status_code == 403


# ── demo 只读回放（seed 的静态快照） ────────────────────────
#
# demo 的 AI 全封，跑不出 checkpoint；Agent 页靠 agent_threads.replay 回放。


def _replay_row(user_id: int = 1, thread_id: str = "replay-1-1") -> AgentThread:
    return AgentThread(
        user_id=user_id,
        thread_id=thread_id,
        title="我上周的实验数据怎么样？",
        replay={
            "status": "answer",
            "answer": "**手机时长**从 80 分钟降到 17 分钟。",
            "messages": [{"role": "user", "content": "我上周的实验数据怎么样？"}],
            "steps": [
                {
                    "tool": "list_experiments",
                    "args": {},
                    "result": {"experiments": []},
                    "approved": None,
                }
            ],
            "pending": [],
        },
    )


def test_replay_thread_renders_without_a_checkpoint(client, db_session):
    """没有 checkpoint 也要能读出整段会话 —— 这正是回放存在的理由。"""
    db_session.add(_replay_row())
    db_session.commit()

    assert [t["thread_id"] for t in client.get("/agent/threads").json()] == ["replay-1-1"]

    body = client.get("/agent/threads/replay-1-1").json()
    assert body["status"] == "answer"
    assert body["answer"].startswith("**手机时长**")
    assert body["steps"][0]["tool"] == "list_experiments"
    assert body["pending"] == []


def test_replay_is_readable_by_demo_but_still_not_writable(client, as_demo, db_session):
    """公网作品集能看到回放，但跑不了图 —— 读写门禁是两回事。"""
    db_session.add(_replay_row())
    db_session.commit()
    as_demo()

    assert client.get("/agent/threads/replay-1-1").status_code == 200
    assert client.post("/agent/run", json={"query": "你好"}).status_code == 403


def test_replay_still_belongs_to_its_user(client, db_session, login_as):
    db_session.add(_replay_row(user_id=1))
    db_session.commit()

    login_as(2)

    assert client.get("/agent/threads/replay-1-1").status_code == 404
    assert client.get("/agent/threads").json() == []


def test_seeded_replays_are_valid_and_show_the_write_gate():
    """种子里的三段文案要能过响应模型；并且至少有一段演示「审批后落库」，
    否则公网作品集看不出这条红线。"""
    for title, replay in seed_demo.AGENT_REPLAYS:
        body = AgentStateResponse.model_validate({"thread_id": "x", **replay})
        assert title and body.answer and body.messages
        # 前端渲染的是 messages 而不是 answer —— 快照里没有 assistant 那条就是空白页
        assert body.messages[-1].role == "assistant"
        assert body.messages[-1].content == body.answer
        assert not body.pending, "回放是只读的：不能带待审批项，否则按钮点了会 403"

    approved = [
        step
        for _title, replay in seed_demo.AGENT_REPLAYS
        for step in replay["steps"]
        if step.get("approved") is True
    ]
    assert approved, "三段回放里没有一条经审批落库的写操作"


# ── 红线护栏：工具声明与审批名单必须对齐 ────────────────────


def test_every_declared_tool_is_classified_and_writes_stay_gated():
    """`TOOLS` 里声明的每个工具都要有实现，且**写实现必须在 WRITE_TOOLS 名单里**。

    漏一个就是静默的洞：工具能被 LLM 选中、也有实现，却不在 WRITE_TOOLS 里 ——
    于是它在 tools 节点当场执行，绕开审批卡。旧的无状态 Agent 正是这么漏的
    （`create_finding` 直接落库）。这条断言把「加写工具时必须同时登记审批」钉死。
    """
    declared = {t["function"]["name"] for t in TOOLS}
    read = set(READ_TOOLS)
    write = set(WRITE_TOOL_FNS)

    assert declared == read | write, "有工具没实现，或实现没在 TOOLS 里声明"
    assert not (read & write), "同一个工具不能既算只读又有写实现"
    assert write == set(WRITE_TOOLS), "写实现与 WRITE_TOOLS 名单不一致"
