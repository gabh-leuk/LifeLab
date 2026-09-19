"""Agent 工具集。

**两条通道，界限是「会不会写库」**：
- 只读工具（`READ_TOOLS`）在 `tools` 节点当场执行，结果直接回给 LLM；
- 写工具（`WRITE_TOOLS`）**不执行**，只登记成 `pending`，由 `approval` 节点
  `interrupt()` 停下等人确认，批准了才落库。

这条界限是本仓库红线的落地：AI 产物必须用户确认后才写（`source=ai` 那套约定）。
旧的无状态 Agent 是直接 `create_finding` 落库的，是这条红线上的一个洞。

**M6 第二步**把复盘与剖析整条搬进来，所以写工具从 1 个变成 5 个：AI 生成的东西
（日报/周报/月报、问题候选、背景候选）一律先弹审批卡，人点了才落库。

工具拿到的不是裸 `user_id` 而是 `ToolCtx`：复盘按**本地时区**分天，同一个
`"2026-09-16"` 在东八区和 UTC 指的不是同一段真实时间，时区得跟着请求走。
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services import (
    analysis_service,
    day_note_service,
    experiment_design_service,
    experiment_service,
    finding_service,
    memory_service,
    metric_source_service,
    period_review_service,
    problem_service,
    profile_service,
    review_service,
    summary_service,
)

# Agent 写入的产物一律打这个来源标记，便于与手工记录区分
AGENT_SOURCE_REF = "agent:chat"


@dataclass(frozen=True)
class ToolCtx:
    """工具运行上下文。

    `tz_offset`：UTC 偏移分钟（东八区=480）。决定「今天」是哪一天、复盘切哪一段。
    """

    user_id: int
    tz_offset: int = 0

    def today(self) -> str:
        return (
            datetime.now(timezone.utc) + timedelta(minutes=self.tz_offset)
        ).date().isoformat()


# ── 工具声明（JSON Schema，供 LLM 选择） ──────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "语义检索用户的记忆/记录（想法、事件、复盘、发现、问题）。"
            "用户问历史相关问题时，用它找关联信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的语义查询，中文表达",
                    },
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_experiments",
            "description": "列出用户的全部实验（含 id、名称、状态、指标）。"
            "要看某个实验的具体数据统计，先调它拿 id，再调 get_experiment_stats。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_experiment_stats",
            "description": "某个实验的数据统计：各指标的均值/极值/样本数、"
            "已运行天数（排除暂停）、数据点总数。回答「实验数据怎么样」用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "experiment_id": {
                        "type": "integer",
                        "description": "实验 id（来自 list_experiments）",
                    }
                },
                "required": ["experiment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_review",
            "description": "查看某日/某周/某月的复盘。日粒度还会带回当天的「小结」"
            "（用户手写的背景）与记录量统计（行为/想法/状态条数、平均精力注意力）。"
            "用户问「我昨天复盘说了什么」「今天记了多少」用它。只读，不生成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "period_type": {
                        "type": "string",
                        "enum": ["day", "week", "month"],
                        "description": "日/周/月，默认 day",
                    },
                    "date": {
                        "type": "string",
                        "description": "YYYY-MM-DD；省略表示今天（用户本地时区）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_problems",
            "description": "列出用户的长期问题（如「为什么精力不足」）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["OPEN", "DORMANT", "RESOLVED"],
                        "description": "按状态过滤，省略则全部",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_findings",
            "description": "列出用户的发现/结论（知识库条目）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "problem_id": {"type": "integer", "description": "只看挂在该问题下的"},
                    "kind": {
                        "type": "string",
                        "enum": ["OBSERVATION", "HYPOTHESIS", "CONCLUSION"],
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_profile_facts",
            "description": "列出用户的背景画像条目（作息、偏好、身体状况等长期事实）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "distill_analysis",
            "description": "把一次问答精炼成「问题候选」与「背景候选」（**只返回，不落库**）。"
            "用户想把这次对话沉淀成长期问题/背景时用它。拿到候选后，逐条用 "
            "create_problem / create_profile_fact 写入（每条都会让用户确认）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用户的问题"},
                    "answer": {"type": "string", "description": "你对这个问题的回答"},
                },
                "required": ["question", "answer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "design_experiment",
            "description": "针对某个长期问题设计一个行为实验草稿（**只返回，不落库**）。"
            "用户说「把这个问题变成实验」时用它。草稿给出后，让用户去「实验」页确认并创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "problem_id": {
                        "type": "integer",
                        "description": "问题 id（来自 list_problems）",
                    }
                },
                "required": ["problem_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "infer_metric_sources",
            "description": "把指标名识别成 LifeLab 的数据源（**只返回，不落库**）。"
            "用户设计实验、只给了指标名字（「睡前手机时长」「专注力」）而没说数据从哪来时用它。"
            "返回的是 source（管道字段）—— 用户不必看懂它，回答时一般不用逐条念出来。",
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {
                        "type": "array",
                        "description": "要识别的指标，1-10 条",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {
                                    "type": "string",
                                    "description": "指标 key（英文短名，实验里用来挂数据点）",
                                },
                                "name": {"type": "string", "description": "指标的中文名"},
                                "unit": {"type": "string", "description": "单位，可省略"},
                            },
                            "required": ["key", "name"],
                        },
                    },
                    "context": {
                        "type": "string",
                        "description": "实验名/问题/假设等背景，帮助判断，可省略",
                    },
                },
                "required": ["metrics"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_finding",
            "description": "把一条发现/结论写入知识库（可挂到某个问题下）。"
            "当用户在对话中表达出值得沉淀的觉察、规律、假设或结论时用它记录。"
            "**这是写操作：会先让用户确认，确认前不会落库。**",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "发现的标题"},
                    "observation": {
                        "type": "string",
                        "description": "观察到的事实（发生了什么）",
                    },
                    "problem_id": {
                        "type": "integer",
                        "description": "挂到的问题 id（可省略）",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["OBSERVATION", "HYPOTHESIS", "CONCLUSION"],
                    },
                    "interpretation": {
                        "type": "string",
                        "description": "可能的解释（推测）",
                    },
                    "next_step": {"type": "string", "description": "下一步建议"},
                },
                "required": ["title", "observation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_problem",
            "description": "写入一个长期问题/困惑（标记来源为 ai）。用于把 distill_analysis "
            "的问题候选落库，或用户直接说「把这条记成我的一个问题」。"
            "**这是写操作：会先让用户确认，确认前不会落库。**",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "问题标题，<60 字"},
                    "category": {"type": "string", "description": "分类（可省略）"},
                    "note": {"type": "string", "description": "依据/背景说明（可省略）"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_profile_fact",
            "description": "写入一条用户背景事实（标记来源为 ai）。用于把 distill_analysis "
            "的背景候选落库。**这是写操作：会先让用户确认，确认前不会落库。**",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["identity", "habit", "preference", "context", "constraint"],
                        "description": "身份/习惯/偏好/情境/限制",
                    },
                    "content": {"type": "string", "description": "一句话事实，<50 字"},
                    "confidence": {
                        "type": "number",
                        "description": "0-1，推测类 ≤0.5",
                    },
                },
                "required": ["category", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_day_note",
            "description": "保存/更新某日的「小结」——用户手写的当日背景，生成日复盘时会作为"
            "补充上下文注入。内容传空表示清除该日小结。"
            "**这是写操作：会先让用户确认，确认前不会落库。**",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "小结正文；空字符串=清除"},
                    "date": {
                        "type": "string",
                        "description": "YYYY-MM-DD；省略表示今天（用户本地时区）",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_review",
            "description": "生成（或重新生成）某日/某周/某月的 AI 复盘并入库，"
            "同时把当天数据同步到覆盖该日的实验（AUTO 数据点，幂等）。"
            "用户说「帮我复盘一下今天」「生成周复盘」时用它。只读数据、消耗额度。"
            "**这是写操作：会先让用户确认，确认前不会落库。**",
            "parameters": {
                "type": "object",
                "properties": {
                    "period_type": {
                        "type": "string",
                        "enum": ["day", "week", "month"],
                        "description": "日/周/月，默认 day",
                    },
                    "date": {
                        "type": "string",
                        "description": "YYYY-MM-DD；省略表示今天（用户本地时区）",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "已生成过时是否覆盖重生成，默认 false",
                    },
                },
            },
        },
    },
]

# 会写库的工具名 —— 路由靠它决定「要不要停下来等人确认」
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "create_finding",
        "create_problem",
        "create_profile_fact",
        "save_day_note",
        "generate_review",
    }
)

PERIOD_TYPES = ("day", "week", "month")


def _norm_period(args: dict) -> str | None:
    period_type = str(args.get("period_type") or "day").lower()
    return period_type if period_type in PERIOD_TYPES else None


# ── 只读工具实现 ────────────────────────────────────────────


def _search_memory(db: Session, args: dict, ctx: ToolCtx) -> dict:
    top_k = int(args.get("top_k") or 5)
    hits = memory_service.search_memory(
        db, args.get("query", ""), top_k=top_k, user_id=ctx.user_id
    )
    return {
        "hits": [
            {
                "kind": hit.item.kind,
                "content": hit.item.content,
                "source_ref": hit.item.source_ref,
            }
            for hit in hits[:top_k]
        ]
    }


def _list_experiments(db: Session, args: dict, ctx: ToolCtx) -> dict:
    return {
        "experiments": [
            {
                "id": e.id,
                "name": e.name,
                "status": e.status,
                "question": e.question,
                "metrics": [m.get("key") for m in (e.metrics or [])],
            }
            for e in experiment_service.list_for_user(db, ctx.user_id)
        ]
    }


def _get_experiment_stats(db: Session, args: dict, ctx: ToolCtx) -> dict:
    exp = experiment_service.get(db, int(args["experiment_id"]), ctx.user_id)
    if exp is None:
        return {"error": f"实验 {args.get('experiment_id')} 不存在"}
    stats = experiment_service.build_stats(db, exp)
    return {"experiment": exp.name, "status": exp.status, **stats.model_dump()}


def _get_review(db: Session, args: dict, ctx: ToolCtx) -> dict:
    period_type = _norm_period(args)
    if period_type is None:
        return {"error": f"period_type 只能是 {PERIOD_TYPES}，收到 {args.get('period_type')!r}"}
    date = str(args.get("date") or ctx.today())

    if period_type == "day":
        review = review_service.get_review(db, date, ctx.user_id)
        summary = summary_service.daily_summary(
            db, date, user_id=ctx.user_id, tz_offset=ctx.tz_offset
        )
        return {
            "period_type": "day",
            "date": date,
            "note": day_note_service.get_note_content(db, date, ctx.user_id),
            "counts": {
                "events": summary.event_total,
                "thoughts": summary.thought_total,
                "states": summary.state_total,
                "avg_energy": summary.avg_energy,
                "avg_focus": summary.avg_focus,
                "avg_irritation": summary.avg_irritation,
            },
            "by_type": [t.model_dump() for t in summary.by_type],
            "review_text": review.review_text if review else None,
            "review_status": review.status if review else None,
            "model": review.model if review else None,
        }

    data = period_review_service.get_read(db, period_type, date, ctx.user_id)
    if data is None:
        return {
            "period_type": period_type,
            "review_text": None,
            "hint": "该期间尚未生成复盘。周复盘建议先完成每日复盘；月复盘需要至少两个已完成的周复盘。",
        }
    return {
        "period_type": data["period_type"],
        "period_key": data["period_key"],
        "period_start": data["period_start"],
        "period_end": data["period_end"],
        "review_text": data["review_text"],
        "status": data["status"],
        "material_count": data["material_count"],
        "stale": data["stale"],
    }


def _list_problems(db: Session, args: dict, ctx: ToolCtx) -> dict:
    return {
        "problems": [
            {"id": p.id, "title": p.title, "status": p.status, "category": p.category}
            for p in problem_service.list_for_user(
                db, ctx.user_id, status=args.get("status")
            )
        ]
    }


def _list_findings(db: Session, args: dict, ctx: ToolCtx) -> dict:
    return {
        "findings": [
            {
                "id": f.id,
                "kind": f.kind,
                "title": f.title,
                "observation": f.observation,
                "problem_id": f.problem_id,
            }
            for f in finding_service.list_for_user(
                db,
                ctx.user_id,
                problem_id=args.get("problem_id"),
                kind=args.get("kind"),
                limit=50,
            )
        ]
    }


def _list_profile_facts(db: Session, args: dict, ctx: ToolCtx) -> dict:
    return {
        "facts": [
            {
                "id": f.id,
                "category": f.category,
                "content": f.content,
                "status": f.status,
            }
            for f in profile_service.list_facts(db, user_id=ctx.user_id)
        ]
    }


def _distill_analysis(db: Session, args: dict, ctx: ToolCtx) -> dict:
    question = str(args.get("question") or "").strip()
    answer = str(args.get("answer") or "").strip()
    if not question or not answer:
        return {"error": "question 与 answer 都不能为空"}
    problems, backgrounds = analysis_service.distill(
        db, question, answer, user_id=ctx.user_id
    )
    return {
        "problem_candidates": [c.model_dump() for c in problems],
        "background_candidates": [c.model_dump() for c in backgrounds],
        "note": "以上都还没落库。要沉淀哪几条，就用 create_problem / "
        "create_profile_fact 逐条写入（每条都会让用户确认）。",
    }


def _design_experiment(db: Session, args: dict, ctx: ToolCtx) -> dict:
    problem_id = int(args["problem_id"])
    draft = experiment_design_service.design_experiment(
        db, problem_id, user_id=ctx.user_id
    )
    if draft is None:
        return {"error": f"问题 {problem_id} 不存在"}
    return draft.model_dump()


def _infer_metric_sources(db: Session, args: dict, ctx: ToolCtx) -> dict:
    """指标名 → 数据源（只返回，不落库）。

    这是**唯一会烧 token 的只读工具**（内部调 LLM）。失败原样抛给 `run_tool`，
    由它兜成 `{"error": ...}` —— 识别的边界四条见 `metric_source_service`。
    """
    raw = args.get("metrics")
    metrics = [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []
    if not metrics:
        return {"error": "metrics 不能为空"}
    sources = metric_source_service.infer_sources(
        db, metrics, user_id=ctx.user_id, context=str(args.get("context") or "")
    )
    return {
        "sources": sources,
        "note": "以上只是识别结果，还没写进任何实验。要落到实验上，"
        "让用户到「实验」页用这些指标名创建实验，识别会自动完成。",
    }


READ_TOOLS: dict[str, Callable[[Session, dict, ToolCtx], dict]] = {
    "search_memory": _search_memory,
    "list_experiments": _list_experiments,
    "get_experiment_stats": _get_experiment_stats,
    "get_review": _get_review,
    "list_problems": _list_problems,
    "list_findings": _list_findings,
    "list_profile_facts": _list_profile_facts,
    "distill_analysis": _distill_analysis,
    "design_experiment": _design_experiment,
    "infer_metric_sources": _infer_metric_sources,
}


# ── 写工具实现（只在审批通过后被调用） ──────────────────────


def _create_finding(db: Session, args: dict, ctx: ToolCtx) -> dict:
    from app.schemas.finding import FindingCreate

    try:
        payload = FindingCreate.model_validate(
            {
                "title": args.get("title", ""),
                "observation": args.get("observation", ""),
                "problem_id": args.get("problem_id"),
                "kind": args.get("kind", "OBSERVATION"),
                "interpretation": args.get("interpretation"),
                "next_step": args.get("next_step"),
            }
        )
    except Exception as e:  # noqa: BLE001 - LLM/用户都可能给出非法参数，必须兜底
        return {"error": f"参数校验失败: {e}"}
    finding = finding_service.create(db, payload, user_id=ctx.user_id)
    db.commit()
    db.refresh(finding)
    return {
        "created": True,
        "finding_id": finding.id,
        "kind": finding.kind,
        "title": finding.title,
    }


def _create_problem(db: Session, args: dict, ctx: ToolCtx) -> dict:
    from app.schemas.problem import ProblemCreate

    try:
        payload = ProblemCreate.model_validate(
            {
                "title": args.get("title", ""),
                "category": args.get("category"),
                "note": args.get("note"),
            }
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"参数校验失败: {e}"}
    row = problem_service.create(
        db,
        user_id=ctx.user_id,
        **payload.model_dump(),
        source="ai",
        source_ref=AGENT_SOURCE_REF,
    )
    db.commit()
    db.refresh(row)
    return {"created": True, "problem_id": row.id, "title": row.title}


def _create_profile_fact(db: Session, args: dict, ctx: ToolCtx) -> dict:
    category = str(args.get("category") or "").strip()
    content = str(args.get("content") or "").strip()
    if not category or not content:
        return {"error": "category 与 content 都不能为空"}
    try:
        fact = profile_service.create_fact(
            db,
            category=category,
            content=content,
            source="ai",
            source_ref=AGENT_SOURCE_REF,
            confidence=args.get("confidence"),
            confirmed=True,
            user_id=ctx.user_id,
        )
    except profile_service.ProfileError as e:
        return {"error": f"背景未写入：{e}"}
    return {
        "created": True,
        "fact_id": fact.id,
        "category": fact.category,
        "content": fact.content,
    }


def _save_day_note(db: Session, args: dict, ctx: ToolCtx) -> dict:
    date = str(args.get("date") or ctx.today())
    note = day_note_service.upsert_note(db, date, str(args.get("content") or ""), ctx.user_id)
    return {
        "saved": True,
        "date": date,
        "content": note.content if note else "",
        "cleared": note is None,
    }


def _generate_review(db: Session, args: dict, ctx: ToolCtx) -> dict:
    period_type = _norm_period(args)
    if period_type is None:
        return {"error": f"period_type 只能是 {PERIOD_TYPES}，收到 {args.get('period_type')!r}"}
    date = str(args.get("date") or ctx.today())
    force = bool(args.get("force"))

    if period_type == "day":
        row = review_service.generate_review(
            db, date, force=force, tz_offset=ctx.tz_offset, user_id=ctx.user_id
        )
        return {
            "generated": True,
            "period_type": "day",
            "date": row.date,
            "status": row.status,
            "error": row.error,
            "review_text": row.review_text,
        }

    row = period_review_service.generate_period_review(
        db, period_type, date, force=force, tz_offset=ctx.tz_offset, user_id=ctx.user_id
    )
    data = period_review_service.build_read(db, row)
    return {
        "generated": True,
        "period_type": period_type,
        "period_key": data["period_key"],
        "status": data["status"],
        "error": data["error"],
        "review_text": data["review_text"],
    }


WRITE_TOOL_FNS: dict[str, Callable[[Session, dict, ToolCtx], dict]] = {
    "create_finding": _create_finding,
    "create_problem": _create_problem,
    "create_profile_fact": _create_profile_fact,
    "save_day_note": _save_day_note,
    "generate_review": _generate_review,
}


# ── 调度 ────────────────────────────────────────────────────


def run_tool(db: Session, name: str, args: dict, ctx: ToolCtx) -> dict[Any, Any]:
    """执行任意工具（读写皆可）。只给测试与审批后的写路径用。"""
    fn = READ_TOOLS.get(name) or WRITE_TOOL_FNS.get(name)
    if fn is None:
        return {"error": f"未知工具: {name}"}
    try:
        return fn(db, args, ctx)
    except Exception as e:  # noqa: BLE001 - 工具内部异常不该炸掉整轮对话
        db.rollback()
        return {"error": f"工具执行失败: {e}"}


def describe_call(name: str, args: dict) -> str:
    """给审批卡用的人话摘要。"""
    if name == "create_finding":
        return f"写入一条{finding_kind_label(args.get('kind'))}：{args.get('title', '')}"
    if name == "create_problem":
        return f"写入一个长期问题：{args.get('title', '')}"
    if name == "create_profile_fact":
        return (
            f"写入一条背景（{fact_category_label(args.get('category'))}）："
            f"{args.get('content', '')}"
        )
    if name == "save_day_note":
        return f"保存 {args.get('date') or '今天'} 的小结"
    if name == "generate_review":
        return f"生成{period_label(args.get('period_type'))}（{args.get('date') or '今天'}）"
    return name


def finding_kind_label(kind: str | None) -> str:
    return {
        "OBSERVATION": "观察",
        "HYPOTHESIS": "假设",
        "CONCLUSION": "结论",
    }.get(str(kind or "OBSERVATION").upper(), "发现")


def fact_category_label(category: str | None) -> str:
    return {
        "identity": "身份",
        "habit": "习惯",
        "preference": "偏好",
        "context": "情境",
        "constraint": "限制",
    }.get(str(category or "").strip().lower(), str(category or "背景"))


def period_label(period_type: str | None) -> str:
    return {"day": "日复盘", "week": "周复盘", "month": "月复盘"}.get(
        str(period_type or "day").lower(), "复盘"
    )


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
