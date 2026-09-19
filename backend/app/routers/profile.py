import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.deps import AiUserDep, DbDep, UserDep
from app.schemas.profile import (
    ProfileFactCreate,
    ProfileFactRead,
    ProfileFactUpdate,
    PromotionCommitRequest,
    PromotionCommitResponse,
    PromotionScanResponse,
)
from app.services import profile_promotion_service, profile_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/facts", response_model=list[ProfileFactRead])
def list_profile_facts(
    db: DbDep,
    current_user: UserDep,
    include_archived: bool = Query(default=False),
):
    """列出用户背景（默认只看 active；archived 保留可查）。"""
    return profile_service.list_facts(
        db, include_archived=include_archived, user_id=current_user.id
    )


@router.post("/facts", response_model=ProfileFactRead, status_code=status.HTTP_201_CREATED)
def create_profile_fact(payload: ProfileFactCreate, db: DbDep, current_user: UserDep):
    """用户手写背景 = 已确认。重复/超配额返回 409。"""
    try:
        return profile_service.create_fact(
            db,
            category=payload.category,
            content=payload.content,
            source="manual",
            source_ref=payload.source_ref,
            user_id=current_user.id,
        )
    except profile_service.ProfileError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.patch("/facts/{fact_id}", response_model=ProfileFactRead)
def update_profile_fact(
    fact_id: int, payload: ProfileFactUpdate, db: DbDep, current_user: UserDep
):
    """编辑（= 再次确认）/ 归档（status=archived）。"""
    try:
        fact = profile_service.update_fact(
            db,
            fact_id,
            category=payload.category,
            content=payload.content,
            status=payload.status,
            user_id=current_user.id,
        )
    except profile_service.ProfileError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if fact is None:
        raise HTTPException(status_code=404, detail="profile fact not found")
    return fact


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile_fact(fact_id: int, db: DbDep, current_user: UserDep):
    """显式删除（真删；归档请用 PATCH status=archived）。"""
    if not profile_service.delete_fact(db, fact_id, current_user.id):
        raise HTTPException(status_code=404, detail="profile fact not found")


# ── 画像晋升（跨周/跨天复现 → 候选 → 用户确认写入） ─────────


@router.post("/promotions/scan", response_model=PromotionScanResponse)
def scan_promotions(
    db: DbDep, current_user: AiUserDep, refine: bool = Query(default=True)
):
    """扫描跨周/跨天复现的模式与洞察，生成晋升候选（只返回，不落库）。

    refine 会调 LLM 精炼 → demo 挡下。commit 不封：纯写库。
    """
    return profile_promotion_service.scan_promotions(
        db, refine=refine, user_id=current_user.id
    )


@router.post("/promotions/commit", response_model=PromotionCommitResponse)
def commit_promotions(
    payload: PromotionCommitRequest, db: DbDep, current_user: UserDep
):
    """用户确认的候选写入用户背景（source=ai，重复/超配额跳过）。"""
    fact_ids: list[int] = []
    skipped = 0
    for candidate in payload.candidates:
        try:
            fact = profile_service.create_fact(
                db,
                category=candidate.category,
                content=candidate.content,
                source="ai",
                source_ref=candidate.source_ref,
                confidence=candidate.confidence,
                confirmed=True,
                user_id=current_user.id,
            )
            fact_ids.append(fact.id)
        except profile_service.ProfileError as e:
            logger.info("晋升候选跳过：%s", e)
            skipped += 1
    return PromotionCommitResponse(fact_ids=fact_ids, skipped=skipped)
