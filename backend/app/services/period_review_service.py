"""周/月复盘服务：漏斗式降本，输入只读上一层浓缩产物。

调用预算（活跃用户）：日 1 次/天（复盘+L1 合并）→ 周 1 次/周 → 月 1 次/月。
- 周输入：本周 L1 洞察 + 每日复盘摘要 + 发现 + 每日设备大类合计（走 `day_hours_service`）
- 月输入：本月周综述 + 周模式 + 周遗漏洞察 + 周设备合计（读周产物，不重算）
无素材时确定性跳过、不花 token；素材更新只标 stale，不自动重跑。
"""

import logging
from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.behavior_categories import category_label
from app.config import get_settings
from app.llm import LLMError, chat_json
from app.models.finding import Finding
from app.models.memory import MemoryItem
from app.models.period_review import PeriodReview
from app.models.review import DailyReview
from app.schemas.review import PeriodStructured
from app.services import (
    context_service,
    day_hours_service,
    experiment_service,
    memory_service,
)
from app.utils import fmt_minutes, parse_day_range

logger = logging.getLogger(__name__)

WEEK_SYSTEM_PROMPT = """你是 LifeLab 的长期行为模式分析师。给你某一周用户积累的
「日洞察（L1）」「每日复盘摘要」与「发现」，请做周复盘：总结本周、提炼跨日模式、
并补上日提炼遗漏、但值得长期保留的洞察。

规则：
1. 模式必须是跨天反复出现的倾向/循环/触发器（如"压力大时倾向熬夜"）。
2. 只基于提供的材料；禁止编造。材料不足时宁可少给。
3. 严格区分事实与推测：推测必须标注「(推测)」。
4. 输出合法 JSON：
{
  "period_summary": "本周综述，两三句话",
  "patterns": ["跨日模式，每条<100字"],
  "missed_insights": ["日提炼遗漏的长期洞察，每条<80字；没有则空数组"],
  "suggestions": ["下周建议 1-3 条"]
}
所有数组元素必须是字符串。仅以上字段。
5. 若提供了「历史相关记忆/用户背景/追踪中的问题」：
   - 指出本周与前几周的延续或反转（如"连续第 N 周出现"），不要重复已有模式；
   - 与历史结论矛盾时明确指出，不要静默覆盖。
6. 每日摘要里的「设备：…」是采集器**真实测量**到的设备使用（事实），与人工记录同等重要。
   必须交叉核对：设备显示的活动与当天记录矛盾（如已记录早睡、设备却显示深夜仍在使用）
   是重点发现，写进 patterns 并点明冲突两侧；不要输出屏幕使用时长报告。"""

MONTH_SYSTEM_PROMPT = """你是 LifeLab 的长期行为模式分析师。给你某个月各周的
「周综述」「周模式」「周遗漏洞察」，请做月复盘：总结本月走势、提炼跨周模式、
并补上周提炼遗漏、但值得长期保留的洞察。

规则：
1. 跨周模式指持续数周的倾向/趋势（如"整月学习时间逐周下降(推测)"）。
2. 只基于提供的材料；禁止编造。材料不足时宁可少给。
3. 严格区分事实与推测：推测必须标注「(推测)」。
4. 输出合法 JSON：
{
  "period_summary": "本月综述，两三句话",
  "patterns": ["跨周模式，每条<100字"],
  "missed_insights": ["周提炼遗漏的长期洞察，每条<80字；没有则空数组"],
  "suggestions": ["下月建议 1-3 条"]
}
所有数组元素必须是字符串。仅以上字段。
5. 若提供了「历史相关记忆/用户背景/追踪中的问题」：
   - 指出本月与更早模式的延续或反转，不要重复已有模式；
   - 与历史结论矛盾时明确指出，不要静默覆盖。
6. 各周的「周设备合计：…」是采集器**真实测量**到的设备使用（事实）。必须与周记录
   交叉核对：某周设备使用与记录描述矛盾时，写进 patterns 并点明冲突两侧；
   不要输出屏幕使用时长报告。"""


# ── 期间计算 ───────────────────────────────────────────────


