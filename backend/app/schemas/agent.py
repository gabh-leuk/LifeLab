from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentStep(BaseModel):
    """一次工具调用（前端渲染成「步骤条」）。"""

    tool: str
    args: dict
    result: dict
    # 写工具才有意义：None = 只读工具，True/False = 用户批没批
    approved: bool | None = None


class PendingCall(BaseModel):
    """等待用户确认的写操作。"""

    id: str
    name: str
    args: dict
    summary: str


class AgentMessage(BaseModel):
    """给前端看的对话消息（tool 消息不进这里，它们由 steps 承载）。"""

    role: Literal["user", "assistant"]
    content: str


class AgentRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # 不给就新开一个会话
    thread_id: str | None = Field(default=None, max_length=64)
    # UTC 偏移分钟（东八区=480）。复盘按本地时区分天，缺省 0 = UTC
    tz_offset: int = Field(default=0, ge=-840, le=840)


class AgentResumeRequest(BaseModel):
    thread_id: str = Field(max_length=64)
    approve: bool
    # 用户可在审批卡里改参数；服务端落库前会重走工具自己的校验
    args: dict | None = None
    note: str | None = Field(default=None, max_length=500)


class AgentStateResponse(BaseModel):
    """会话当前状态。run / resume / 读状态三个端点返回同一形态。"""

    thread_id: str
    # interrupt = 停下来等确认；answer = 这一轮说完了
    status: Literal["answer", "interrupt"]
    answer: str = ""
    messages: list[AgentMessage] = []
    steps: list[AgentStep] = []
    pending: list[PendingCall] = []


class AgentThreadRead(BaseModel):
    """侧栏会话列表项。"""

    model_config = ConfigDict(from_attributes=True)

    thread_id: str
    title: str
    created_at: datetime
    updated_at: datetime
