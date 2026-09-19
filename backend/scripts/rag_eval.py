"""RAG 检索质量评测：固定查询 + 断言，调参后可回归（需要演示数据）。

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\rag_eval.py

依赖 scripts/seed_demo.py 生成的数据 + 完成周/月提炼（见 docs/PROJECT_MAP.md）。
断言分三类：
- 正查询：期望命中关键词出现在 top-k，且 top-1 相似度不低于下限
- 负查询：过线数必须为 0（门槛拦截噪声）
- 约束：向量路径生效、候选去重后无近重复、时间权重参与计算
退出码：0 全部通过，1 有失败。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import engine
from app.models import MemoryItem
from app.services import memory_service

# (查询, 期望任一关键词出现在 top5 内容, raw 相似度下限)
POSITIVE: list[tuple[str, list[str], float]] = [
    ("我睡前刷手机的习惯改善了吗？", ["手机", "睡前", "物理隔离"], 0.70),
    ("最近的专注度有变化吗？", ["专注", "注意力", "休息"], 0.65),
    ("旅行那几天发生了什么？", ["旅行"], 0.55),
]

# 无关问题：过线数必须为 0
NEGATIVE: list[str] = [
    "今天中午吃什么？",
    "明天天气怎么样？",
]


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def main() -> int:
    failures: list[str] = []
    with Session(engine) as db:
        if not memory_service._is_pg(db):
            print("需要 PostgreSQL（向量路径）。")
            return 1
        total = len(db.scalars(select(MemoryItem)).all())
        print(f"库内 {total} 条记忆\n")

        print("── 正查询 ──────────────────────────────────────────")
        for query, keywords, min_top1 in POSITIVE:
            res = memory_service.search_debug(db, query, limit=200)
            hits = [h for h in res["hits"] if h.similarity >= res["threshold"]]
            top1 = hits[0].similarity if hits else 0.0
            contents = " ".join(h.item.content for h in hits[:5])
            hit_keyword = any(k in contents for k in keywords)
            ok = hit_keyword and top1 >= min_top1
            status = "PASS" if ok else "FAIL"
            print(
                f"[{status}] {query}\n"
                f"        过线 {len(hits)} 条，top1 sim={top1:.3f}（下限 {min_top1}）"
                f"，关键词 {'命中' if hit_keyword else '未命中'}"
            )
            if not ok:
                failures.append(f"正查询失败: {query}")

        print("\n── 负查询（应被门槛全部拦截） ──────────────────────")
        for query in NEGATIVE:
            res = memory_service.search_debug(db, query, limit=200)
            passed = [h for h in res["hits"] if h.similarity >= res["threshold"]]
            ok = len(passed) == 0
            max_sim = max((h.similarity for h in res["hits"]), default=0.0)
            print(
                f"[{'PASS' if ok else 'FAIL'}] {query}"
                f"  全量最高 sim={max_sim:.3f}，过线 {len(passed)}（应为 0）"
            )
            if not ok:
                failures.append(f"负查询失败: {query}")

        print("\n── 约束检查 ────────────────────────────────────────")
        res = memory_service.search_debug(db, POSITIVE[0][0], limit=200)
        mode_ok = res["mode"] == "vector"
        print(f"[{'PASS' if mode_ok else 'FAIL'}] 走向量路径（{res['mode']}）")
        if not mode_ok:
            failures.append("向量路径未生效")

        weights_ok = res["time_weight"] > 0
        print(f"[{'PASS' if weights_ok else 'FAIL'}] 时间权重已启用（{res['time_weight']}）")
        if not weights_ok:
            failures.append("时间权重为 0")

        top5 = [h for h in res["hits"] if h.similarity >= res["threshold"]][:5]
        norm = [_normalize(h.item.content) for h in top5]
        dup_ok = len(norm) == len(set(norm))
        print(f"[{'PASS' if dup_ok else 'FAIL'}] top5 内无文本重复")
        if not dup_ok:
            failures.append("top5 存在重复条目")

        # 全库同 kind 近重复（规范化文本）应为 0
        items = db.scalars(select(MemoryItem)).all()
        seen: set[tuple[str, str]] = set()
        global_dups = 0
        for item in items:
            key = (item.kind, _normalize(item.content or ""))
            if key in seen:
                global_dups += 1
            seen.add(key)
        print(f"[{'PASS' if global_dups == 0 else 'FAIL'}] 全库无同 kind 文本重复（发现 {global_dups}）")
        if global_dups:
            failures.append("全库仍有重复")

    print()
    if failures:
        print(f"结果：{len(failures)} 项失败")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