def month_bounds(date: str) -> tuple[str, str]:
    """任意一天 → 所在自然月的 (首日, 末日)，格式 YYYY-MM-DD。"""
    try:
        d = date_cls.fromisoformat(date)
    except ValueError as e:
        raise ValueError(f"date must be YYYY-MM-DD: {date}") from e
    start = d.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1) - timedelta(days=1)
    else:
        end = start.replace(month=start.month + 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def period_bounds(period_type: str, date: str) -> tuple[str, str, str]:
    """(period_key, start, end)。week 走 ISO 周，month 走自然月。"""
    if period_type == "week":
        monday, sunday, week = memory_service.iso_week_bounds(date)
        return week, monday, sunday
    if period_type == "month":
        start, end = month_bounds(date)
        return start[:7], start, end
    raise ValueError(f"period_type must be week|month: {period_type}")


# ── 设备合计渲染 ───────────────────────────────────────────


def _device_text(totals: dict[str, int], top: int = 4) -> str:
    """大类秒数 → `学习3h 视频40分`（按秒数降序，最多 top 个大类）。"""
    items = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return " ".join(f"{category_label(c)} {fmt_minutes(s)}" for c, s in items)


# ── 素材收集（只读下层产物） ────────────────────────────────


def _week_fragments(
    db: Session, monday: str, sunday: str, tz_offset: int, user_id: int
) -> tuple[list[str], dict]:
    """周输入：L1 洞察 + 每日复盘结构化摘要 + 发现。"""
    day_refs = [f"insight:{d}" for d in memory_service.day_strings(monday, sunday)]
    insights = db.scalars(
        select(MemoryItem).where(
            MemoryItem.user_id == user_id,
            MemoryItem.kind == "insight",
            MemoryItem.source_ref.in_(day_refs),
        )
    ).all()
    reviews = db.scalars(
        select(DailyReview).where(
            DailyReview.user_id == user_id,
            DailyReview.date >= monday,
            DailyReview.date <= sunday,
            DailyReview.status == "ok",
        )
    ).all()
    week_start, _ = parse_day_range(monday, tz_offset)
    _, week_end = parse_day_range(sunday, tz_offset)
    findings = db.scalars(
        select(Finding).where(
            Finding.user_id == user_id,
            Finding.created_at >= week_start,
            Finding.created_at < week_end,
        )
    ).all()

    fragments: list[str] = []
    insight_lines = [f"- {i.content}" for i in insights if i.content.strip()]
    if insight_lines:
        fragments.append("本周日洞察（L1）：\n" + "\n".join(insight_lines))

    # 设备测量：折进当天那行，让「记录」与「设备」同行出现——交叉核对正需要这种相邻。
    # 口径走 day_hours_service（与时间轴/实验/日复盘同一处），不再自算。
    device_by_day: dict[str, dict[str, int]] = {}
    week_device: dict[str, int] = defaultdict(int)
    for d in memory_service.day_strings(monday, sunday):
        totals = day_hours_service.category_totals_for_day(
            db, d, tz_offset=tz_offset, user_id=user_id
        )
        if totals:
            device_by_day[d] = totals
            for category, seconds in totals.items():
                week_device[category] += seconds

    review_dates: set[str] = set()
    review_lines: list[str] = []
    for r in reviews:
        review_dates.add(r.date)
        s = r.structured or {}
        bits = []
        if s.get("day_summary"):
            bits.append(f"总结：{s['day_summary']}")
        if s.get("patterns"):
            bits.append("规律：" + "；".join(s["patterns"]))
        if s.get("concerns"):
            bits.append("关注：" + "；".join(s["concerns"]))
        if device_by_day.get(r.date):
            bits.append("设备：" + _device_text(device_by_day[r.date]))
        if bits:
            review_lines.append(f"- {r.date} " + "｜".join(bits))
    # 有设备活动却没做日复盘的日子单独出一行，否则那天的测量在周复盘里完全不可见
    for d in sorted(set(device_by_day) - review_dates):
        review_lines.append(f"- {d} 设备：{_device_text(device_by_day[d])}")
    if review_lines:
        fragments.append("本周每日摘要与设备测量：\n" + "\n".join(review_lines))

    finding_lines = [
        f"- [{f.kind}] {f.title}: {f.observation}"
        + (f"（推测：{f.interpretation}）" if f.interpretation else "")
        for f in findings
        if f.observation and f.observation.strip()
    ]
    if finding_lines:
        fragments.append("本周发现：\n" + "\n".join(finding_lines))

    source_data = {
        "insights": [i.content for i in insights],
        "reviews": [r.date for r in reviews],
        "review_summaries": [
            (r.structured or {}).get("day_summary") or "" for r in reviews
        ],
        "findings": [f.id for f in findings],
        # 周设备合计：月复盘只读周产物，靠这个键把设备层带上去，不必重算 31 天
        "device_summary": dict(week_device) or None,
    }
    return fragments, source_data


def _month_fragments(
    db: Session, start: str, end: str, user_id: int
) -> tuple[list[str], dict]:
    """月输入：与本月重叠的周复盘（周综述+周模式+周遗漏洞察）。"""
    weeks = db.scalars(
        select(PeriodReview)
        .where(
            PeriodReview.user_id == user_id,
            PeriodReview.period_type == "week",
            PeriodReview.period_start <= end,
            PeriodReview.period_end >= start,
            PeriodReview.status == "ok",
        )
        .order_by(PeriodReview.period_start)
    ).all()

    fragments: list[str] = []
    week_keys: list[str] = []
    week_starts: list[str] = []
    summaries: list[str] = []
    for w in weeks:
        week_keys.append(w.period_key)
        week_starts.append(w.period_start)
        if (w.structured or {}).get("period_summary"):
            summaries.append(str(w.structured["period_summary"]))
        patterns = db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.kind == "pattern",
                MemoryItem.source_ref == f"pattern:{w.period_key}",
            )
        ).all()
        misseds = db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.kind == "insight",
                MemoryItem.source_ref == f"insight:{w.period_key}",
            )
        ).all()
        lines = [f"【{w.period_key}（{w.period_start} ~ {w.period_end}）】"]
        if w.review_text:
            lines.append(w.review_text.strip())
        if patterns:
            lines.append("周模式：" + "；".join(p.content for p in patterns))
        if misseds:
            lines.append("周遗漏洞察：" + "；".join(m.content for m in misseds))
        # 设备层已由周复盘折成合计写进它的 source_data；月不重算，直接读
        device_summary = (w.source_data or {}).get("device_summary") or {}
        if device_summary:
            lines.append("周设备合计：" + _device_text(device_summary, top=6))
        fragments.append("\n".join(lines))

    return fragments, {
        "weeks": week_keys,
        "week_starts": week_starts,
        "summaries": summaries,
    }


