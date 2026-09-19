"""LLM 客户端抽象：OpenAI 兼容接口（DeepSeek 等），可换供应商不改业务代码。

这里是全项目 LLM 流量的**唯一收口**，所以调用埋点也放在这 —— 每次调用追一行 JSONL
到 `~/.lifelab/llm_calls.jsonl`（路径可用 `LLM_TRACE_PATH` 覆盖，
`LLM_TRACE_ENABLED=false` 关掉）。`scripts/llm_usage.py` 读它出用量汇总。
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from app.config import get_settings

# 与 serve.cmd 的 server.log 同一口径：超 5MB 轮转为 .old
_TRACE_MAX_BYTES = 5 * 1024 * 1024


class LLMError(Exception):
    pass


def _trace_path() -> Path:
    configured = get_settings().llm_trace_path
    if configured:
        return Path(configured)
    return Path.home() / ".lifelab" / "llm_calls.jsonl"


def _record(
    caller: str,
    fn: str,
    model: str,
    started: float,
    usage,
    *,
    ok: bool,
    error: str | None = None,
) -> None:
    """追一行调用记录。**任何异常都吞掉** —— 埋点出问题绝不能连累主流程。"""
    try:
        if not get_settings().llm_trace_enabled:
            return
        path = _trace_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _TRACE_MAX_BYTES:
            path.replace(path.with_name(path.name + ".old"))
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "caller": caller or "unknown",
            "fn": fn,
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "ok": ok,
            "error": (error or "")[:300] or None,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001, S110 —— 埋点是旁路，静默失败是设计要求
        pass


def build_client() -> OpenAI:
    settings = get_settings()
    if not settings.llm_api_key:
        raise LLMError("LLM_API_KEY 未设置")
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def chat_json(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 3000,
    timeout: float | None = None,
    caller: str = "",
) -> dict:
    """调用 chat 并返回解析后的 JSON 对象（要求模型输出合法 JSON）。

    `timeout` 是**按次**超时（秒），默认 None = 用客户端默认值（SDK 约 600s）。
    带接口的同步调用要传 —— 否则供应商不响应时能把请求挂上十分钟。
    `caller` 只用于埋点归类（如 "review.daily"），不影响行为。
    """
    settings = get_settings()
    client = build_client()
    kwargs: dict = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    started = time.perf_counter()
    usage = None
    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=max_tokens,
            **kwargs,
        )
        usage = getattr(resp, "usage", None)
        content = resp.choices[0].message.content
        if not content:
            raise LLMError("LLM 返回空内容")
        data = json.loads(content)
    except Exception as e:  # 网络/限流/鉴权、空内容、非 JSON —— 都记一行 ok=false
        _record(
            caller, "chat_json", settings.llm_model, started, usage,
            ok=False, error=f"{type(e).__name__}: {e}",
        )
        if isinstance(e, LLMError):
            raise
        if isinstance(e, json.JSONDecodeError):
            raise LLMError(f"LLM 返回非 JSON: {e}") from e
        raise LLMError(f"LLM 调用失败: {e}") from e

    _record(caller, "chat_json", settings.llm_model, started, usage, ok=True)
    return data


def chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    max_tokens: int = 3000,
    caller: str = "",
) -> tuple[str | None, list[dict] | None]:
    """函数调用式对话（M4 Tool Calling）。返回 (文本内容, 模型请求的工具调用列表)。"""
    settings = get_settings()
    client = build_client()
    started = time.perf_counter()
    usage = None
    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=max_tokens,
        )
        usage = getattr(resp, "usage", None)
        msg = resp.choices[0].message
    except Exception as e:
        _record(
            caller, "chat_with_tools", settings.llm_model, started, usage,
            ok=False, error=f"{type(e).__name__}: {e}",
        )
        raise LLMError(f"LLM 调用失败: {e}") from e

    _record(caller, "chat_with_tools", settings.llm_model, started, usage, ok=True)

    content = msg.content

    if msg.tool_calls is None:
        return content, None

    tool_calls = []
    for tc in msg.tool_calls:
        tool_calls.append(
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments or "",
            }
        )
    return content, tool_calls
