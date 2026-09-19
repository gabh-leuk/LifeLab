"""halfvec 类型：让 ORM 知道 embedding 列的真实 PG halfvec 类型。

本机 pgvector Python 包(0.5.0) 没有 HalfVector 类，故手写 TypeDecorator：
- 写入: 把 str "[0.1,0.2,...]" 原样绑定（PG 自动接受文本表示）
- 读取: 从 PG 拿回的 halfvec 是 str，原样返回

dbapi 绑定 str 会被 PG 按 unknown → halfvec 解析，避免 VARCHAR mismatch。
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


class HalfVector(TypeDecorator):
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String)

    def process_bind_param(self, value, dialect):
        # value 是 "[0.1,0.2,...]" 文本；原样传给 PG 由其解析为 halfvec
        return value

    def process_result_value(self, value, dialect):
        # PG 返回 halfvec 时可能是 bytes/其他；归一为 str
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)