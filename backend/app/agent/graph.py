"""有状态 Agent 图（M6 第一步）。

    START → agent ─┬─(无工具调用)──────────────────→ END
                   └─(有工具调用)→ tools ─┬─(只剩只读)──→ agent
                                          └─(有待确认的写)→ approval → agent

- `agent`：把 messages 发给 LLM，拿回文本或 tool_calls
- `tools` ：只读调用当场执行；写调用**不执行**，只登记进 `pending`
- `approval`：`interrupt()` 停下来等人确认；批准才落库

**为什么不用 `create_react_agent` + `interrupt_before=["tools"]`**：那条路会对
*所有*工具中断，查个记忆都要点一次确认。这里按「工具名是否写库」路由，只拦写。

**为什么图每请求重编、checkpointer 却是单例**：节点要闭包捕获请求作用域的
`Session`（Session 不能跨请求复用，也不能进 state）。compile 很便宜，
真正的状态在 checkpointer 里，进程级共享。
"""

import json
import logging
from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.orm import Session

from app.agent.checkpointer import get_checkpointer
from app.agent.state import AgentState
from app.agent.tools import (
    TOOLS,
    WRITE_TOOLS,
    ToolCtx,
    describe_call,
    dumps,
    run_tool,
)
from app.llm import LLMError, chat_with_tools

logger = logging.getLogger(__name__)

# 一轮对话最多几次 LLM 往返（等价旧 agent_service.MAX_STEPS）
MAX_TURNS = 8
# 每个 turn 最多走 agent→tools→approval 三个超步，留点余量给收尾
RECURSION_LIMIT = MAX_TURNS * 3 + 5

SYSTEM_PROMPT = """你是 LifeLab 的个人行为研究员，帮用户理解自己的记录、实验和规律。

【可用工具】
只读，直接调用，不用等确认：
  search_memory(query)                 语义检索历史记忆/记录
  list_experiments()                   实验清单（含 id）
  get_experiment_stats(id)             某实验的指标统计与运行天数
  get_review(period_type, date?)       看某日/某周/某月的复盘（日粒度还带回当天
                                       小结与记录量统计）
  list_problems(status?)               长期问题清单
  list_findings(...)                   发现/结论清单
  list_profile_facts()                 背景画像
  distill_analysis(question, answer)   把一次问答精炼成问题/背景候选（不落库）
  design_experiment(problem_id)        从某个问题设计实验草稿（不落库）
  infer_metric_sources(metrics)        把指标名识别成数据源（不落库）
写库，**会先弹给用户确认，确认前什么都没写**：
  create_finding(...)                  沉淀一条发现
  create_problem(title, ...)           写入一个长期问题
  create_profile_fact(category, content)  写入一条背景事实
  save_day_note(content, date?)        保存某日小结
  generate_review(period_type, date?, force?)  生成日/周/月复盘

【何时调工具】
1. 问到自己的历史或数据（"我上周的实验数据怎么样"、"我有没有…"）：先用对应
   只读工具拿真实数据，再回答。不要凭印象答。
2. 用户要复盘（"帮我复盘今天"）：先确认复盘哪一天/哪一周，再调 generate_review。
   复盘会写库，必须经过审批卡。
3. 用户明确要沉淀（"记下来"、"记一条规律"）：调 create_finding。想沉淀成长期
   问题/背景：先 distill_analysis 拿候选，再逐条 create_problem /
   create_profile_fact。
4. 用户想把某个问题变成实验：先 list_problems 拿 id，再 design_experiment 出草稿，
   然后引导用户去「实验」页确认创建（这里不直接建实验）。
5. 普通问题：直接回答，不调工具。

【写库的说法】
调写工具后，工具结果会告诉你用户批没批。**拿到结果前不要说"已记录/已生成"**；
用户拒绝时说"好的，不记录"，不要改个措辞再试一次。

【回答风格】
中文，简洁，先结论后依据。数据不足就说不足，不要编。推测标注「(推测)」。
提到实验或发现时带上 id，方便用户去对应页面核对。
"""


