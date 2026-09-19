"""语义记忆服务：写入带向量条目 + 向量相似度检索。

向量存储走 PG 原生 halfvec(2560)（迁移已建 + HNSW 索引）。SQLite 测试
环境无向量能力，search 退化为关键词匹配，保证测试可跑。

说明：本机 pgvector Python 包(0.5.0) 没有 HalfVector 类型类，因此
ORM 的 embedding 列用 Text 存 halfvec 文本（"[0.1,0.2,...]"）；
检索用原生 SQL `embedding <=> CAST(:q AS halfvec(2560))` 做余弦距离。
注意必须用 CAST(:q AS ...) 而不是 :q::halfvec —— SQLAlchemy 的
text() 会把紧跟冒号的 :q 当成 PG 的 :: 转型语法而不识别为绑定参数，
导致 SQL 直接报语法错误（历史 bug：向量检索静默退化成关键词匹配）。

检索排序：raw cosine（语义相关） + 时间衰减（越久越轻）的加权和，
并按 kind 设不同半衰期；低于相似度门槛的候选直接丢弃（宁缺毋滥）。

记忆三层（个人经历）：
- L0 原始记录：thought/event/state 各表（不上向量，避免流水账噪声）
- L1 洞察：extract_insights 从单日记录提炼 → kind="insight"
- L2 模式：extract_patterns 从单周 insight/finding 中提炼 → kind="pattern"
"""

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import LLMError, chat_json
from app.models.event import Event
from app.models.finding import Finding
from app.models.memory import MemoryItem
from app.models.problem import Problem
from app.models.review import DailyReview
from app.models.thought import Thought
from app.services import profile_service
from app.services.embedding import embed
from app.utils import parse_day_range

logger = logging.getLogger(__name__)


# 时间衰半衰期（天）：按内容类型区分，结论/模式衰减慢，单日叙事衰减快
HALF_LIFE_DAYS: dict[str, float] = {
    "review": 45,
    "insight": 90,
    "pattern": 180,
    "finding": 365,
}
HALF_LIFE_GRANULAR: dict[str, float] = {
    "day": 0,      # 占位，day 用 HALF_LIFE_DAYS["insight"]
    "week": 150,   # 周/月提炼产物更稳定，衰减更慢
    "month": 150,
}
DEFAULT_HALF_LIFE_DAYS = 120.0

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True)
class MemoryHit:
    """一条检索结果：raw 相似度 + 时间权重后的最终分（排序依据）。"""

    item: MemoryItem
    similarity: float  # 原始余弦相似度（fallback 时为关键词命中率）
    final_score: float
    anchor_date: date_cls | None
    recency: float = 1.0  # 时间新鲜度 0-1（调试展示用）
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS


# 提炼素材来源：给 extract_insights 聚合当天文本用（原始记录 + 已有结论）
# 注意：素材 ≠ 索引，原始记录不会因此进入向量库（见 _SYNC_SOURCES）
_MATERIAL_SOURCES: list[tuple[str, type, str]] = [
    ("thought", Thought, "content"),
    ("event", Event, "note"),
    ("review", DailyReview, "review_text"),
    ("finding", Finding, "observation"),
    ("problem", Problem, "title"),
]

# 向量库同步白名单（边界一：只收提炼后的总结，不收原始流水）
# insight/pattern 由提炼管道写入（replace_by_ref），不走 sync。
_SYNC_SOURCES: list[tuple[str, type, str]] = [
    ("review", DailyReview, "review_text"),
    ("finding", Finding, "observation"),
]


def _sync_content(kind: str, row: object, field: str) -> str:
    """同步进向量库的文本：finding 用组合文本（标题+观察+推测），其余取主字段。"""
    if kind == "finding":
        title = getattr(row, "title", "") or ""
        observation = getattr(row, "observation", "") or ""
        interpretation = getattr(row, "interpretation", "") or ""
        text = f"{title}：{observation}".strip("：") if title else observation
        if interpretation:
            text = f"{text}（推测：{interpretation}）"
        return text.strip()
    return (getattr(row, field) or "").strip()


