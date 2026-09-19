from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# embedding 列在 PG 里是 halfvec(2560)（迁移创建），并建了
# USING hnsw (embedding halfvec_cosine_ops) 索引。
# 本机 pgvector Python 包(0.5.0)没有 HalfVector 类型类，故 ORM 侧用 Text 存
# halfvec 的文本表示（如 "[0.1,0.2,...]"）；写入/检索靠原生 SQL CAST 完成。
#
# SQLite 测试环境无 halfvec —— 存 Text 同样成立，检索退化为关键词（见 service）。
EMBEDDING_TYPE = Text  # PG 列已是 halfvec(2560)，ORM 不用管具体类型


class MemoryItem(Base):
    """语义记忆条目：可检索的历史信息片段（来自想法/事件/复盘/发现/问题）。

    embedding 存 PG halfvec(2560)（Text 表示），配 HNSW 索引；
    source_ref 记录来源（thought/event/... 的 id），删除源时级联清理。
    """

    __tablename__ = "memory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding: Mapped[str | None] = mapped_column(EMBEDDING_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )