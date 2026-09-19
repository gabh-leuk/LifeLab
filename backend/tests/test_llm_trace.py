"""LLM 调用埋点（app/llm.py）的单元测试：不触网、不碰真实 ~/.lifelab 文件。"""

import json
from types import SimpleNamespace

import pytest

from app import llm as llm_mod
from app.llm import LLMError, chat_json, chat_with_tools


def _resp(content: str | None = "{}", usage=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _client(resp=None, exc: Exception | None = None):
    class _Completions:
        def create(self, **_kwargs):
            if exc is not None:
                raise exc
            return resp

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


def _lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def trace(tmp_path, monkeypatch):
    """把埋点落到本测试专属文件，并返回该路径。"""
    path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setattr(llm_mod, "_trace_path", lambda: path)
    return path


def _usage(prompt=10, completion=5):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                           total_tokens=prompt + completion)


def test_chat_json_records_success(monkeypatch, trace):
    monkeypatch.setattr(llm_mod, "build_client", lambda: _client(_resp('{"a": 1}', _usage())))

    assert chat_json("sys", "usr", caller="unit.test") == {"a": 1}

    rows = _lines(trace)
    assert len(rows) == 1
    row = rows[0]
    assert row["caller"] == "unit.test"
    assert row["fn"] == "chat_json"
    assert row["ok"] is True
    assert row["error"] is None
    assert row["prompt_tokens"] == 10 and row["completion_tokens"] == 5
    assert row["total_tokens"] == 15
    assert isinstance(row["latency_ms"], int | float)
    assert row["model"]


def test_chat_json_defaults_caller_to_unknown(monkeypatch, trace):
    monkeypatch.setattr(llm_mod, "build_client", lambda: _client(_resp("{}")))

    chat_json("sys", "usr")

    assert _lines(trace)[0]["caller"] == "unknown"


def test_chat_json_records_transport_failure(monkeypatch, trace):
    monkeypatch.setattr(llm_mod, "build_client", lambda: _client(exc=RuntimeError("boom")))

    with pytest.raises(LLMError):
        chat_json("sys", "usr", caller="unit.fail")

    row = _lines(trace)[0]
    assert row["caller"] == "unit.fail"
    assert row["ok"] is False
    assert "boom" in row["error"]


def test_chat_json_records_empty_content_as_failure(monkeypatch, trace):
    """空内容走的是 LLMError 分支：原始报错信息要原样抛出，不能被包成「调用失败」。"""
    monkeypatch.setattr(llm_mod, "build_client", lambda: _client(_resp(None)))

    with pytest.raises(LLMError, match="空内容"):
        chat_json("sys", "usr")

    row = _lines(trace)[0]
    assert row["ok"] is False
    assert "空内容" in row["error"]
    assert row["total_tokens"] is None  # 供应商没给 usage 时留空，不是 0


def test_chat_with_tools_records_and_parses(monkeypatch, trace):
    tool_calls = [
        SimpleNamespace(id="c1", function=SimpleNamespace(name="list_experiments", arguments="{}"))
    ]
    monkeypatch.setattr(
        llm_mod, "build_client", lambda: _client(_resp(None, _usage(), tool_calls))
    )

    content, parsed = chat_with_tools([{"role": "user", "content": "hi"}], [], caller="agent.turn")

    assert content is None
    assert parsed == [{"id": "c1", "name": "list_experiments", "arguments": "{}"}]
    row = _lines(trace)[0]
    assert row["fn"] == "chat_with_tools"
    assert row["caller"] == "agent.turn"
    assert row["ok"] is True


def test_trace_failure_never_breaks_the_call(monkeypatch, trace):
    """护栏：埋点自己炸了也不能影响调用结果 —— 它是旁路，不是主流程。"""
    monkeypatch.setattr(llm_mod, "build_client", lambda: _client(_resp('{"a": 1}')))

    def _boom():
        raise OSError("埋点文件不可写")

    monkeypatch.setattr(llm_mod, "_trace_path", _boom)

    assert chat_json("sys", "usr") == {"a": 1}


def test_trace_can_be_disabled(monkeypatch, trace):
    monkeypatch.setattr(llm_mod, "build_client", lambda: _client(_resp("{}")))
    monkeypatch.setattr(llm_mod, "get_settings", lambda: SimpleNamespace(
        llm_trace_enabled=False, llm_model="test-model", llm_trace_path=""
    ))

    chat_json("sys", "usr")

    assert not trace.exists()