def sync_from_sources(db: Session, user_id: int) -> int:
    """把已提炼产物（复盘/发现）同步为记忆索引（幂等）。

    边界一：只同步 _SYNC_SOURCES（review/finding）；
    thought/event/state/problem 是原始数据或结构化实体，永不进向量库。
    """
    added = 0
    for kind, model, field in _SYNC_SOURCES:
        rows = db.scalars(
            select(model).where(model.user_id == user_id).order_by(model.id)
        ).all()
        for row in rows:
            source_ref = f"{kind}:{row.id}"
            exists = db.scalar(
                select(MemoryItem).where(
                    MemoryItem.user_id == user_id,
                    MemoryItem.source_ref == source_ref,
                )
            )
            if exists:
                continue
            content = _sync_content(kind, row, field)
            if not content:
                continue
            store_memory(
                db,
                kind=kind,
                content=content,
                source_ref=source_ref,
                embed_vectors=True,
                user_id=user_id,
            )
            added += 1
    return added


ASK_SYSTEM_PROMPT = """你是 LifeLab 的个人记忆助手。用户会问你一个关于自己历史的问题，
以下是检索到的相关记忆片段（真实记录，可能有噪声）。

规则：
1. 只基于提供的记忆片段与用户背景回答；没有相关信息就如实说"没有找到相关记忆"。
2. 区分事实与推测：推测必须标注「(推测)」。
3. 回答简洁（<300字）、直接、可读，用中文。
4. 若片段相互矛盾，指出矛盾之处，不强行调和。
5. 输出必须是合法 JSON：{"answer": "你的回答"}。仅此一个字段。
6. 若提供了「关于用户的长期背景」，那是用户确认过的既定事实，可直接采信；
   当背景与记忆片段冲突时，指出冲突并说明哪边更新。"""


def search_debug(
    db: Session,
    query: str,
    *,
    kind: str | None = None,
    limit: int = 50,
    min_similarity: float | None = None,
    time_weight: float | None = None,
    user_id: int,
) -> dict:
    """检索诊断：返回全部候选 + 打分明细（不做截断），供可视化调参。

    返回 {query, threshold, time_weight, mode, hits: [MemoryHit...]}。
    mode = "vector" | "keyword" 标注本次走的是哪条路径。
    """
    settings = get_settings()
    threshold = settings.rag_min_similarity if min_similarity is None else min_similarity
    weight = settings.rag_time_weight if time_weight is None else time_weight

    raw: list[tuple[MemoryItem, float]] = []
    mode = "keyword"
    if _is_pg(db):
        try:
            raw = _vector_hits(db, query, kind=kind, limit=limit, user_id=user_id)
            mode = "vector"
        except Exception:
            db.rollback()
            logger.warning("debug 向量检索失败，回退关键词", exc_info=True)
    if mode == "keyword":
        tokens = _tokenize(query)
        if not tokens:
            return {
                "query": query,
                "threshold": threshold,
                "time_weight": weight,
                "mode": mode,
                "hits": [],
            }
        conds = [MemoryItem.content.ilike(f"%{t}%") for t in tokens]
        stmt = select(MemoryItem).where(
            MemoryItem.user_id == user_id, or_(*conds)
        )
        if kind:
            stmt = stmt.where(MemoryItem.kind == kind)
        raw = [
            (it, sum(1 for t in tokens if t in (it.content or "")) / len(tokens))
            for it in db.scalars(stmt).all()
        ]

    hits = rank_hits(raw, top_k=limit, time_weight=weight, min_similarity=None)
    return {
        "query": query,
        "threshold": threshold,
        "time_weight": weight,
        "mode": mode,
        "hits": hits,
    }


