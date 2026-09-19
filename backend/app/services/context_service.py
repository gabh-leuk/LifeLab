"""Prompt 上下文组装：历史记忆检索 + 画像 + 追踪中的问题。

复盘生成前调用，把系统"已经知道的东西"注入 prompt，避免重复提炼与失忆式建议：
- 画像（profile_facts）：用户确认过的既定事实
- 问题（OPEN/DORMANT）：正在追踪的议题
- 历史记忆（向量召回 top_k，带门槛与时间权重）：提供历史纵深、检测矛盾

查询构造：
- 日复盘：LLM 从当天原始记录写一句检索查询（失败回退确定性拼接）
- 周/月复盘：材料本身已是浓缩产物，直接拼接关键文本，不额外调 LLM

跨期召回（周/月复盘）：
- 池子正向限定为「早于本期」——本期素材已经全在 prompt 里，再召回来只是复读；
  要的是更早期间有没有同类信号（延续/反转），所以按内容日期在候选阶段就过滤。
- 每期间（月复盘按月、周复盘按周）最多留 per_bucket 条，避免最近的某一期占满名额。
"""

import logging
from collections.abc import Sequence
from datetime import date as date_cls
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import LLMError, chat_json
from app.models.problem import Problem, ProblemStatus
from app.services import memory_service, profile_service

logger = logging.getLogger(__name__)


# 跨期召回要"取全池再按期间分桶"，候选窗口必须放开到 rag_max_candidates 之上，
# 否则最近的那一期会把窗口占满，更早期间根本进不了池子（100 对单用户记忆库足够）。
_POOL_FETCH = 100

# 跨期召回不带时间权重：这里问的是"更早期间有没有同类信号"，越早不等于越不重要。
# 实测时间衰减正好帮倒忙 —— 上月同地的旅游洞察 sim=0.698 会被更近但更弱的
# sim=0.678 压下去，要找回的复发信号反而被埋掉。
PERIOD_TIME_WEIGHT = 0.0
# 每个更早期间先预留的名额（不够再用补位填满），避免最近那一期独吞
PERIOD_RESERVE = 2


QUERY_SYSTEM_PROMPT = """你是个人记忆检索系统的查询生成器。根据用户某天的原始记录，
写一句用于检索「过去相关记忆」的查询语句。

规则：
1. 聚焦今天经历的主题（行为/情绪/实验/困扰），推测可能与过去的哪些内容相关。
2. 一句话，≤50 字，中文；不要包含具体日期、不要加"查询："之类前缀。
3. 输出合法 JSON：{"query": "..."}。仅此一个字段。"""

PROBLEM_STATUS_LABELS = {
    ProblemStatus.OPEN.value: "待解决",
    ProblemStatus.DORMANT.value: "搁置",
}


def _snapshot_brief(snapshot: dict) -> str:
    """把当天快照压成给查询生成器的简短描述。"""
    parts = [f"日期：{snapshot.get('date', '')}"]
    note = (snapshot.get("note") or "").strip()
    if note:
        parts.append(f"手写小结：{note}")
    thoughts = [
        t.get("content", "") for t in snapshot.get("thoughts", []) if t.get("content")
    ]
    if thoughts:
        parts.append("想法：" + "；".join(thoughts[:5]))
    counts: dict[str, int] = {}
    for e in snapshot.get("events", []):
        key = e.get("type", "")
        counts[key] = counts.get(key, 0) + 1
    if counts:
        parts.append("事件：" + "，".join(f"{k}×{v}" for k, v in counts.items()))
    states = snapshot.get("states", [])
    if states:
        parts.append(
            "状态均值：精力 {:.1f} 专注 {:.1f}".format(
                fmean([s.get("energy", 0) for s in states]),
                fmean([s.get("focus", 0) for s in states]),
            )
        )
    return "\n".join(parts)


def fallback_query(snapshot: dict) -> str:
    """LLM 不可用时的确定性检索查询：小结 + 想法 + 主要事件类型。"""
    parts: list[str] = []
    note = (snapshot.get("note") or "").strip()
    if note:
        parts.append(note)
    parts += [
        t.get("content", "") for t in snapshot.get("thoughts", [])[:3] if t.get("content")
    ]
    counts: dict[str, int] = {}
    for e in snapshot.get("events", []):
        counts[e.get("type", "")] = counts.get(e.get("type", ""), 0) + 1
    if counts:
        top = sorted(counts, key=lambda k: counts[k], reverse=True)[:3]
        parts.append(" ".join(top))
    return "；".join(p for p in parts if p)[:200] or "今天的状态与行为"


def extract_search_query(snapshot: dict) -> str:
    """LLM 从当天原始记录生成检索查询；失败时回退确定性拼接。"""
    try:
        raw = chat_json(
            QUERY_SYSTEM_PROMPT, _snapshot_brief(snapshot), max_tokens=200, caller="context.query"
        )
        query = str(raw.get("query") or "").strip()
        if query:
            return query[:200]
    except LLMError:
        logger.warning("检索查询生成失败，使用确定性兜底", exc_info=True)
    return fallback_query(snapshot)


def context_query_from_texts(texts: Sequence[str], limit: int = 3) -> str:
    """周/月复盘用：材料已是浓缩文本，直接拼接作查询（零 LLM 调用）。"""
    parts = [t.strip() for t in texts if t and t.strip()][:limit]
    return "；".join(parts)[:200] or "近期的行为与模式"


def _period_key(anchor: date_cls, granularity: str) -> tuple[int, ...]:
    """内容日期 → 所属期间（周按 ISO 周，月按自然月）。"""
    if granularity == "week":
        year, week, _ = anchor.isocalendar()
        return (year, week)
    return (anchor.year, anchor.month)


