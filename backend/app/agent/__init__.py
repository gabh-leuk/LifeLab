"""M6 有状态 Agent（LangGraph）。

- `checkpointer.py` Postgres checkpointer 单例 + DSN 转换
- `tools.py`       工具集：只读 / 写库两条通道
- `state.py`       图状态
- `graph.py`       状态图（只对写工具中断，等用户确认）

取代了原先 `app/services/agent_service.py` 那个无状态 ReAct 循环（已删）。
"""