def memory_stats(db: Session, user_id: int) -> dict:
    """记忆库可视化统计：条目/向量覆盖 + 各来源表体量。"""
    rows = db.execute(
        text(
            "SELECT kind, count(*) AS total, "
            "count(embedding) AS embedded "
            "FROM memory_items WHERE user_id = :uid GROUP BY kind ORDER BY kind"
        ),
        {"uid": user_id},
    ).all()
    by_kind = [
        {"kind": r.kind, "count": int(r.total), "embedded": int(r.embedded)}
        for r in rows
    ]
    source_counts = {}
    for name, table in (
        ("events", "events"),
        ("thoughts", "thoughts"),
        ("states", "state_records"),
        ("reviews", "daily_reviews"),
        ("period_reviews", "period_reviews"),
        ("findings", "findings"),
        ("problems", "problems"),
        ("profile_facts", "profile_facts"),
    ):
        source_counts[name] = int(
            db.execute(
                text(f"SELECT count(*) FROM {table} WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar_one()
        )
    total = sum(k["count"] for k in by_kind)
    embedded = sum(k["embedded"] for k in by_kind)
    return {
        "total": total,
        "embedded": embedded,
        "by_kind": by_kind,
        "source_counts": source_counts,
    }


def ask_memory(
    db: Session,
    question: str,
    *,
    top_k: int = 5,
    kind: str | None = None,
    user_id: int,
) -> tuple[str, list[MemoryHit]]:
    """记忆问答：检索 → LLM 归纳 → 返回 (answer, 来源列表)。

    来源列表 = MemoryHit（含 raw 相似度/最终分/锚点日期），可追溯。
    若检索为空或 LLM 失效，answer 会说明情况，不影响返回。
    """
    hits = search_memory(db, question, top_k=top_k, kind=kind, user_id=user_id)
    background = profile_service.prompt_context(db, user_id)
    if not hits and not background:
        return ("没有找到相关的历史记忆。", [])

    fragments = []
    for hit in hits:
        it = hit.item
        header = f"- [{it.kind}] "
        if it.source_ref:
            header += f"(来源:{it.source_ref}) "
        header += f"(相关度:{hit.similarity:.2f}) "
        fragments.append(header + (it.content or ""))
    if not fragments:
        fragments.append("（本次没有检索到相关的历史记忆片段，请仅依据长期背景回答）")

    user_prompt = (
        f"问题：{question}\n\n"
        f"检索到的相关记忆片段：\n" + "\n".join(fragments)
    )

    system_prompt = ASK_SYSTEM_PROMPT
    if background:
        # 边界二：画像层只读注入（常驻背景与召回片段分开标注）
        system_prompt = f"{system_prompt}\n\n{background}"

    answer = ""
    sources: list[MemoryHit] = list(hits)
    try:
        raw = chat_json(system_prompt, user_prompt, caller="memory.ask")
        answer = raw.get("answer") or ""
    except LLMError as e:
        answer = f"(记忆检索成功，但 LLM 归纳失败：{e})"
    if not answer.strip():
        answer = "(未能生成回答)"

    return answer, sources


def _is_pg(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _vec_to_halfvec_text(vec: list[float]) -> str:
    """list[float] → halfvec 文本表示 "[0.1,0.2,...]"（存进 Text 列）。"""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _nearest_same_kind(
    db: Session, kind: str, emb_text: str, user_id: int
) -> tuple[int, float] | None:
    """找同 kind 中与给定向量最相似的条目（PG 向量查询）；无则 None。"""
    if not _is_pg(db):
        return None
    row = db.execute(
        text(
            "SELECT id, 1 - (embedding <=> CAST(:q AS halfvec(2560))) AS sim "
            "FROM memory_items "
            "WHERE user_id = :uid AND kind = :kind AND embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:q AS halfvec(2560)) LIMIT 1"
        ),
        {"q": emb_text, "uid": user_id, "kind": kind},
    ).first()
    if row is None:
        return None
    return int(row.id), float(row.sim)


def _find_duplicate(
    db: Session, kind: str, content: str, emb_text: str | None, user_id: int
) -> MemoryItem | None:
    """同 kind 去重：规范化文本完全相同，或向量相似度 ≥ 阈值。

    返回已存在的条目（调用方跳过写入），没有则 None。
    无向量时只做文本比较；阈值可经 settings.rag_dedup_threshold 调整。
    """
    norm = _normalize_text(content)
    existing = db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id, MemoryItem.kind == kind
        )
    ).all()
    for item in existing:
        if _normalize_text(item.content or "") == norm:
            return item

    if emb_text is None:
        return None
    threshold = get_settings().rag_dedup_threshold
    found = _nearest_same_kind(db, kind, emb_text, user_id)
    if found is not None:
        item_id, sim = found
        if sim >= threshold:
            dup = db.scalars(
                select(MemoryItem).where(
                    MemoryItem.id == item_id, MemoryItem.user_id == user_id
                )
            ).first()
            if dup is not None:
                logger.info(
                    "记忆去重：跳过与 #%s 相似度 %.3f 的条目 (%s)", item_id, sim, kind
                )
            return dup
    return None


def _normalize_text(text: str) -> str:
    return "".join(text.split()).lower()


def store_memory(
    db: Session,
    *,
    kind: str,
    content: str,
    source_ref: str | None = None,
    embed_vectors: bool = True,
    dedup: bool = True,
    user_id: int,
) -> MemoryItem | None:
    """写入一条记忆（可选去重）。embed 失败不阻塞写入（留空向量，仅存文本）。

    PG 下 embedding 列是 halfvec(2560)，ORM 直接赋值会按 VARCHAR 绑定导致
    DatatypeMismatch，故用原生 SQL 显式 ::halfvec(2560) 插入。

    dedup=True：同 kind 中已存在规范化文本相同或向量相似度 ≥
    settings.rag_dedup_threshold 的条目时跳过写入并返回已存在条目。
    """
    emb_text: str | None = None
    vec: list[float] | None = None
    if embed_vectors and _is_pg(db):
        try:
            vec = embed(content)
            emb_text = _vec_to_halfvec_text(vec)
        except Exception:  # noqa: BLE001 - embed 可能抛任意库异常，兜底仅存文本
            logger.warning("memory embed 失败，仅存文本: kind=%s", kind)
            emb_text = None

    if dedup and content.strip():
        dup = _find_duplicate(db, kind, content, emb_text, user_id)
        if dup is not None:
            return dup

    if embed_vectors and _is_pg(db):
        db.execute(
            text(
                "INSERT INTO memory_items (user_id, kind, content, source_ref, embedding, created_at) "
                "VALUES (:uid, :kind, :content, :source_ref, "
                + (
                    "NULL"
                    if emb_text is None
                    else f"'{emb_text}'::halfvec(2560)"
                )
                + ", now()) RETURNING id"
            ),
            {
                "uid": user_id,
                "kind": kind,
                "content": content,
                "source_ref": source_ref,
            },
        ).scalar_one()
        db.commit()
        item = db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                MemoryItem.source_ref == source_ref,
                MemoryItem.content == content,
            )
            .order_by(MemoryItem.id.desc())
            .limit(1)
        ).first()
        return item

    item = MemoryItem(
        user_id=user_id,
        kind=kind,
        content=content,
        source_ref=source_ref,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _anchor_date(item: MemoryItem) -> date_cls:
    """内容锚点日期：优先 source_ref（提炼产物的内容日期），否则行创建日期。"""
    return _anchor_of(item.source_ref, item.created_at)


def _anchor_of(source_ref: str | None, created_at: datetime | None) -> date_cls:
    """内容锚点日期：优先 source_ref（提炼产物的内容日期），否则行创建日期。

    用内容日期而非 created_at 做时间权重，重提炼/重新生成不会让旧内容诈尸变新。
    """
    ref = source_ref or ""
    if ":" in ref:
        kind, key = ref.split(":", 1)
        if kind in ("insight", "pattern"):
            try:
                if _DATE_RE.match(key):
                    return date_cls.fromisoformat(key)
                m = _WEEK_RE.match(key)
                if m:
                    return date_cls.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
                m = _MONTH_RE.match(key)
                if m:
                    return date_cls(int(m.group(1)), int(m.group(2)), 1)
            except ValueError:
                pass
    if created_at is not None:
        return (
            created_at.date()
            if hasattr(created_at, "date")
            else date_cls.fromisoformat(str(created_at)[:10])
        )
    return datetime.now(timezone.utc).date()


def _ref_granularity(item: MemoryItem) -> str:
    ref = item.source_ref or ""
    if ":" in ref:
        _, key = ref.split(":", 1)
        if _DATE_RE.match(key):
            return "day"
        if _WEEK_RE.match(key):
            return "week"
        if _MONTH_RE.match(key):
            return "month"
    return "day"


def _half_life_days(item: MemoryItem) -> float:
    if item.kind == "insight":
        return HALF_LIFE_GRANULAR.get(_ref_granularity(item)) or HALF_LIFE_DAYS["insight"]
    return HALF_LIFE_DAYS.get(item.kind, DEFAULT_HALF_LIFE_DAYS)


def _recency(anchor: date_cls, today: date_cls, half_life_days: float) -> float:
    age_days = max(0, (today - anchor).days)
    return math.pow(0.5, age_days / max(half_life_days, 1.0))


def rank_hits(
    raw: list[tuple[MemoryItem, float]],
    *,
    top_k: int,
    time_weight: float,
    min_similarity: float | None = None,
    today: date_cls | None = None,
) -> list[MemoryHit]:
    """纯函数：门槛过滤 + 时间权重 + 排序截断。

    final = (1 - w) * similarity + w * recency，加法混合保证
    "半年前已验证过的高相关结论"不会被新内容埋掉（乘法会）。
    """
    today = today or datetime.now(timezone.utc).date()
    w = min(max(time_weight, 0.0), 1.0)
    hits: list[MemoryHit] = []
    for item, sim in raw:
        if min_similarity is not None and sim < min_similarity:
            continue
        anchor = _anchor_date(item)
        half_life = _half_life_days(item)
        rec = _recency(anchor, today, half_life)
        final = (1.0 - w) * sim + w * rec
        hits.append(
            MemoryHit(
                item=item,
                similarity=sim,
                final_score=final,
                anchor_date=anchor,
                recency=rec,
                half_life_days=half_life,
            )
        )
    hits.sort(key=lambda h: h.final_score, reverse=True)
    return hits[:top_k]


def _vector_hits(
    db: Session,
    query: str,
    *,
    kind: str | None,
    limit: int,
    user_id: int,
    ids: set[int] | None = None,
) -> list[tuple[MemoryItem, float]]:
    """PG 向量检索：SQL 下推 ORDER BY 距离 + LIMIT，只查有向量的行。

    历史 bug 修复点：绑定参数必须用 CAST(:q AS halfvec(2560))，
    :q::halfvec 会被 SQLAlchemy 当成转型语法而不识别参数 → 语法错误。

    ids 不为 None 时（空集直接返回），候选池硬限定在这批条目上：期间过滤
    必须发生在 SQL 的 LIMIT **之前** —— 先取全局 top N 再剔除本期，本期条目
    会把候选窗口占满，更早期间的相关记忆根本进不了池子。
    """
    if ids is not None and not ids:
        return []
    q = embed(query)
    q_text = _vec_to_halfvec_text(q)
    kind_clause = " AND kind = :kind" if kind else ""
    pool_clause = " AND id = ANY(:ids)" if ids is not None else ""
    params: dict = {"q": q_text, "uid": user_id, "lim": limit}
    if kind:
        params["kind"] = kind
    if ids is not None:
        params["ids"] = list(ids)
    rows = db.execute(
        text(
            "SELECT id, 1 - (embedding <=> CAST(:q AS halfvec(2560))) AS sim "
            "FROM memory_items "
            "WHERE user_id = :uid AND embedding IS NOT NULL"
            + kind_clause
            + pool_clause
            + " ORDER BY embedding <=> CAST(:q AS halfvec(2560)) "
            "LIMIT :lim"
        ),
        params,
    ).all()
    if not rows:
        return []
    sim_map = {r.id: float(r.sim) for r in rows}
    items = db.scalars(
        select(MemoryItem).where(
            MemoryItem.id.in_(list(sim_map)), MemoryItem.user_id == user_id
        )
    ).all()
    by_id = {i.id: i for i in items}
    return [(by_id[r.id], sim_map[r.id]) for r in rows if r.id in by_id]


def _pool_ids(
    db: Session,
    *,
    user_id: int,
    after: date_cls | None,
    before: date_cls | None,
) -> set[int] | None:
    """按内容日期圈定候选池的条目 id；两界都不给时返回 None（＝不限定）。

    只取 id/source_ref/created_at 三列，不加载 embedding 文本（那是几十 KB/行）。
    """
    if after is None and before is None:
        return None
    rows = db.execute(
        select(MemoryItem.id, MemoryItem.source_ref, MemoryItem.created_at).where(
            MemoryItem.user_id == user_id
        )
    ).all()
    ids: set[int] = set()
    for row in rows:
        anchor = _anchor_of(row.source_ref, row.created_at)
        if before is not None and anchor >= before:
            continue
        if after is not None and anchor < after:
            continue
        ids.add(row.id)
    return ids


def search_memory(
    db: Session,
    query: str,
    *,
    top_k: int = 5,
    kind: str | None = None,
    min_similarity: float | None = None,
    time_weight: float | None = None,
    after: date_cls | None = None,
    before: date_cls | None = None,
    candidate_limit: int | None = None,
    user_id: int,
) -> list[MemoryHit]:
    """按语义检索记忆，返回 MemoryHit 列表（含 raw 相似度与最终分）。

    - PG：向量检索（SQL 下推 + HNSW），候选取 top_k*multiplier，
      按门槛过滤后用「相似度 + 时间衰减」排序截断
    - SQLite / 无向量行：关键词 2-gram 兜底（不套用余弦门槛）

    after/before（按内容日期，before 开区间）把候选池限定在某个期间内，
    且在候选阶段就生效 —— 供「只召回比本期更早的记忆」这类跨期场景使用。
    三个新参数默认 None，行为与不传时逐字一致，其余调用方不受影响。
    candidate_limit：覆盖 rag_max_candidates（按期间分桶时要放开窗口取全池）。
    """
    settings = get_settings()
    if min_similarity is None:
        min_similarity = settings.rag_min_similarity
    if time_weight is None:
        time_weight = settings.rag_time_weight

    cap = settings.rag_max_candidates if candidate_limit is None else candidate_limit
    candidates = min(cap, max(top_k * settings.rag_candidate_multiplier, top_k))

    pool = _pool_ids(db, user_id=user_id, after=after, before=before)
    if pool is not None and not pool:
        return []

    if _is_pg(db):
        try:
            raw = _vector_hits(
                db, query, kind=kind, limit=candidates, user_id=user_id, ids=pool
            )
            return rank_hits(
                raw,
                top_k=top_k,
                time_weight=time_weight,
                min_similarity=min_similarity,
            )
        except Exception:
            db.rollback()  # 事务出错先回滚，否则后续查询报 aborted
            logger.warning("vector 检索失败，回退关键词匹配", exc_info=True)

    # 非 PG / 向量检索失败：分词匹配兜底
    # 中文没有空格分词 → 用 2-gram（连续两字）作 token，显著提高命中率
    tokens = _tokenize(query)
    if not tokens:
        return []
    conds = [MemoryItem.content.ilike(f"%{t}%") for t in tokens]
    stmt_all = (
        select(MemoryItem)
        .where(MemoryItem.user_id == user_id, or_(*conds))
        .order_by(MemoryItem.created_at.desc())
    )
    if kind:
        stmt_all = stmt_all.where(MemoryItem.kind == kind)
    if pool is not None:
        stmt_all = stmt_all.where(MemoryItem.id.in_(pool))
    raw = [
        (it, sum(1 for t in tokens if t in (it.content or "")) / len(tokens))
        for it in db.scalars(stmt_all).all()
    ]
    # fallback 命中率与余弦不同量纲：不套余弦门槛，但保留时间权重参与排序
    return rank_hits(raw, top_k=top_k, time_weight=time_weight, min_similarity=None)


def _tokenize(text: str) -> list[str]:
    """中文/混合文本分词：空白分词 + 2-gram。"""
    parts = [p for p in text.replace("，", " ").replace("。", " ").split(" ") if p]
    tokens = set(parts)
    for p in parts:
        if len(p) >= 2:
            tokens.update(p[i : i + 2] for i in range(len(p) - 1))
    return list(tokens)


def list_memories(
    db: Session,
    *,
    limit: int = 50,
    kind: str | None = None,
    source_ref: str | None = None,
    user_id: int,
) -> Sequence[MemoryItem]:
    stmt = (
        select(MemoryItem)
        .where(MemoryItem.user_id == user_id)
        .order_by(MemoryItem.created_at.desc())
        .limit(limit)
    )
    if kind:
        stmt = stmt.where(MemoryItem.kind == kind)
    if source_ref:
        stmt = stmt.where(MemoryItem.source_ref == source_ref)
    return db.scalars(stmt).all()


def replace_by_ref(
    db: Session,
    *,
    kind: str,
    source_ref: str,
    contents: Sequence[str],
    limit: int = 8,
    user_id: int,
) -> list[MemoryItem]:
    """按 source_ref 整体替换一组提炼产物（先删旧、再写新）。

    用于"重新提炼"语义：日/周/月产物共享同一 source_ref 时保证幂等覆盖，
    即使本次输出为空也会清掉旧条目（避免旧结论残留）。
    """
    old = db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.source_ref == source_ref,
        )
    ).all()
    for item in old:
        db.delete(item)
    if old:
        db.commit()

    created: list[MemoryItem] = []
    for text2 in list(contents)[:limit]:
        content = str(text2).strip()
        if not content:
            continue
        created.append(
            store_memory(
                db,
                kind=kind,
                content=content,
                source_ref=source_ref,
                embed_vectors=True,
                user_id=user_id,
            )
        )
    return created


