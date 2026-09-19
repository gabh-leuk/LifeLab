from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentThread(Base):
    """Agent 会话索引：LangGraph checkpoint 的**归属登记**。

    对话状态本身存在 checkpoint 四表里（`thread_id` 只是那里的一把键）。
    本表的唯一职责是回答「这个 thread_id 是谁的」——不这样就得靠
    「uuid 猜不到」当授权，那是侥幸不是授权。顺带承载侧栏列表与标题。

    `replay` 是 demo 专用：AI 全封的账号跑不了图，于是给它一份**静态会话
    快照**（messages/steps/answer），`GET /agent/threads/{id}` 在没有
    checkpoint 时回放它。种子写入，前端只读，不参与图的执行。
    """

    __tablename__ = "agent_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    replay: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