def _spread_per_period(
    hits: list, granularity: str, top_k: int, reserve: int = PERIOD_RESERVE
) -> list:
    """跨期间铺开：每个期间先占 reserve 个名额，剩下的按分数补满 top_k。

    纯按全局相关性取 top_k 时，刚过去的那一期会占满名额（实测 5 条里 4 条是本月的），
    更早期间的复发信号被挤掉。先给每期留名额保证跨期覆盖，再补位保证不浪费名额 ——
    只有一个更早期间时，它照常能占满 top_k。
    """
    buckets: dict[tuple[int, ...], list] = {}
    for h in hits:  # hits 已按 final_score 降序
        buckets.setdefault(_period_key(h.anchor_date, granularity), []).append(h)
    picked = [h for bucket in buckets.values() for h in bucket[:reserve]]
    if len(picked) < top_k:
        rest = [h for bucket in buckets.values() for h in bucket[reserve:]]
        rest.sort(key=lambda h: h.final_score, reverse=True)
        picked += rest[: top_k - len(picked)]
    picked.sort(key=lambda h: h.final_score, reverse=True)
    return picked


def _retrieve_history(
    db: Session,
    query: str,
    *,
    top_k: int,
    exclude_refs: set[str],
    before: date_cls | None,
    granularity: str | None,
    time_weight: float | None,
    user_id: int,
) -> list:
    if before is None:
        # 日复盘：全库召回，事后按 source_ref 剔除当天自己的产物
        hits = memory_service.search_memory(
            db, query, top_k=top_k + len(exclude_refs), user_id=user_id
        )
        return [h for h in hits if (h.item.source_ref or "") not in exclude_refs][:top_k]

    # 周/月复盘：候选阶段就限定「早于本期」，再按期间铺开名额
    hits = memory_service.search_memory(
        db,
        query,
        top_k=_POOL_FETCH,
        candidate_limit=_POOL_FETCH,
        before=before,
        time_weight=PERIOD_TIME_WEIGHT if time_weight is None else time_weight,
        user_id=user_id,
    )
    hits = [h for h in hits if (h.item.source_ref or "") not in exclude_refs]
    if granularity:
        hits = _spread_per_period(hits, granularity, top_k)
    hits = hits[:top_k]
    # 输出按时间正序：让模型看到的是先后序列，才谈得上「延续还是反转」
    hits.sort(key=lambda h: (h.anchor_date, -h.final_score))
    return hits


def build_history(
    db: Session,
    query: str,
    *,
    top_k: int = 5,
    exclude_refs: set[str] | None = None,
    before: date_cls | None = None,
    granularity: str | None = None,
    time_weight: float | None = None,
    user_id: int,
) -> dict:
    """组装历史上下文：画像 + 追踪中的问题 + 向量召回。

    任何一部分失败都只降级为空，绝不影响复盘生成本身。
    返回 dict：{query, profile, problems, hits}。

    before/granularity 一起用即「跨期召回」：只召回早于 before 的记忆，
    并按 granularity（"week"/"month"）给每个期间预留意额。默认都不给，走原来的
    全库召回，日复盘路径的行为因此逐字不变。
    """
    exclude = exclude_refs or set()
    history: dict = {"query": query, "profile": "", "problems": [], "hits": []}

    try:
        history["profile"] = profile_service.prompt_context(db, user_id)
    except Exception:
        logger.warning("画像注入失败", exc_info=True)

    try:
        rows = db.scalars(
            select(Problem)
            .where(
                Problem.user_id == user_id,
                Problem.status.in_(
                    [ProblemStatus.OPEN.value, ProblemStatus.DORMANT.value]
                ),
            )
            .order_by(Problem.updated_at.desc())
            .limit(20)
        ).all()
        history["problems"] = [(p.title, p.status) for p in rows]
    except Exception:
        logger.warning("问题注入失败", exc_info=True)

    try:
        history["hits"] = _retrieve_history(
            db,
            query,
            top_k=top_k,
            exclude_refs=exclude,
            before=before,
            granularity=granularity,
            time_weight=time_weight,
            user_id=user_id,
        )
    except Exception:
        logger.warning("历史检索失败", exc_info=True)

    return history


def render_history(history: dict) -> str:
    """把上下文渲染成 prompt 文本块；无内容时返回空串。"""
    blocks: list[str] = []
    if history.get("profile"):
        blocks.append(str(history["profile"]))

    problems = history.get("problems") or []
    if problems:
        lines = [
            f"- [{PROBLEM_STATUS_LABELS.get(status, status)}] {title}"
            for title, status in problems
        ]
        blocks.append("你正在追踪的问题：\n" + "\n".join(lines))

    hits = history.get("hits") or []
    if hits:
        # 带内容日期：跨期召回是按时间正序给的，模型要能看出「哪一期、隔了多久」
        # 才谈得上判断延续/反转；来源 ref 不进 prompt（日期就是它的信息量，
        # 追溯看 source_data.history）
        lines = [
            f"- [{h.item.kind}] {h.anchor_date.isoformat()} {h.item.content}"
            for h in hits
        ]
        blocks.append(
            "历史相关记忆（来自更早的期间，按时间正序；用于判断延续/反转与纵深，"
            "不要直接复述）：\n" + "\n".join(lines)
        )

    return "\n\n".join(blocks)


def history_meta(history: dict) -> dict:
    """可序列化摘要：存档到复盘 source_data 供追溯。"""
    return {
        "query": history.get("query", ""),
        "hits": [
            {
                "kind": h.item.kind,
                "ref": h.item.source_ref,
                "date": h.anchor_date.isoformat(),
                "similarity": round(h.similarity, 4),
            }
            for h in history.get("hits") or []
        ],
        "problems": len(history.get("problems") or []),
        "has_profile": bool(history.get("profile")),
    }