def replace_insights(
    db: Session, date: str, insights: Sequence[str], user_id: int
) -> list[MemoryItem]:
    """替换某日 L1 洞察（由复盘生成时一次调用产出）。"""
    return replace_by_ref(
        db,
        kind="insight",
        source_ref=f"insight:{date}",
        contents=insights,
        user_id=user_id,
    )


def remove_by_source(
    db: Session, kind: str, source_id: int, user_id: int
) -> int:
    """删除某来源记录时级联删除其记忆索引行（保持索引与源同步）。"""
    source_ref = f"{kind}:{source_id}"
    rows = db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id, MemoryItem.source_ref == source_ref
        )
    ).all()
    deleted = 0
    for r in rows:
        db.delete(r)
        deleted += 1
    if deleted:
        db.commit()
    return deleted


INSIGHT_SYSTEM_PROMPT = """你是 LifeLab 的记忆提炼助手。给你一天内用户记录的原始文本，
请提炼出 2-5 条对用户**长期有价值**的洞察（记忆），供未来检索。

规则：
1. 洞察应是可复用的规律、偏好、模式、改进建议，不是流水账。
2. 只基于提供的文本；禁止编造。
3. 区分事实与推测：推测必须标注「(推测)」。
4. 每条洞察一句话（<80字），具体、可执行。
5. 输出合法 JSON：{"insights": ["洞察1", "洞察2", ...]}。仅此一个字段。"""


