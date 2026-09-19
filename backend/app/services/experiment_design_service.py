"""AI 实验设计：长期问题 + 发现 + 历史记忆 → 可执行实验草稿。

红线：AI 只产出草稿（不落库）；用户在前端编辑确认后，走既有
POST /experiments 创建 DRAFT 实验，是否采纳完全由用户决定。
数据源受 MetricDef 白名单约束，LLM 编造的非法 source 会被丢弃。
"""

import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.behavior_categories import CATEGORIES, ONLINE_CATEGORIES
from app.llm import chat_json
from app.models.finding import Finding
from app.models.problem import Problem
from app.schemas.analysis import DraftEvidence, ExperimentDraftResponse
from app.schemas.experiment import MetricDef
from app.services import memory_service, profile_service
from app.services.event_type_service import creatable_builtins

# 数据源清单从常量生成，避免 prompt 里的类型/大类列表与记录按钮各写一份后漂移
_EVENT_TYPE_KEYS = "|".join(creatable_builtins())
_ONLINE_CATEGORY_KEYS = "|".join(k for k in CATEGORIES if k in ONLINE_CATEGORIES)

logger = logging.getLogger(__name__)

DESIGN_SYSTEM_PROMPT = """你是 LifeLab 的行为实验设计师。基于用户的长期问题、
相关发现与历史记忆，设计一个 7-30 天可执行的行为实验。

可用数据源（metrics[].source 只能取下列值）：
- event_duration:__EVENT_TYPES__
  某类事件当天总时长（分钟；只有线下记录类型可手记）
- event_count:<同上事件类型>   某类事件当天次数
- event_duration_category:<行为大类>  设备真实活动某大类当天时长（分钟，自动采集）
  大类取值：__ONLINE_CATEGORIES__
- event_count_category:<行为大类>      某大类设备段次数
- state_avg:energy|focus|irritation    当天状态均值（0-5）
- usage_platform:android|pc   手机/电脑当天使用总时长（分钟，设备自动采集）
- usage_duration:<应用名>      某应用当天使用时长（分钟，设备自动采集）
  以上 usage_* 可加时段后缀 @起-止，如 usage_platform:android@22-24
- manual   手动录入

设计要求：
1. 明确改变什么（variable，具体到可执行动作）、观察什么（indicator）、数据从哪来。
2. 优先用能自动聚合的数据源，减少手动负担；指标 1-4 个，突出 1-2 个核心指标。
3. 假设必须可证伪；不要预设结论，不要编造用户数据。
4. expected_days 取 7-30 的整数。
5. rationale 用 100 字内说明设计思路（给人看，不写进实验）。
6. 只输出 JSON：
{
  "name": "实验名（<30字）",
  "question": "要回答的问题",
  "hypothesis": "可证伪的假设",
  "variable": "改变的变量（具体动作）",
  "indicator": "观察指标说明",
  "metrics": [
    {"key": "英文短键如 phone_min", "name": "中文指标名", "unit": "分钟/次/分 或 null",
     "direction": "up_good|down_good|neutral", "source": "..."}
  ],
  "expected_days": 14,
  "baseline_note": "基线说明（当前状态，用于对照）",
  "rationale": "设计思路"
}""".replace("__EVENT_TYPES__", _EVENT_TYPE_KEYS).replace(
    "__ONLINE_CATEGORIES__", _ONLINE_CATEGORY_KEYS
)


def _gather_problem(
    db: Session, problem: Problem, user_id: int
) -> tuple[str, list[DraftEvidence]]:
    """问题 + 发现 + 记忆召回拼成 user prompt；返回 (文本, 依据列表)。"""
    evidence: list[DraftEvidence] = []
    parts = [f"长期问题：{problem.title}"]
    if problem.category:
        parts.append(f"分类：{problem.category}")
    if problem.note:
        parts.append(f"补充说明：{problem.note}")

    findings = db.scalars(
        select(Finding)
        .where(
            Finding.user_id == user_id,
            Finding.problem_id == problem.id,
        )
        .order_by(Finding.created_at.desc())
        .limit(5)
    ).all()
    if findings:
        lines = []
        for f in findings:
            lines.append(f"- [{f.kind}] {f.title}: {f.observation}")
            evidence.append(
                DraftEvidence(kind="finding", ref=f"finding:{f.id}", title=f.title)
            )
        parts.append("相关发现：\n" + "\n".join(lines))

    try:
        hits = memory_service.search_memory(db, problem.title, top_k=4, user_id=user_id)
        if hits:
            lines = []
            for h in hits:
                lines.append(f"- [{h.item.kind}] {h.item.content}")
                evidence.append(
                    DraftEvidence(
                        kind=h.item.kind,
                        ref=h.item.source_ref,
                        similarity=round(h.similarity, 4),
                    )
                )
            parts.append("历史相关记忆：\n" + "\n".join(lines))
    except Exception:
        logger.warning("实验设计召回失败", exc_info=True)

    try:
        profile = profile_service.prompt_context(db, user_id)
        if profile:
            parts.append(profile)
    except Exception:
        logger.warning("实验设计画像注入失败", exc_info=True)

    return "\n\n".join(parts), evidence


def _parse_metrics(raw_metrics: object) -> list[MetricDef]:
    """校验 LLM 指标：非法 source/字段丢弃，key 去重；全丢则给手动兜底指标。"""
    metrics: list[MetricDef] = []
    seen: set[str] = set()
    if isinstance(raw_metrics, list):
        for item in raw_metrics[:4]:
            try:
                m = MetricDef.model_validate(item)
            except ValidationError as e:
                logger.info("丢弃非法指标 %s: %s", item, e)
                continue
            if m.key in seen:
                continue
            seen.add(m.key)
            metrics.append(m)
    if not metrics:
        metrics = [
            MetricDef(
                key="subjective",
                name="主观效果评分",
                unit="分",
                direction="up_good",
                source="manual",
            )
        ]
    return metrics


def design_experiment(
    db: Session, problem_id: int, user_id: int
) -> ExperimentDraftResponse | None:
    """问题 → 实验草稿；问题不存在返回 None，LLM 失败抛 LLMError（路由转 502）。"""
    problem = db.scalar(
        select(Problem).where(
            Problem.id == problem_id, Problem.user_id == user_id
        )
    )
    if problem is None:
        return None

    user_prompt, evidence = _gather_problem(db, problem, user_id)
    raw = chat_json(DESIGN_SYSTEM_PROMPT, user_prompt, caller="experiment.design")
    expected = raw.get("expected_days")
    try:
        expected_days = max(7, min(30, int(expected)))
    except (TypeError, ValueError):
        expected_days = 14

    return ExperimentDraftResponse(
        problem_id=problem.id,
        name=str(raw.get("name") or problem.title)[:200],
        question=str(raw.get("question") or problem.title)[:5000],
        hypothesis=str(raw.get("hypothesis") or "").strip(),
        variable=str(raw.get("variable") or "").strip(),
        indicator=str(raw.get("indicator") or "").strip(),
        metrics=_parse_metrics(raw.get("metrics")),
        expected_days=expected_days,
        baseline_note=(str(raw.get("baseline_note")).strip() if raw.get("baseline_note") else None),
        rationale=str(raw.get("rationale") or "").strip()[:500],
        evidence=evidence,
    )
