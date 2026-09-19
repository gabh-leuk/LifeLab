from datetime import date as date_cls
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import AiUserDep, DbDep, UserDep
from app.models.period_review import PeriodReview
from app.models.review import DailyReview
from app.schemas.review import (
    PeriodReviewGenerateRequest,
    PeriodReviewRead,
    ReviewGenerateRequest,
    ReviewIndexItem,
    ReviewRead,
)
from app.services import memory_service, period_review_service, review_service
from app.utils import ensure_aware

router = APIRouter(prefix="/reviews", tags=["reviews"])

# 索引一次最多带这么多篇（日、周/月各自独立上限）。个人自用的量级下够用，
# 又不至于把整张表拖下来。
_INDEX_LIMIT = 60
_SUMMARY_MAX = 200


def _first_meaningful_line(text: str) -> str:
    """跳过 markdown 标题与分隔线，取第一行正文当摘要（去掉 `**` 粗体标记）。"""
    for line in (text or "").splitlines():
        t = line.strip()
        if not t or t.startswith("#") or set(t) <= {"-"}:
            continue
        return t.replace("**", "")
    return ""


def _day_label(date: str) -> str:
    """2026-09-16 → 2026-09-16 周三"""
    try:
        d = date_cls.fromisoformat(date)
    except ValueError:
        return date
    return f"{date} 周{'一二三四五六日'[d.weekday()]}"


def _period_label(row: PeriodReview) -> str:
    """2026-W37 → 2026-W37（09-08 ~ 09-14）"""
    return f"{row.period_key}（{row.period_start} ~ {row.period_end}）"


@router.get("", response_model=list[ReviewIndexItem])
def list_reviews(
    db: DbDep,
    current_user: UserDep,
    limit: int = Query(default=_INDEX_LIMIT, ge=1, le=200, description="日、周/月各自的上限"),
):
    """复盘索引：日/周/月统一成一个列表，按生成时间倒序。

    助手页的「复盘」标签用它浏览历史。stale 复用详情端点同一条计算路径
    （`period_review_service.build_read`），避免列表与详情两处口径漂移。
    """
    items: list[ReviewIndexItem] = []

    for row in db.scalars(
        select(DailyReview)
        .where(DailyReview.user_id == current_user.id)
        .order_by(DailyReview.date.desc())
        .limit(limit)
    ):
        summary = (row.structured or {}).get("day_summary") or _first_meaningful_line(
            row.review_text
        )
        items.append(
            ReviewIndexItem(
                kind="day",
                key=row.date,
                label=_day_label(row.date),
                summary=summary[:_SUMMARY_MAX],
                status=row.status,
                updated_at=ensure_aware(row.updated_at),
            )
        )

    for row in db.scalars(
        select(PeriodReview)
        .where(PeriodReview.user_id == current_user.id)
        .order_by(PeriodReview.period_start.desc())
        .limit(limit)
    ):
        data = period_review_service.build_read(db, row)
        items.append(
            ReviewIndexItem(
                kind=row.period_type,
                key=row.period_key,
                label=_period_label(row),
                period_start=row.period_start,
                summary=_first_meaningful_line(row.review_text)[:_SUMMARY_MAX],
                status=row.status,
                stale=bool(data["stale"]),
                # SQLite 读出的无时区；补成 UTC 才能与另一张表的行一起排序
                updated_at=ensure_aware(row.updated_at),
            )
        )

    items.sort(key=lambda i: (i.updated_at, i.key), reverse=True)
    return items


@router.delete("/{date}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review(date: str, db: DbDep, current_user: UserDep):
    """删除某日复盘（含其记忆索引）。"""
    review = review_service.get_review(db, date, current_user.id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    db.delete(review)
    db.commit()
    memory_service.remove_by_source(db, "review", review.id, user_id=current_user.id)


@router.get("/{date}", response_model=ReviewRead)
def get_review(date: str, db: DbDep, current_user: UserDep):
    review = review_service.get_review(db, date, current_user.id)
    if review is None:
        raise HTTPException(status_code=404, detail="该日复盘尚未生成")
    return review


@router.post("", response_model=ReviewRead)
def generate_review(
    payload: ReviewGenerateRequest,
    db: DbDep,
    current_user: AiUserDep,
    tz_offset: int = Query(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480"),
):
    """生成（或 force 重新生成）某日 AI 复盘。按本地时区聚合当天数据。

    同一次 LLM 调用同时产出 L1 洞察并写入记忆索引。
    消耗真实额度 → demo 账号被 AiUserDep 挡下（见 app/deps.py）。
    """
    return review_service.generate_review(
        db,
        payload.date,
        force=payload.force,
        tz_offset=tz_offset,
        user_id=current_user.id,
    )


# ── 周/月复盘 ──────────────────────────────────────────────


@router.get("/period/{period_type}/{date}", response_model=PeriodReviewRead)
def get_period_review(
    period_type: Literal["week", "month"], date: str, db: DbDep, current_user: UserDep
):
    """查看某周/某月复盘（未生成时 404），附素材数与 stale 标记。"""
    data = period_review_service.get_read(db, period_type, date, current_user.id)
    if data is None:
        raise HTTPException(status_code=404, detail="该期间复盘尚未生成")
    return data


@router.post("/period", response_model=PeriodReviewRead)
def generate_period_review(
    payload: PeriodReviewGenerateRequest, db: DbDep, current_user: AiUserDep
):
    """生成（或 force 重新生成）周/月复盘。

    周输入=本周 L1 洞察+每日复盘摘要+发现；月输入=本月周综述+周模式+周遗漏洞察。
    无素材时不调 LLM，直接返回"素材不足"的说明行。demo 账号被挡下。
    """
    row = period_review_service.generate_period_review(
        db,
        payload.period_type,
        payload.date,
        force=payload.force,
        tz_offset=payload.tz_offset,
        user_id=current_user.id,
    )
    return period_review_service.build_read(db, row)
