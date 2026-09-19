"""Agent 会话式接口（M6）。

会话状态存在 Postgres checkpoint 里，**进程重启不丢**：中断（等用户确认）之后
重启服务，再读状态仍是待确认。

端点与门禁：
- `POST /agent/run`、`/agent/resume` → `AiUserDep`（会真打 LLM，demo 一律 403）
- `GET  /agent/threads`、`/agent/threads/{id}` → `UserDep`（纯读库、零 LLM 成本，
  所以不挂 AI 门禁）。demo 的 Agent 页之所以不是一片禁用态，靠的就是这条：
  seed 写进去的 `agent_threads.replay` 快照在这里原样回放，只读、点不动。
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.graph import RECURSION_LIMIT, get_graph, last_answer
from app.deps import AiUserDep, DbDep, UserDep
from app.models.agent_thread import AgentThread
from app.schemas.agent import (
    AgentMessage,
    AgentResumeRequest,
    AgentRunRequest,
    AgentStateResponse,
    AgentStep,
    AgentThreadRead,
    PendingCall,
)

router = APIRouter(prefix="/agent", tags=["agent"])

TITLE_LEN = 30


def _config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }


def _owned_thread(db: Session, thread_id: str, user_id: int) -> AgentThread:
    """会话归属校验。**不靠「uuid 猜不到」当授权** —— 那是侥幸不是授权。"""
    row = db.scalar(select(AgentThread).where(AgentThread.thread_id == thread_id))
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return row


def _claim_thread(db: Session, thread_id: str, user_id: int, query: str) -> AgentThread:
    """只有**新生成**的 thread_id 能走到这里建行；外部传入的必须先过 _owned_thread。

    否则会有个洞：伪造一个别人的 thread_id（checkpoint 里已有状态、但
    agent_threads 里没有行）就能顺走那段对话继续写。
    """
    row = AgentThread(
        user_id=user_id, thread_id=thread_id, title=query.strip()[:TITLE_LEN] or "新对话"
    )
    db.add(row)
    db.commit()
    return row


def _touch(db: Session, row: AgentThread) -> None:
    row.updated_at = datetime.now(timezone.utc)
    db.commit()


def _chat_messages(messages: list[dict]) -> list[AgentMessage]:
    """只留能当气泡显示的。带 tool_calls 的 assistant 消息 content 为空，跳过；
    tool 消息由 steps 承载，不进对话流。"""
    return [
        AgentMessage(role=m["role"], content=m["content"])
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]


def _pending_from_calls(calls: list[dict]) -> list[PendingCall]:
    return [PendingCall(**c) for c in calls]


def _from_invoke(thread_id: str, result: dict) -> AgentStateResponse:
    interrupts = result.get("__interrupt__") or ()
    pending: list[PendingCall] = []
    if interrupts:
        pending = _pending_from_calls((interrupts[0].value or {}).get("calls", []))
    messages = result.get("messages") or []
    return AgentStateResponse(
        thread_id=thread_id,
        status="interrupt" if pending else "answer",
        answer="" if pending else last_answer(messages),
        messages=_chat_messages(messages),
        steps=[AgentStep(**s) for s in (result.get("steps") or [])],
        pending=pending,
    )


@router.post("/run", response_model=AgentStateResponse)
def run_agent(payload: AgentRunRequest, db: DbDep, current_user: AiUserDep):
    """发一轮消息。带 thread_id 就是接着聊，不带就新开一个会话。

    读工具会在这一轮里直接跑完；写工具会在这里停下（status=interrupt），
    等 `/agent/resume` 带确认回来。
    """
    if payload.thread_id:
        _owned_thread(db, payload.thread_id, current_user.id)
        thread_id = payload.thread_id
    else:
        thread_id = f"u{current_user.id}-{uuid.uuid4().hex}"
        _claim_thread(db, thread_id, current_user.id, payload.query)

    result = get_graph(db).invoke(
        {
            "messages": [{"role": "user", "content": payload.query}],
            "user_id": current_user.id,
            "tz_offset": payload.tz_offset,
            "turns": 0,
            "pending": None,
            "steps": [],
        },
        _config(thread_id),
    )
    return _from_invoke(thread_id, result)


@router.post("/resume", response_model=AgentStateResponse)
def resume_agent(payload: AgentResumeRequest, db: DbDep, current_user: AiUserDep):
    """批准或拒绝挂起的写操作，然后让对话继续跑下去。"""
    row = _owned_thread(db, payload.thread_id, current_user.id)
    graph = get_graph(db)
    snapshot = graph.get_state(_config(payload.thread_id))
    # 双保险：agent_threads 只是索引，真正的归属也在 state 里存了一份
    owner = (snapshot.values or {}).get("user_id")
    if owner is not None and owner != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not snapshot.next:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="该会话没有待确认的操作"
        )

    result = graph.invoke(
        Command(
            resume={
                "approve": payload.approve,
                "args": payload.args,
                "note": payload.note,
            }
        ),
        _config(payload.thread_id),
    )
    _touch(db, row)
    return _from_invoke(payload.thread_id, result)


@router.get("/threads", response_model=list[AgentThreadRead])
def list_threads(db: DbDep, current_user: UserDep):
    """侧栏会话列表，最近更新的在前。"""
    rows = db.scalars(
        select(AgentThread)
        .where(AgentThread.user_id == current_user.id)
        .order_by(AgentThread.updated_at.desc())
    ).all()
    return [AgentThreadRead.model_validate(r) for r in rows]


@router.get("/threads/{thread_id}", response_model=AgentStateResponse)
def get_thread_state(thread_id: str, db: DbDep, current_user: UserDep):
    """页面刷新 / 服务重启后恢复会话视图。"""
    row = _owned_thread(db, thread_id, current_user.id)
    snapshot = get_graph(db).get_state(_config(thread_id))
    values = snapshot.values or {}

    # 预置回放（demo）：没有 checkpoint，整份状态来自 agent_threads.replay。
    # 有 checkpoint 就以 checkpoint 为准 —— 回放只是"没跑过图的会话"的替身。
    if row.replay and not values:
        return AgentStateResponse(thread_id=thread_id, **row.replay)

    pending: list[PendingCall] = []
    for task in snapshot.tasks or ():
        for itr in task.interrupts or ():
            pending.extend(_pending_from_calls((itr.value or {}).get("calls", [])))

    messages = values.get("messages") or []
    return AgentStateResponse(
        thread_id=thread_id,
        status="interrupt" if pending else "answer",
        answer="" if pending else last_answer(messages),
        messages=_chat_messages(messages),
        steps=[AgentStep(**s) for s in (values.get("steps") or [])],
        pending=pending,
    )