def _parse_args(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.warning("工具参数不是合法 JSON：%r", raw)
        return {}


def _tool_msg(call_id: str, result: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": dumps(result)}


def _agent_node(db: Session) -> Callable[[AgentState], dict]:
    def node(state: AgentState) -> dict:
        turns = state.get("turns", 0)
        if turns >= MAX_TURNS:
            return {
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"（已达本轮工具调用上限 {MAX_TURNS} 次，先给到这里）",
                    }
                ]
            }
        # system 提示词每轮现拼、不进 state：减小 checkpoint 体积，
        # 改提示词也能立刻对老会话生效。
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *state["messages"]]
        try:
            content, tool_calls = chat_with_tools(messages, TOOLS, caller="agent.turn")
        except LLMError as e:
            return {"messages": [{"role": "assistant", "content": f"(Agent 调用失败：{e})"}]}

        assistant: dict = {"role": "assistant", "content": content or ""}
        if tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls
            ]
        return {"messages": [assistant], "turns": turns + 1}

    return node


def _route_after_agent(state: AgentState) -> str:
    return "tools" if state["messages"][-1].get("tool_calls") else END


def _ctx(state: AgentState) -> ToolCtx:
    return ToolCtx(
        user_id=state["user_id"], tz_offset=int(state.get("tz_offset") or 0)
    )


def _tools_node(db: Session) -> Callable[[AgentState], dict]:
    def node(state: AgentState) -> dict:
        calls = state["messages"][-1].get("tool_calls") or []
        messages: list[dict] = []
        steps: list[dict] = []
        pending: list[dict] = []
        for call in calls:
            name = call["function"]["name"]
            args = _parse_args(call["function"].get("arguments"))
            if name in WRITE_TOOLS:
                # 写调用不在这里执行 —— 交给 approval 节点先问人
                pending.append(
                    {
                        "id": call["id"],
                        "name": name,
                        "args": args,
                        "summary": describe_call(name, args),
                    }
                )
                continue
            result = run_tool(db, name, args, _ctx(state))
            messages.append(_tool_msg(call["id"], result))
            # approved=None：只读工具不经审批，与 approval 节点产出的步骤同形
            steps.append({"tool": name, "args": args, "result": result, "approved": None})
        return {"messages": messages, "steps": steps, "pending": pending or None}

    return node


def _route_after_tools(state: AgentState) -> str:
    return "approval" if state.get("pending") else "agent"


def _approval_node(db: Session) -> Callable[[AgentState], dict]:
    def node(state: AgentState) -> dict:
        pending = state.get("pending") or []
        # interrupt() 必须是本节点第一句有副作用的话：恢复时本节点会**从头整体
        # 重跑**，放在它前面的写操作会执行两次。pending 从 checkpoint 重放，
        # 值与中断时一致。
        decision = interrupt({"kind": "approval_request", "calls": pending}) or {}
        approved = bool(decision.get("approve"))
        # 用户可以在审批卡里改参数 —— 但落库前一律重走工具自己的校验，
        # 前端传来的内容不能直接当落库内容用。
        override = decision.get("args") or {}
        messages: list[dict] = []
        steps: list[dict] = []
        for call in pending:
            args = override or call["args"]
            if approved:
                result = run_tool(db, call["name"], args, _ctx(state))
            else:
                result = {
                    "denied": True,
                    "reason": decision.get("note") or "用户拒绝了这次写入",
                }
            # 拒绝也必须回一条 tool 消息：漏答 tool_call，下一轮 LLM 会因
            # 「tool_calls 未被全部应答」直接报 400。
            messages.append(_tool_msg(call["id"], result))
            steps.append(
                {"tool": call["name"], "args": args, "result": result, "approved": approved}
            )
        return {"messages": messages, "steps": steps, "pending": None}

    return node


def get_graph(db: Session):
    builder = StateGraph(AgentState)
    builder.add_node("agent", _agent_node(db))
    builder.add_node("tools", _tools_node(db))
    builder.add_node("approval", _approval_node(db))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _route_after_agent, ["tools", END])
    builder.add_conditional_edges("tools", _route_after_tools, ["approval", "agent"])
    builder.add_edge("approval", "agent")
    return builder.compile(checkpointer=get_checkpointer())


def last_answer(messages: list[dict]) -> str:
    """最后一条有内容的 assistant 消息 = 给用户看的回答。"""
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return message["content"]
    return ""
