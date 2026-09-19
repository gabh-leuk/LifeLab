"""AI 剖析授权写入：问答 → 候选（问题/背景）→ 用户勾选 → 入库。

红线：AI 不自动写入；/analysis/commit 是唯一写入路径，且只写用户
在前端勾选确认的候选，problem/profile_fact 的 source 一律标 "ai" 可区分。
"""

import logging
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.llm import chat_json
from app.models.problem import Problem
from app.models.profile import ProfileFact
from app.schemas.analysis import BackgroundCandidate, ProblemCandidate
from app.services import problem_service, profile_service

ANALYSIS_SOURCE_REF = "analysis:ask"
MAX_PROBLEM_CANDIDATES = 5
MAX_BACKGROUND_CANDIDATES = 8

logger = logging.getLogger(__name__)

DISTILL_SYSTEM_PROMPT = """你是 LifeLab 的记忆精炼助手。用户在「AI 剖析」中问了一个问题，
下面给出问题和 AI 的回答。请从中提炼两类可沉淀内容：

1. problem_candidates：用户自身长期存在的问题/困惑（如"为什么学习前会逃避？"），
   值得被长期追踪、用实验验证的议题——不是一次性疑问。
2. background_candidates：关于用户、跨话题稳定的一句话事实（身份/习惯/偏好/情境/限制），
   如"压力大时倾向熬夜刷手机"。

规则：
1. 只基于问答内容提炼；禁止编造。没有可提炼的内容就返回空数组。
2. 不确定的推断必须标注「(推测)」，且 confidence ≤ 0.5。
3. 问题标题 <60 字；背景每条 <50 字；均不含日期、不带编号。
4. 不要与已有的问题/背景重复（下方清单）。
5. 输出合法 JSON：
{
  "problem_candidates": [
    {"title": "...", "category": "...", "note": "依据/背景，可为空", "confidence": 0.0}
  ],
  "background_candidates": [
    {"category": "identity|habit|preference|context|constraint", "content": "...", "confidence": 0.0}
  ]
}
仅以上字段；数组元素必须是对象。"""


def _norm(text: str) -> str:
    return "".join(text.split()).lower()


def _existing_problems(db: Session, user_id: int) -> Sequence[Problem]:
    return problem_service.list_for_user(db, user_id)


def _user_prompt(
    question: str,
    answer: str,
    titles: list[str],
    facts: Sequence[ProfileFact],
) -> str:
    parts = [f"用户的问题：{question}", f"AI 的回答：{answer}"]
    if titles:
        parts.append("已有问题（不要重复）：\n" + "\n".join(f"- {t}" for t in titles[:50]))
    if facts:
        parts.append(
            "已有背景（不要重复）：\n"
            + "\n".join(f"- [{f.category}] {f.content}" for f in facts[:80])
        )
    return "\n\n".join(parts)


def _parse_candidates(
    raw: dict,
) -> tuple[list[ProblemCandidate], list[BackgroundCandidate]]:
    problems: list[ProblemCandidate] = []
    backgrounds: list[BackgroundCandidate] = []
    for item in (raw.get("problem_candidates") or [])[:MAX_PROBLEM_CANDIDATES]:
        try:
            problems.append(ProblemCandidate.model_validate(item))
        except ValidationError:
            continue
    for item in (raw.get("background_candidates") or [])[:MAX_BACKGROUND_CANDIDATES]:
        try:
            backgrounds.append(BackgroundCandidate.model_validate(item))
        except ValidationError:
            continue
    return problems, backgrounds


def distill(
    db: Session, question: str, answer: str, user_id: int
) -> tuple[list[ProblemCandidate], list[BackgroundCandidate]]:
    """问答 → 候选清单（不落库；失败抛 LLMError 由路由转 502）。"""
    titles = [p.title for p in _existing_problems(db, user_id)]
    facts = profile_service.list_facts(db, user_id=user_id)
    raw = chat_json(
        DISTILL_SYSTEM_PROMPT,
        _user_prompt(question, answer, titles, facts),
        caller="analysis.distill",
    )
    return _parse_candidates(raw)


def commit(
    db: Session,
    problems: list[ProblemCandidate],
    backgrounds: list[BackgroundCandidate],
    user_id: int,
) -> tuple[list[int], list[int], int, int]:
    """把用户勾选的候选写入问题库/用户背景（来源一律标 ai）。

    重复或超配额只跳过不报错，返回 (problem_ids, fact_ids, skipped_p, skipped_b)。
    """
    existing = {_norm(t) for t in (p.title for p in _existing_problems(db, user_id))}
    problem_ids: list[int] = []
    p_skipped = 0
    for c in problems:
        title = c.title.strip()
        if not title or _norm(title) in existing:
            p_skipped += 1
            continue
        row = problem_service.create(
            db,
            user_id=user_id,
            title=title[:200],
            category=(c.category or "").strip() or None,
            note=(c.note or "").strip() or None,
            source="ai",
            source_ref=ANALYSIS_SOURCE_REF,
        )
        problem_ids.append(row.id)
        existing.add(_norm(title))
    if problem_ids:
        db.commit()

    fact_ids: list[int] = []
    b_skipped = 0
    for c in backgrounds:
        try:
            fact = profile_service.create_fact(
                db,
                category=c.category,
                content=c.content,
                source="ai",
                source_ref=ANALYSIS_SOURCE_REF,
                confidence=c.confidence,
                confirmed=True,
                user_id=user_id,
            )
            fact_ids.append(fact.id)
        except profile_service.ProfileError as e:
            logger.info("背景候选跳过：%s", e)
            b_skipped += 1

    return problem_ids, fact_ids, p_skipped, b_skipped