# ── 素材新鲜度（无 flag 字段，按时间戳推导） ────────────────


def _as_naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _newer(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return False
    return _as_naive(a) > _as_naive(b)


def _material_freshness(
    db: Session,
    period_type: str,
    start: str,
    end: str,
    *,
    user_id: int,
    tz_offset: int = 0,
) -> tuple[int, datetime | None]:
    """(素材条数, 最新素材时间)。不去重，用于 stale 标记与展示。"""
    latest: datetime | None = None
    count = 0
    if period_type == "week":
        day_refs = [
            f"insight:{d}" for d in memory_service.day_strings(start, end)
        ]
        extras = [
            (i.created_at, 1)
            for i in db.scalars(
                select(MemoryItem).where(
                    MemoryItem.user_id == user_id,
                    MemoryItem.kind == "insight",
                    MemoryItem.source_ref.in_(day_refs),
                )
            ).all()
        ]
        extras += [
            (r.updated_at, 1)
            for r in db.scalars(
                select(DailyReview).where(
                    DailyReview.user_id == user_id,
                    DailyReview.date >= start,
                    DailyReview.date <= end,
                )
            ).all()
        ]
        week_start, _ = parse_day_range(start, tz_offset)
        _, week_end = parse_day_range(end, tz_offset)
        extras += [
            (f.created_at, 1)
            for f in db.scalars(
                select(Finding).where(
                    Finding.user_id == user_id,
                    Finding.created_at >= week_start,
                    Finding.created_at < week_end,
                )
            ).all()
        ]
    else:
        extras = [
            (w.updated_at, 1)
            for w in db.scalars(
                select(PeriodReview).where(
                    PeriodReview.user_id == user_id,
                    PeriodReview.period_type == "week",
                    PeriodReview.period_start <= end,
                    PeriodReview.period_end >= start,
                )
            ).all()
        ]

    for ts, n in extras:
        count += n
        if latest is None or _newer(ts, latest):
            latest = ts
    return count, latest


# ── 生成 ───────────────────────────────────────────────────


def get_period_review(
    db: Session, period_type: str, period_key: str, user_id: int
) -> PeriodReview | None:
    return db.scalar(
        select(PeriodReview).where(
            PeriodReview.user_id == user_id,
            PeriodReview.period_type == period_type,
            PeriodReview.period_key == period_key,
        )
    )


def _upsert(
    db: Session,
    *,
    period_type: str,
    period_key: str,
    period_start: str,
    period_end: str,
    review_text: str,
    structured: dict | None,
    source_data: dict | None,
    status: str,
    user_id: int,
    error: str | None = None,
) -> PeriodReview:
    settings = get_settings()
    existing = get_period_review(db, period_type, period_key, user_id)
    if existing is not None:
        existing.period_start = period_start
        existing.period_end = period_end
        existing.review_text = review_text
        existing.structured = structured
        existing.source_data = source_data
        existing.model = settings.llm_model or None
        existing.status = status
        existing.error = error
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing
    row = PeriodReview(
        user_id=user_id,
        period_type=period_type,
        period_key=period_key,
        period_start=period_start,
        period_end=period_end,
        review_text=review_text,
        structured=structured,
        source_data=source_data,
        model=settings.llm_model or None,
        status=status,
        error=error,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _render(
    period_type: str, key: str, start: str, end: str, s: PeriodStructured
) -> str:
    label = "周复盘" if period_type == "week" else "月复盘"
    lines = [f"## {key} {label}（{start} ~ {end}）", ""]
    lines.append(f"**总结**：{s.period_summary}")
    if s.patterns:
        lines += ["", "### 模式（推测已标注）", *[f"- {p}" for p in s.patterns]]
    if s.missed_insights:
        lines += ["", "### 补充洞察", *[f"- {m}" for m in s.missed_insights]]
    if s.suggestions:
        lines += ["", "### 建议", *[f"- {g}" for g in s.suggestions]]
    lines += ["", "---", "_由 LLM 生成，仅供反思参考；原始记录未被修改。_"]
    return "\n".join(lines)


def generate_period_review(
    db: Session,
    period_type: str,
    date: str,
    *,
    force: bool = False,
    tz_offset: int = 0,
    user_id: int,
) -> PeriodReview:
    """生成（或覆盖）周/月复盘。无素材直接存"素材不足"，不调 LLM。

    月复盘的素材门槛：至少 2 个已完成的周复盘（force 时允许 1 个，便于查看月内进度）。
    """
    key, start, end = period_bounds(period_type, date)
    existing = get_period_review(db, period_type, key, user_id)
    # "素材不足"的占位行（structured=None）不算已生成，允许再次尝试（仍不花 token）
    if (
        existing
        and existing.status == "ok"
        and existing.structured is not None
        and not force
    ):
        return existing

    if period_type == "week":
        fragments, source_data = _week_fragments(db, start, end, tz_offset, user_id)
        has_material = bool(
            source_data["insights"] or source_data["reviews"] or source_data["findings"]
        )
        system = WEEK_SYSTEM_PROMPT
        label = "本周"
    else:
        fragments, source_data = _month_fragments(db, start, end, user_id)
        weeks = source_data["weeks"]
        has_material = len(weeks) >= (1 if force else 2)
        system = MONTH_SYSTEM_PROMPT
        label = "本月"
    source_data["tz_offset"] = tz_offset

    if not has_material:
        tip = (
            f"## {key}\n\n{label}还没有可分析的素材"
            "（先做每日复盘并提炼洞察，再做周复盘；月复盘需要至少两个已完成的周复盘）。"
        )
        return _upsert(
            db,
            period_type=period_type,
            period_key=key,
            period_start=start,
            period_end=end,
            review_text=tip,
            structured=None,
            source_data=source_data,
            status="ok",
            user_id=user_id,
        )

    # 历史上下文（画像/问题/**更早期间**的相关记忆）；查询用浓缩材料直接拼接，不额外调 LLM。
    # 本期素材已经全在 prompt 里，召回它等于让它复读 —— 所以池子在候选阶段就限定为
    # 「内容日期早于本期」，并按本复盘自身的粒度（周复盘按周、月复盘按月）给每期限额。
    if period_type == "week":
        query_texts = list(source_data.get("insights") or [])
        query_texts += list(source_data.get("review_summaries") or [])
        material_start = start
        granularity = "week"
    else:
        query_texts = list(source_data.get("summaries") or [])
        # 月复盘的素材含跨月的整周：边界取最早那一周的周一，否则那周的 pattern/insight
        # （内容属于本月素材，锚点却落在上月）会被当成"更早期间"召回来
        material_start = min(source_data.get("week_starts") or [start])
        granularity = "month"
    query = context_service.context_query_from_texts(query_texts)
    history = context_service.build_history(
        db,
        query,
        before=date_cls.fromisoformat(material_start),
        granularity=granularity,
        user_id=user_id,
    )
    source_data["history"] = context_service.history_meta(history)
    history_block = context_service.render_history(history)

    user_prompt = (
        f"期间：{start} ~ {end}（{key}）\n\n"
        + (f"【已有上下文】\n{history_block}\n\n" if history_block else "")
        + "素材：\n"
        + "\n\n".join(fragments)[:20000]
    )
    try:
        raw = chat_json(system, user_prompt, caller=f"review.{period_type}")
        structured = PeriodStructured.model_validate(raw)
    except (LLMError, ValidationError) as e:
        logger.warning("%s 复盘生成失败: %s", period_type, e)
        return _upsert(
            db,
            period_type=period_type,
            period_key=key,
            period_start=start,
            period_end=end,
            review_text="",
            structured=None,
            source_data=source_data,
            status="error",
            error=str(e),
            user_id=user_id,
        )

    review_text = _render(period_type, key, start, end, structured)
    row = _upsert(
        db,
        period_type=period_type,
        period_key=key,
        period_start=start,
        period_end=end,
        review_text=review_text,
        structured=raw,
        source_data=source_data,
        status="ok",
        user_id=user_id,
    )

    # 模式与补充洞察进记忆索引（replace 语义，重新生成不残留）
    try:
        memory_service.replace_by_ref(
            db,
            kind="pattern",
            source_ref=f"pattern:{key}",
            contents=structured.patterns,
            user_id=user_id,
        )
        memory_service.replace_by_ref(
            db,
            kind="insight",
            source_ref=f"insight:{key}",
            contents=structured.missed_insights,
            user_id=user_id,
        )
    except Exception:
        logger.warning("周期提炼写入记忆失败: key=%s", key, exc_info=True)

    # 复盘完成 = 数据沉淀时点：把期间每一天的数据同步到覆盖该日的实验
    #（AUTO 数据点幂等重算；周最多 7 天、月最多 31 天）。
    try:
        for d in memory_service.day_strings(start, end):
            experiment_service.aggregate_day(
                db, d, user_id=user_id, tz_offset=tz_offset
            )
    except Exception:
        logger.warning("实验数据自动同步失败: key=%s", key, exc_info=True)
    return row


def build_read(db: Session, row: PeriodReview) -> dict:
    """给 PeriodReviewRead 补 stale / material_count（非 ORM 字段）。"""
    tz_offset = int((row.source_data or {}).get("tz_offset", 0) or 0)
    count, latest = _material_freshness(
        db,
        row.period_type,
        row.period_start,
        row.period_end,
        user_id=row.user_id,
        tz_offset=tz_offset,
    )
    return {
        "id": row.id,
        "user_id": row.user_id,
        "period_type": row.period_type,
        "period_key": row.period_key,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "review_text": row.review_text,
        "structured": row.structured,
        "source_data": row.source_data,
        "model": row.model,
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "stale": _newer(latest, row.updated_at),
        "material_count": count,
    }


def get_read(
    db: Session, period_type: str, date: str, user_id: int
) -> dict | None:
    key, _, _ = period_bounds(period_type, date)
    row = get_period_review(db, period_type, key, user_id)
    if row is None:
        return None
    return build_read(db, row)
