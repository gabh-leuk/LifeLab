"""Postgres checkpointer 的接入点（单例）。

**为什么 DSN 要转一次**：`settings.database_url` 是 SQLAlchemy 形态
（`postgresql+psycopg://…`），而 psycopg / libpq 不认 `+psycopg` 驱动后缀。
转换只在这里做，不去改 `database_url` 本身 —— SQLAlchemy 侧要那个后缀。

**为什么 checkpointer 是单例**：它持有连接池；每请求新建会随请求数泄漏连接。
但图本身**每请求重建**（节点闭包要拿请求作用域的 Session），见 `graph.py`。

**为什么非 Postgres 要回落内存**：`database_url` 的默认值就是
`sqlite:///./lifelab.db` —— `.env` 缺失、拼错、或（config.py 里写过的那个坑）
服务从别的目录起来，都会回落成它。此时若照旧把它当 libpq 连接串喂给 psycopg，
报的不是一句人话，而是池子背后反复重连、把每个请求吊满 30 秒再 PoolTimeout。
项目本来就支持 SQLite 起步（见 README），所以这里显式降级：连不上 Postgres
就没有 checkpointer，读会话退回进程内存 —— 但**其余数据照常落进 SQLite**。
"""

from __future__ import annotations

import logging
import threading

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

logger = logging.getLogger(__name__)


def libpq_dsn() -> str:
    """SQLAlchemy URL → libpq URI（剥掉 `+psycopg`）。"""
    return get_settings().database_url.replace("+psycopg", "")


def is_postgres() -> bool:
    """当前库是不是 Postgres —— 决定 checkpointer 能不能用。"""
    return get_settings().database_url.startswith(("postgresql", "postgres"))


def setup_checkpoint_tables() -> None:
    """建 checkpoint 四表（幂等），由 alembic 迁移调用。

    用库自己的 `setup()` 而不是手抄 DDL：这四张表的 schema 与索引由 langgraph
    版本决定，手抄一份等于把它冻在抄写那一刻。`setup()` 顶部那条
    `CREATE INDEX CONCURRENTLY` 不能在事务里跑，所以调用方必须先
    `autocommit_block()` 逃出 alembic 的事务（见对应迁移）。
    """
    with PostgresSaver.from_conn_string(libpq_dsn()) as saver:
        saver.setup()


_saver: PostgresSaver | InMemorySaver | None = None
_lock = threading.Lock()


def get_checkpointer() -> PostgresSaver | InMemorySaver:
    """进程内单例。首次调用时建池，之后复用（非 Postgres 则退回内存）。"""
    global _saver
    if _saver is None:
        with _lock:
            if _saver is None:
                if not is_postgres():
                    # 只警告一次（单例只建一次），不刷屏
                    logger.warning(
                        "database_url 不是 Postgres（%s）—— Agent 会话退回进程内存，"
                        "重启即丢；其余数据不受影响。",
                        get_settings().database_url,
                    )
                    _saver = InMemorySaver()
                    return _saver
                pool = ConnectionPool(
                    conninfo=libpq_dsn(),
                    min_size=1,
                    max_size=8,
                    # 与 PostgresSaver.from_conn_string 保持同一组 kwargs：
                    # 库内部靠 autocommit 逐条提交 checkpoint 写入。
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": 0,
                        "row_factory": dict_row,
                    },
                    open=True,
                )
                _saver = PostgresSaver(pool)
    return _saver


def reset_checkpointer() -> None:
    """丢掉单例（下次 get 会重建）；Postgres 那支顺带关掉连接池。

    测试用得到它（换一个干净的内存 checkpointer），脚本收尾也用它 ——
    不关池的话解释器退出时 `psycopg_pool` 的 `__del__` 会往 stderr 吐一串
    `PythonFinalizationError`，把真正的输出盖掉。
    """
    global _saver
    with _lock:
        if isinstance(_saver, PostgresSaver):
            pool = getattr(_saver, "conn", None)
            if isinstance(pool, ConnectionPool):
                pool.close()
        _saver = None
