"""画像晋升：跨周/跨天复现的提炼产物 → 用户背景候选。

边界二（向量库 → 画像）的自动发现环节：
- 素材：kind=pattern（跨周复现才算）与 kind=insight（跨天复现才算）
- 方法：向量相似度聚类（并查集）；只把「同一主题在不同周/天独立出现」的簇
  作为候选，避免单周重复或链式误聚
- 排除：已被晋升过的簇（任何成员 ref 命中已有画像 source_ref）、
  与现有背景文本相同的簇
- 红线：只产出候选，不落库；用户在「用户背景」确认后才写入（source=ai）
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import LLMError, chat_json
from app.models.memory import MemoryItem
from app.models.profile import ProfileFact

logger = logging.getLogger(__name__)

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

REFINE_SYSTEM_PROMPT = """你是用户画像整理助手。下面是从用户行为数据中发现的
「跨周/跨天反复出现」的模式聚类。请把每一条改写成一条用户画像事实：

- category 只能取 identity（身份）/ habit（习惯）/ preference（偏好）/
  context（情境）/ constraint（限制）之一
- 一句话、无日期、不带编号、≤50 字，是脱离上下文也能成立的自我描述
- 原文有「(推测)」的必须保留该标注；禁止编造新材料
- 输出合法 JSON：{"facts": [{"index": 0, "category": "habit", "content": "..."}]}
仅以上字段。"""


@dataclass
class _Node:
    id: int
    kind: str
    content: str
    ref: str | None


@dataclass
class _Cluster:
    members: list[_Node]
    span_keys: list[str]
    pairs: dict[tuple[int, int], float] = field(default_factory=dict)


def _granularity(ref: str | None) -> tuple[str, str] | None:
    """source_ref → (粒度, 键)；如 ("week", "2026-W37") / ("day", "2026-09-07")。"""
    if not ref or ":" not in ref:
        return None
    _, key = ref.split(":", 1)
    if _WEEK_RE.match(key):
        return "week", key
    if _DAY_RE.match(key):
        return "day", key
    if _MONTH_RE.match(key):
        return "month", key
    return None


def _load_kind(
    db: Session, kind: str, *, user_id: int, limit: int = 300
) -> list[_Node]:
    rows = db.scalars(
        select(MemoryItem)
        .where(
            MemoryItem.user_id == user_id,
            MemoryItem.kind == kind,
        )
        .order_by(MemoryItem.id.desc())
        .limit(limit)
    ).all()
    return [_Node(id=r.id, kind=r.kind, content=r.content or "", ref=r.source_ref) for r in rows]


def _similar_pairs(
    db: Session, kind: str, threshold: float, user_id: int
) -> dict[tuple[int, int], float]:
    """同 kind 两两相似度 ≥ 阈值的边（PG 向量查询；其他方言返回空）。

    只取有向量的条目；n 很小（几十条），两两 SQL 可接受。
    """
    if db.get_bind().dialect.name != "postgresql":
        return {}
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT a.id AS aid, b.id AS bid, 1 - (a.embedding <=> b.embedding) AS sim "
            "FROM memory_items a "
            "JOIN memory_items b ON a.id < b.id "
            "WHERE a.kind = :k AND b.kind = :k AND a.user_id = :uid "
            "  AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL "
            "  AND 1 - (a.embedding <=> b.embedding) >= :t"
        ),
        {"k": kind, "uid": user_id, "t": threshold},
    ).all()
    return {(int(r.aid), int(r.bid)): float(r.sim) for r in rows}


def _cluster(nodes: list[_Node], pairs: dict[tuple[int, int], float]) -> list[_Cluster]:
    """并查集聚类；pairs 的键是 (小 id, 大 id)。"""
    parent: dict[int, int] = {n.id: n.id for n in nodes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        if a in parent and b in parent:
            union(a, b)

    groups: dict[int, list[_Node]] = defaultdict(list)
    for node in nodes:
        groups[find(node.id)].append(node)

    clusters: list[_Cluster] = []
    for members in groups.values():
        ids = {m.id for m in members}
        local_pairs = {k: v for k, v in pairs.items() if k[0] in ids and k[1] in ids}
        span_keys: list[str] = []
        for m in members:
            parsed = _granularity(m.ref)
            if parsed and parsed[1] not in span_keys:
                span_keys.append(parsed[1])
        clusters.append(_Cluster(members=members, span_keys=span_keys, pairs=local_pairs))
    return clusters


def _representative(cluster: _Cluster) -> _Node:
    """最中心的成员：到簇内其他成员平均相似度最高；无对则最靠后的成员。"""
    if not cluster.pairs:
        return cluster.members[-1]
    score: dict[int, list[float]] = defaultdict(list)
    for (a, b), sim in cluster.pairs.items():
        score[a].append(sim)
        score[b].append(sim)
    return max(
        cluster.members,
        key=lambda m: (
            sum(score.get(m.id, [0.0])) / max(len(score.get(m.id, [0.0])), 1),
            m.id,
        ),
    )


def _avg_sim(cluster: _Cluster) -> float:
    if not cluster.pairs:
        return 0.0
    return sum(cluster.pairs.values()) / len(cluster.pairs)


def _promoted_refs(db: Session, user_id: int) -> set[str]:
    rows = db.scalars(
        select(ProfileFact).where(ProfileFact.user_id == user_id)
    ).all()
    return {r.source_ref for r in rows if r.source_ref}


def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def _existing_fact_texts(db: Session, user_id: int) -> set[str]:
    rows = db.scalars(
        select(ProfileFact).where(
            ProfileFact.user_id == user_id,
            ProfileFact.status == "active",
        )
    ).all()
    return {_normalize(r.content) for r in rows}


def _build_candidates(
    db: Session,
    *,
    kind: str,
    span_granularity: str,
    threshold: float,
    min_spans: int,
    promoted_refs: set[str],
    fact_texts: set[str],
    user_id: int,
) -> list[dict]:
    """聚类 → 过滤 → 候选 dict 列表（未精炼）。"""
    nodes = _load_kind(db, kind, user_id=user_id)
    if len(nodes) < 2:
        return []
    clusters = _cluster(nodes, _similar_pairs(db, kind, threshold, user_id))

    candidates: list[dict] = []
    for cluster in clusters:
        spans = [
            m.ref
            for m in cluster.members
            if (_granularity(m.ref) or ("", ""))[0] == span_granularity
        ]
        distinct = sorted({r.split(":", 1)[1] for r in spans if r and ":" in r})
        if len(distinct) < min_spans:
            continue
        if any(m.ref in promoted_refs for m in cluster.members if m.ref):
            continue
        rep = _representative(cluster)
        if _normalize(rep.content) in fact_texts:
            continue
        avg = _avg_sim(cluster)
        confidence = round(
            min(0.95, 0.55 + 0.08 * (len(distinct) - min_spans) + 0.4 * max(0.0, avg - threshold)),
            2,
        )
        members = []
        for m in sorted(cluster.members, key=lambda x: x.id):
            sim = None
            key = (min(m.id, rep.id), max(m.id, rep.id))
            if m.id != rep.id and key in cluster.pairs:
                sim = round(cluster.pairs[key], 4)
            members.append(
                {
                    "kind": m.kind,
                    "ref": m.ref,
                    "content": m.content,
                    "similarity": sim,
                }
            )
        candidates.append(
            {
                "content": rep.content,
                "category": "habit",
                "confidence": confidence,
                "source_ref": rep.ref,
                "source_kind": kind,
                "spans": distinct,
                "avg_similarity": round(avg, 4),
                "members": members,
            }
        )

    candidates.sort(
        key=lambda c: (len(c["spans"]), c["avg_similarity"]), reverse=True
    )
    return candidates


def _refine(candidates: list[dict]) -> bool:
    """LLM 把候选改写为画像事实（分类 + ≤50字自足陈述）。失败返回 False。"""
    if not candidates:
        return False
    lines = []
    for i, c in enumerate(candidates):
        members = "\n".join(f"    - {m['content']}" for m in c["members"])
        lines.append(
            f"[{i}] 代表表述：{c['content']}\n  跨{len(c['spans'])}个期间："
            f"{', '.join(c['spans'])}\n  成员：\n{members}"
        )
    user_prompt = "候选列表：\n\n" + "\n\n".join(lines)
    try:
        raw = chat_json(
            REFINE_SYSTEM_PROMPT, user_prompt, max_tokens=1500, caller="profile.refine"
        )
    except LLMError:
        logger.warning("晋升候选精炼失败，返回原始表述", exc_info=True)
        return False

    facts = raw.get("facts")
    if not isinstance(facts, list):
        return False
    valid_categories = {"identity", "habit", "preference", "context", "constraint"}
    refined = 0
    for item in facts:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(candidates)):
            continue
        category = str(item.get("category") or "")
        content = str(item.get("content") or "").strip()
        if category in valid_categories and content:
            candidates[idx]["category"] = category
            candidates[idx]["content"] = content[:300]
            refined += 1
    return refined > 0


def scan_promotions(
    db: Session, *, refine: bool = True, user_id: int
) -> dict:
    """扫描跨周/跨天复现的模式与洞察，返回晋升候选（不落库）。"""
    settings = get_settings()
    promoted = _promoted_refs(db, user_id)
    fact_texts = _existing_fact_texts(db, user_id)

    pattern_candidates = _build_candidates(
        db,
        kind="pattern",
        span_granularity="week",
        threshold=settings.promotion_pattern_similarity,
        min_spans=2,
        promoted_refs=promoted,
        fact_texts=fact_texts,
        user_id=user_id,
    )
    insight_candidates = _build_candidates(
        db,
        kind="insight",
        span_granularity="day",
        threshold=settings.promotion_insight_similarity,
        min_spans=2,
        promoted_refs=promoted,
        fact_texts=fact_texts,
        user_id=user_id,
    )

    # 模式优先：与已选模式候选主题重复的洞察候选跳过（代表内容不同则保留）
    selected_pattern_texts = {_normalize(c["content"]) for c in pattern_candidates}
    insight_candidates = [
        c for c in insight_candidates if _normalize(c["content"]) not in selected_pattern_texts
    ]

    candidates = (pattern_candidates + insight_candidates)[
        : settings.promotion_max_candidates
    ]
    mode = "raw"
    if refine and candidates:
        mode = "refined" if _refine(candidates) else "raw"

    return {
        "candidates": candidates,
        "scanned_patterns": len(_load_kind(db, "pattern", user_id=user_id)),
        "scanned_insights": len(_load_kind(db, "insight", user_id=user_id)),
        "mode": mode,
    }