def extract_insights(
    db: Session, date: str, *, tz_offset: int = 0, user_id: int
) -> list[MemoryItem]:
    """从某天的记录提炼长期洞察并写入记忆（幂等：同一日期只提炼一次）。

    聚合当天 thoughts/events/findings/review 文本 → LLM 提炼 → 逐条
    存入 memory (kind=insight, source_ref="insight:{date}") 并向量化。
    """
    # 幂等：当天已提炼过则跳过
    ref = f"insight:{date}"
    exists = db.scalar(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id, MemoryItem.source_ref == ref
        )
    )
    if exists:
        return [exists]

    # 聚合当天所有来源文本（按本地时区分天，与 timeline/review 一致）
    try:
        day_start, day_end = parse_day_range(date, tz_offset)
    except ValueError:
        return []

    fragments = []
    for kind, model, field in _MATERIAL_SOURCES:
        ts_col = getattr(model, "__table__", None)
        ts_col = ts_col.c.timestamp if ts_col is not None and "timestamp" in ts_col.c else None
        stmt = select(model).where(model.user_id == user_id).order_by(model.id)
        if ts_col is not None:
            stmt = stmt.where(ts_col >= day_start, ts_col < day_end)
        else:
            # 无 timestamp 列的模型（findings/problems）：取全部
            pass
        rows = db.scalars(stmt).all()
        for row in rows:
            content = getattr(row, field) or ""
            if content.strip():
                fragments.append(f"[{kind}] {content.strip()}")
    # 加上当天的 review 正文
    from app.services import review_service as _rs

    rev = _rs.get_review(db, date, user_id)
    if rev and rev.review_text:
        fragments.append(f"[review] {rev.review_text}")

    if not fragments:
        return []  # 当天无任何记录，无从提炼

    user_prompt = (
        f"日期：{date}\n\n当天记录：\n" + "\n".join(fragments[:200])
    )
    try:
        raw = chat_json(INSIGHT_SYSTEM_PROMPT, user_prompt, caller="memory.insight")
        insights = raw.get("insights") or []
    except LLMError as e:
        logger.warning("insight 提炼失败: %s", e)
        return []

    created = []
    for text2 in insights[:8]:
        if not str(text2).strip():
            continue
        item = store_memory(
            db,
            kind="insight",
            content=str(text2).strip(),
            source_ref=ref,
            embed_vectors=True,
            user_id=user_id,
        )
        created.append(item)
    return created


