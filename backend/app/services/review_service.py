"""Daily Review 生成服务。

流程：汇总当天原始数据（只读）→ 组装 prompt → LLM JSON 输出
→ pydantic 校验 → 生成 markdown 正文 → upsert 入库。
红线：Review 永不修改 Event/Thought/State；推测必须标注。
"""

import json
import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.behavior_categories import category_label
from app.config import get_settings
from app.llm import LLMError, chat_json
from app.models.event import Event
from app.models.review import DailyReview
from app.models.state import StateRecord
from app.models.thought import Thought
from app.schemas.review import ReviewStructured
from app.services import (
    context_service,
    day_hours_service,
    day_note_service,
    experiment_service,
    memory_service,
)
from app.utils import fmt_minutes, parse_day_range

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 LifeLab 的个人行为分析助手。你只分析用户真实记录的数据。

严格遵守：
1. 只描述数据中真实出现的事实，禁止编造不存在的行为/时间。
2. 规律与解释属于「推测」，必须明确标注「(推测)」或写成"可能…"。
3. 不评价人格，不贴标签，语气克制、具体、可执行。
4. 输出必须是合法 JSON，结构如下：
{
  "day_summary": "一两句话总结",
  "highlights": ["值得记录的事"],
  "concerns": ["值得关注的问题"],
  "patterns": ["观察到的规律，注明(推测)"],
  "suggestions": ["明天可执行的 1-3 件事"],
  "insights": ["对用户长期有价值、可复用的规律/偏好/改进建议，每条一句话(<80字)，没有则空数组"]
}
所有数组元素必须是字符串。
5. insights 是给未来检索用的长期记忆：只写脱离当天语境仍成立的总结，
   不要写流水账；有推测必须标注「(推测)」。
6. 若提供了「历史相关记忆/用户背景/追踪中的问题」：
   - 提炼 insights 时不得与历史记忆重复；有新证据或新条件时写成"补充：…"；
   - 发现与历史结论矛盾时，写进 concerns 并明确指出矛盾点，不要静默覆盖。
7. 若提供了「设备真实活动」：它是采集器**真实测量**到的前台应用使用，与人工记录
   同等重要，不是佐证。描述「用了什么、用了多久、什么时段」是事实，可直接陈述。
8. 必须交叉核对设备测量与人工记录：二者矛盾（如已记录入睡、设备却显示深夜仍在用）
   是重点发现，写入 concerns 并点明冲突两侧；记录缺失而设备有显著活动（或反之）
   同样要指出；同类活动不要重复计数（人工记录优先）。
