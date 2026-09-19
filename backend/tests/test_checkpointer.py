"""checkpointer 按库型分流：Postgres 建池，非 Postgres 退回内存。

这条分界不是可有可无的洁癖：`database_url` 的默认值就是 `sqlite:///./lifelab.db`，
`.env` 一旦缺失（CI、首次 clone、从别的目录启动）就会用它。此时若照旧把 SQLite
串当 libpq 连接串喂给 psycopg，症状不是报错而是一句 `missing "="`，然后每个请求
在池子上吊满 30 秒才 PoolTimeout —— 与「配置错了」差着十万八千里。
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver

from app.agent import checkpointer as cp


@pytest.fixture(autouse=True)
def _clean_singleton():
    """单例是模块级全局；不隔离的话一条用例建出来的 saver 会漏给下一条。"""
    cp.reset_checkpointer()
    yield
    cp.reset_checkpointer()


def test_sqlite_url_degrades_to_memory(monkeypatch):
    monkeypatch.setattr(cp.get_settings(), "database_url", "sqlite:///./lifelab.db")

    def _boom(*a, **kw):
        raise AssertionError("SQLite 库不该去建 psycopg 连接池")

    monkeypatch.setattr(cp, "ConnectionPool", _boom)
    assert isinstance(cp.get_checkpointer(), InMemorySaver)


def test_postgres_url_builds_pool_and_strips_driver(monkeypatch):
    monkeypatch.setattr(
        cp.get_settings(), "database_url", "postgresql+psycopg://u:p@h:5432/lifelab"
    )
    seen = {}

    class _FakePool:
        def __init__(self, conninfo, **kw):
            seen["conninfo"] = conninfo

        def close(self):
            pass

    monkeypatch.setattr(cp, "ConnectionPool", _FakePool)
    assert isinstance(cp.get_checkpointer(), PostgresSaver)
    # libpq 不认 `+psycopg` 驱动后缀，必须在进池子前剥掉
    assert seen["conninfo"] == "postgresql://u:p@h:5432/lifelab"


def test_singleton_is_reused(monkeypatch):
    monkeypatch.setattr(cp.get_settings(), "database_url", "sqlite:///./lifelab.db")
    assert cp.get_checkpointer() is cp.get_checkpointer()