# ── L2：周模式提炼 ────────────────────────────────────────────

PATTERN_SYSTEM_PROMPT = """你是 LifeLab 的长期行为模式分析师。给你某一周用户积累的
「日洞察」「发现（观察/假设/结论）」与「复盘」，请提炼 1-5 条**跨日的长期模式**。

规则：
1. 模式必须是跨天反复出现的倾向/循环/触发器，如"压力大时倾向熬夜刷手机"。
2. 只基于提供的文本；禁止编造。不足以下结论时，宁可少给。
3. 严格区分事实与推测：推测必须标注「(推测)」。
4. 每条模式一句话（<100字），说明触发条件与结果。
5. 输出合法 JSON：{"patterns": ["模式1", "模式2", ...]}。仅此一个字段。"""


def iso_week_bounds(date: str) -> tuple[str, str, str]:
    """任意一天 → 所在 ISO 周的 (周一, 周日, "YYYY-Www")。"""
    try:
        d = date_cls.fromisoformat(date)
    except ValueError as e:
        raise ValueError(f"date must be YYYY-MM-DD: {date}") from e
    monday = d - timedelta(days=d.isoweekday() - 1)
    sunday = monday + timedelta(days=6)
    iso = monday.isocalendar()
    return (
        monday.isoformat(),
        sunday.isoformat(),
        f"{iso.year}-W{iso.week:02d}",
    )