9. 设备的「用了什么/多久/何时」是事实；据此推断用户的**动机、目的、状态**仍是推测，
   须标注「(推测)」。复盘以行为与规律为主，不要输出屏幕使用时长报告。"""


def _day_window(date: str, tz_offset: int = 0) -> tuple[datetime, datetime]:
    start, end = parse_day_range(date, tz_offset)
    return start, end


def _load_day_snapshot(
    db: Session, date: str, *, user_id: int, tz_offset: int = 0
) -> dict:
    """聚合当天数据为可序列化的快照（给 LLM 的上下文 + 追溯存档）。

    与时间线同用本地时区分天，保证复盘统计与时间线一致。
    """
    start, end = _day_window(date, tz_offset)

    events = db.scalars(
        select(Event).where(
            Event.user_id == user_id,
            Event.timestamp >= start,
            Event.timestamp < end,
        )
    ).all()
    thoughts = db.scalars(
        select(Thought).where(
            Thought.user_id == user_id,
            Thought.timestamp >= start,
            Thought.timestamp < end,
        )
    ).all()
    states = db.scalars(
        select(StateRecord).where(
            StateRecord.user_id == user_id,
            StateRecord.timestamp >= start,
            StateRecord.timestamp < end,
        )
    ).all()

    manual_events = [
        e for e in events if e.source != "DEVICE"
    ]

    return {
        "date": date,
        "note": day_note_service.get_note_content(db, date, user_id),
        "events": [
            {
                "time": e.timestamp.isoformat(),
                "type": e.type,
                "category": e.category,
                "note": e.note,
                "ended_at": e.ended_at.isoformat() if e.ended_at else None,
            }
            for e in manual_events
        ],
        # 设备真实活动（按本地小时聚合）——与记忆页时间轴、实验指标读同一处
        "device_hours": _device_hours(db, date, start, end, user_id),
        "thoughts": [
            {"time": t.timestamp.isoformat(), "content": t.content, "tags": t.tags}
            for t in thoughts
        ],
        "states": [
            {
                "time": s.timestamp.isoformat(),
                "energy": s.energy,
                "focus": s.focus,
                "irritation": s.irritation,
            }
            for s in states
        ],
    }


def _device_hours(
    db: Session, date: str, day_start: datetime, day_end: datetime, user_id: int
) -> list[dict]:
    """当天设备数据序列化（供 prompt 渲染 + 追溯存档）。

    口径完全交给 `day_hours_service` —— 复盘不再自己算设备时长，
    否则就成了时间轴 / 实验之外的第三条计算路径（旧实现按整段计时、
    不裁剪到当日，与另两处对不上账）。
    """
    return [
        {
            "hour": h.hour,
            "seconds": h.seconds,
            "category_seconds": h.category_seconds,
            "apps": [a.model_dump() for a in h.apps],
            "activities": [
                {
                    "time": a.start.isoformat(),
                    "ended_at": a.end.isoformat(),
                    "category": a.category,
                    "platform": a.platform,
                    "label": a.label,
                }
                for a in h.activities
            ],
        }
        for h in day_hours_service.build_day_hours(
            db, date, day_start, day_end, user_id=user_id
        )
    ]


def _stats_from_snapshot(snapshot: dict) -> str:
    """给 LLM 的数字摘要（事件次数、状态均值）。"""
    events = snapshot["events"]
    states = snapshot["states"]
    thoughts = snapshot["thoughts"]

    lines = [
        f"共 {len(events)} 次行为记录, {len(thoughts)} 条想法, {len(states)} 次状态快照。"
    ]
    if events:
        by_type: dict[str, int] = {}
        for e in events:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        lines.append("行为次数: " + ", ".join(f"{k}={v}" for k, v in by_type.items()))
    if states:
        n = len(states)
        avg = lambda key: sum(s[key] for s in states) / n
        lines.append(
            f"状态均值(0-5): 精力={avg('energy'):.1f} 注意力={avg('focus'):.1f} 烦躁={avg('irritation'):.1f}"
        )
    return "\n".join(lines)


def _json_pretty(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def _device_block(snapshot: dict) -> str:
    """把「按本地小时聚合的设备数据」渲染成紧凑中文块；无数据返回空串。

    每小时一行：总时长 · 各大类摊销秒数（降序）· 该小时 Top 应用。
    段落明细（`activities`）留在快照里供追溯，不进 prompt —— 逐条罗列会
    与小时行重复，把 prompt 撑长。
    """
    hours = snapshot.get("device_hours") or []
    lines: list[str] = []
    for h in hours:
        cats = "、".join(
            f"{category_label(c)} {fmt_minutes(s)}"
            for c, s in sorted(
                (h.get("category_seconds") or {}).items(),
                key=lambda kv: kv[1],
                reverse=True,
            )
        )
        apps = "、".join(
            f"{a.get('label') or a['app']} {fmt_minutes(a['seconds'])}"
            for a in (h.get("apps") or [])
        )
        parts = [f"总 {fmt_minutes(h['seconds'])}"]
        if cats:
            parts.append(cats)
        if apps:
            parts.append(apps)
        lines.append(f"{h['hour']:02d}:00 " + " · ".join(parts))
    if not lines:
        return ""
    return (
        "【设备真实活动（采集器真实测量，按本地小时聚合，已与人工记录按类别去重）】\n"
        + "\n".join(lines)
        + "\n\n"
    )


def _prompt_user(snapshot: dict, history_block: str = "") -> str:
    note = snapshot.get("note") or ""
    note_block = (
        f"用户手写小结（复盘前写下，作为补充上下文，请结合它分析）：\n{note}\n\n"
        if note
        else ""
    )
    history_section = (
        f"【已有上下文】\n{history_block}\n\n" if history_block else ""
    )
    return (
        f"这是 {snapshot['date']} 的原始记录：\n\n"
        f"{history_section}"
        f"{note_block}"
        f"统计：{_stats_from_snapshot(snapshot)}\n\n"
        f"{_device_block(snapshot)}"
        f"完整事件序列（人工记录）：\n{_json_pretty(snapshot['events'])}\n\n"
        f"想法：\n{_json_pretty(snapshot['thoughts'])}\n\n"
        f"状态快照：\n{_json_pretty(snapshot['states'])}\n\n"
        f"请生成复盘。若当天几乎没有数据，如实说明记录不完整即可。"
    )


def _render_review_text(date: str, s: ReviewStructured) -> str:
    lines = [f"## {date}", ""]
    lines.append(f"**总结**：{s.day_summary}")
    if s.highlights:
        lines += ["", "### 值得记录", *[f"- {h}" for h in s.highlights]]
    if s.concerns:
        lines += ["", "### 值得关注", *[f"- {c}" for c in s.concerns]]
    if s.patterns:
        lines += ["", "### 可能规律（推测）", *[f"- {p}" for p in s.patterns]]
    if s.suggestions:
        lines += ["", "### 下一步建议", *[f"- {g}" for g in s.suggestions]]
    lines += ["", "---", "_由 LLM 生成，仅供反思参考；原始记录未被修改。_"]
    return "\n".join(lines)


def _upsert(db: Session, new: DailyReview, existing: DailyReview | None) -> DailyReview:
    if existing is not None:
        existing.review_text = new.review_text
        existing.structured = new.structured
        existing.source_data = new.source_data
        existing.model = new.model
        existing.status = new.status
        existing.error = new.error
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing
    db.add(new)
    db.commit()
    db.refresh(new)
    return new


def generate_review(
    db: Session,
    date: str,
    *,
    force: bool = False,
    tz_offset: int = 0,
    user_id: int,
) -> DailyReview:
    """生成（或覆盖）某天复盘。数据为空则直接生成"无记录"，不调 LLM。"""
    settings = get_settings()
    existing = db.scalar(
        select(DailyReview).where(
            DailyReview.user_id == user_id, DailyReview.date == date
        )
    )
    # 已成功生成且非强制 → 直接返回；error 记录视为可重试，不挡路
    if existing and existing.status == "ok" and not force:
        return existing

    snapshot = _load_day_snapshot(db, date, user_id=user_id, tz_offset=tz_offset)
    if not any(
        [
            snapshot["events"],
            snapshot["thoughts"],
            snapshot["states"],
            snapshot["device_hours"],
        ]
    ) and not snapshot.get("note"):
        review = DailyReview(
            user_id=user_id,
            date=date,
            review_text=f"## {date}\n\n这一天没有任何记录，无法复盘。",
            structured=None,
            source_data=snapshot,
            model=settings.llm_model or None,
            status="ok",
        )
        review = _upsert(db, review, existing)
        _sync_experiments(db, date, tz_offset, user_id)
        return review

    # 生成前先检索历史上下文（画像/问题/相关记忆），供 prompt 注入与去重。
    query = context_service.extract_search_query(snapshot)
    history = context_service.build_history(
        db, query, exclude_refs={f"insight:{date}"}, user_id=user_id
    )
    snapshot["history"] = context_service.history_meta(history)
    history_block = context_service.render_history(history)

    try:
        raw = chat_json(SYSTEM_PROMPT, _prompt_user(snapshot, history_block), caller="review.daily")
        structured = ReviewStructured.model_validate(raw)
    except (LLMError, ValidationError) as e:
        review = DailyReview(
            user_id=user_id,
            date=date,
            review_text="",
            structured=None,
            source_data=snapshot,
            model=settings.llm_model or None,
            status="error",
            error=str(e),
        )
        return _upsert(db, review, existing)

    review_text = _render_review_text(date, structured)
    review = DailyReview(
        user_id=user_id,
        date=date,
        review_text=review_text,
        structured=raw,
        source_data=snapshot,
        model=settings.llm_model or None,
        status="ok",
    )
    review = _upsert(db, review, existing)

    # 同一次 LLM 调用产出的 L1 洞察随复盘一并入库（replace 语义：
    # 重新生成时旧洞察被覆盖，避免旧结论残留、也不再单独调一次 LLM）。
    try:
        memory_service.replace_insights(db, date, structured.insights, user_id=user_id)
    except Exception:
        logger.warning("L1 洞察写入失败: date=%s", date, exc_info=True)

    # 复盘完成 = 当天数据沉淀时点：把对应数据同步到覆盖该日的实验
    #（AUTO 数据点幂等重算，MANUAL 点不受影响）。
    _sync_experiments(db, date, tz_offset, user_id)
    return review


def _sync_experiments(db: Session, date: str, tz_offset: int, user_id: int) -> None:
    """复盘时把当天数据写入覆盖该日的实验（失败不影响复盘本身）。"""
    try:
        experiment_service.aggregate_day(
            db, date, user_id=user_id, tz_offset=tz_offset
        )
    except Exception:
        logger.warning("实验数据自动同步失败: date=%s", date, exc_info=True)


def get_review(
    db: Session, date: str, user_id: int
) -> DailyReview | None:
    return db.scalar(
        select(DailyReview).where(
            DailyReview.user_id == user_id, DailyReview.date == date
        )
    )
