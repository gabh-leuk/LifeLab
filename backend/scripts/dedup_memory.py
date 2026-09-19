"""清理 memory_items 中已有的同 kind 近重复条目（可安全重复执行）。

规则与 store_memory 去重一致：
- 规范化文本完全相同：保留 id 最小者
- 向量相似度 ≥ settings.rag_dedup_threshold：保留 id 最小者

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\dedup_memory.py [--dry-run]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import engine
from app.models import MemoryItem


def _normalize(text_value: str) -> str:
    return "".join(text_value.split()).lower()


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    threshold = get_settings().rag_dedup_threshold

    with Session(engine) as db:
        dialect = db.get_bind().dialect.name
        if dialect != "postgresql":
            print("仅支持 PostgreSQL（向量去重）。")
            return

        items = db.scalars(
            select(MemoryItem).order_by(MemoryItem.id)
        ).all()
        print(f"检查 {len(items)} 条，去重阈值 {threshold}")

        removed: list[tuple[int, int, float]] = []  # (dup_id, kept_id, sim)
        kept_by_kind: dict[str, list[MemoryItem]] = {}
        text_seen: dict[str, dict[str, MemoryItem]] = {}

        for item in items:
            kind = item.kind
            kept_by_kind.setdefault(kind, [])
            text_seen.setdefault(kind, {})

            norm = _normalize(item.content or "")
            if norm in text_seen[kind]:
                removed.append((item.id, text_seen[kind][norm].id, 1.0))
                continue

            duplicate_of: MemoryItem | None = None
            if item.embedding:
                row = db.execute(
                    text(
                        "SELECT id, 1 - (embedding <=> CAST(:q AS halfvec(2560))) AS sim "
                        "FROM memory_items "
                        "WHERE id = ANY(:ids) AND embedding IS NOT NULL "
                        "ORDER BY embedding <=> CAST(:q AS halfvec(2560)) LIMIT 1"
                    ),
                    {
                        "q": item.embedding,
                        "ids": [k.id for k in kept_by_kind[kind]],
                    },
                ).first()
                if row is not None and float(row.sim) >= threshold:
                    match = next((k for k in kept_by_kind[kind] if k.id == row.id), None)
                    if match is not None:
                        duplicate_of = match

            if duplicate_of is not None:
                removed.append((item.id, duplicate_of.id, -1.0))
                continue

            kept_by_kind[kind].append(item)
            text_seen[kind][norm] = item

        print(f"发现重复 {len(removed)} 条：")
        for dup_id, kept_id, sim in removed:
            dup = next(i for i in items if i.id == dup_id)
            tag = "文本相同" if sim == 1.0 else f"相似度≥{threshold}"
            print(f"  #{dup_id} → 保留 #{kept_id}（{tag}）[{dup.kind}] {dup.content[:40]}")

        if dry_run:
            print("dry-run：未删除。")
            return
        for dup_id, _, _ in removed:
            db.execute(text("DELETE FROM memory_items WHERE id = :id"), {"id": dup_id})
        db.commit()
        print(f"已删除 {len(removed)} 条重复。剩余 {len(items) - len(removed)} 条。")


if __name__ == "__main__":
    main()