def day_strings(start: str, end: str) -> list[str]:
    """起止日（含）之间的全部 YYYY-MM-DD。用于周/月区间遍历。"""
    first = date_cls.fromisoformat(start)
    days = (date_cls.fromisoformat(end) - first).days
    return [(first + timedelta(days=i)).isoformat() for i in range(days + 1)]


# 兼容旧名（周提炼复用日区间的语义）
week_day_strings = day_strings


def get_patterns(
    db: Session, week: str, user_id: int
) -> list[MemoryItem]:
    """查看某 ISO 周已提炼的模式。"""
    ref = f"pattern:{week}"
    return list(
        db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                MemoryItem.kind == "pattern",
                MemoryItem.source_ref == ref,
            )
            .order_by(MemoryItem.id)
        ).all()
    )


def extract_patterns(
    db: Session,
    date: str,
    *,
    tz_offset: int = 0,
    force: bool = False,
    user_id: int,
) -> tuple[str, str, str, list[MemoryItem]]:
    """从某 ISO 周内的洞察/发现/复盘中提炼长期模式（L2）。

    幂等：同一周已提炼过则直接返回；force=True 时删除旧模式重新提炼。
    返回 (week, monday, sunday, patterns)。date 为口述日期（任意一天，自动对齐周）。
    """
    monday, sunday, week = iso_week_bounds(date)
    ref = f"pattern:{week}"

    existing = get_patterns(db, week, user_id)
    if existing and not force:
        return week, monday, sunday, existing
    if existing and force:
        for item in existing:
            db.delete(item)
        db.commit()

    # 1) 本周的 L1 洞察（source_ref=insight:YYYY-MM-DD 精确对应本地日）
    day_refs = [f"insight:{d}" for d in day_strings(monday, sunday)]
    insights = db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.kind == "insight",
            MemoryItem.source_ref.in_(day_refs),
        )
    ).all()
    fragments = [f"[insight] {i.content}" for i in insights if i.content.strip()]

    # 2) 本周的发现（按本地周起止的 UTC 区间过滤）
    week_start, _ = parse_day_range(monday, tz_offset)
    _, week_end = parse_day_range(sunday, tz_offset)
    findings = db.scalars(
        select(Finding).where(
            Finding.user_id == user_id,
            Finding.created_at >= week_start,
            Finding.created_at < week_end,
        )
    ).all()
    for f in findings:
        fragments.append(
            f"[finding/{f.kind}] {f.title}: {f.observation}"
            + (f" | 推测: {f.interpretation}" if f.interpretation else "")
        )

    # 3) 本周的复盘正文
    reviews = db.scalars(
        select(DailyReview).where(
            DailyReview.user_id == user_id,
            DailyReview.date >= monday,
            DailyReview.date <= sunday,
        )
    ).all()
    for r in reviews:
        if r.review_text:
            fragments.append(f"[review {r.date}] {r.review_text}")

    if not fragments:
        return week, monday, sunday, []  # 本周无素材，无从提炼

    user_prompt = (
        f"周区间：{monday} ~ {sunday}（{week}）\n\n"
        f"本周素材：\n" + "\n".join(fragments[:200])
    )
    try:
        raw = chat_json(PATTERN_SYSTEM_PROMPT, user_prompt, caller="memory.pattern")
        patterns = raw.get("patterns") or []
    except LLMError as e:
        logger.warning("pattern 提炼失败: %s", e)
        return week, monday, sunday, []

    created = []
    for text2 in patterns[:8]:
        if not str(text2).strip():
            continue
        created.append(
            store_memory(
                db,
                kind="pattern",
                content=str(text2).strip(),
                source_ref=ref,
                embed_vectors=True,
                user_id=user_id,
            )
        )
    return week, monday, sunday, created