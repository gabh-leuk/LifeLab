"""图状态。

**只放可 JSON 序列化的东西**：整个 state 每一步都会写进 Postgres checkpoint，
ORM 对象 / Session / datetime 会在写入那一刻炸。数据库会话走节点闭包
（见 `graph.py` 的 `get_graph`）。

`user_id` 必须在 state 里而不是只在闭包里：中断后恢复是**另一个请求**，
拿的是另一个 Session、另一个 `current_user`；不存下来，恢复时就不知道该
把发现写进谁名下。
"""

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    # OpenAI 线格式的消息（dict），不是 LangChain 对象：
    # 直接用 chat_with_tools 期望的形态，省一层转换，也天然可序列化。
    messages: Annotated[list[dict], operator.add]
    user_id: int
    # UTC 偏移分钟（东八区=480）。复盘按本地时区分天，恢复时是另一个请求，
    # 所以必须和 user_id 一样存进 state，不能只留在闭包里。
    tz_offset: int
    # 已用掉的 LLM 轮数，等价旧 MAX_STEPS
    turns: int
    # 本轮待审批的写调用（只在 tools → approval 之间非空）
    pending: list[dict] | None
    # 工具调用审计轨迹，回给前端做「步骤条」
    steps: Annotated[list[dict], operator.add]
