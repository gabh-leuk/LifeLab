from fastapi import APIRouter, HTTPException, Query

from app.deps import AiUserDep, DbDep, UserDep
from app.schemas.memory import (
    MemoryAnswerSource,
    MemoryAskRequest,
    MemoryAskResponse,
    MemoryDebugHit,
    MemoryDebugResponse,
    MemoryExtractRequest,
    MemoryExtractResponse,
    MemoryItemCreate,
    MemoryItemRead,
    MemoryPatternExtractRequest,
    MemoryPatternExtractResponse,
    MemorySearchDebugRequest,
    MemorySearchHit,
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryStatsResponse,
)
from app.services import memory_service

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/items", response_model=MemoryItemRead, status_code=201)
def create_memory_item(payload: MemoryItemCreate, db: DbDep, current_user: AiUserDep):
    """手工写一条记忆（embed=True 时顺带向量化）。

    demo 挡下：这是 AI 产物的写入端口，封住它，「每日重置保留 AI 产物」才严密
    —— 否则访客能往保留表里塞东西，重置也清不掉。
    """
    return memory_service.store_memory(
        db,
        kind=payload.kind,
        content=payload.content,
        source_ref=payload.source_ref,
        embed_vectors=payload.embed,
        user_id=current_user.id,
    )


@router.get("/items", response_model=list[MemoryItemRead])
def list_memory_items(
    db: DbDep,
    current_user: UserDep,
    limit: int = Query(default=50, ge=1, le=200),
    kind: str | None = Query(default=None),
    source_ref: str | None = Query(default=None, max_length=255),
):
    return memory_service.list_memories(
        db, limit=limit, kind=kind, source_ref=source_ref, user_id=current_user.id
    )


@router.post("/search", response_model=MemorySearchResponse)
def search_memory(payload: MemorySearchRequest, db: DbDep, current_user: UserDep):
    hits = memory_service.search_memory(
        db,
        payload.query,
        top_k=payload.top_k,
        kind=payload.kind,
        min_similarity=payload.min_similarity,
        user_id=current_user.id,
    )
    return MemorySearchResponse(
        query=payload.query,
        hits=[
            MemorySearchHit(
                item=h.item,
                similarity=h.similarity,
                final_score=h.final_score,
                anchor_date=h.anchor_date.isoformat() if h.anchor_date else None,
            )
            for h in hits
        ],
    )


@router.post("/search/debug", response_model=MemoryDebugResponse)
def search_memory_debug(
    payload: MemorySearchDebugRequest, db: DbDep, current_user: UserDep
):
    """检索诊断：全部候选 + 相似度/新鲜度/最终分与门槛标记（可视化调参用）。"""
    result = memory_service.search_debug(
        db,
        payload.query,
        kind=payload.kind,
        limit=payload.limit,
        min_similarity=payload.min_similarity,
        time_weight=payload.time_weight,
        user_id=current_user.id,
    )
    threshold = result["threshold"]
    return MemoryDebugResponse(
        query=result["query"],
        threshold=threshold,
        time_weight=result["time_weight"],
        mode=result["mode"],
        hits=[
            MemoryDebugHit(
                item=h.item,
                similarity=h.similarity,
                final_score=h.final_score,
                anchor_date=h.anchor_date.isoformat() if h.anchor_date else None,
                recency=h.recency,
                half_life_days=h.half_life_days,
                passed_threshold=h.similarity >= threshold,
            )
            for h in result["hits"]
        ],
    )


@router.get("/stats", response_model=MemoryStatsResponse)
def memory_stats(db: DbDep, current_user: UserDep):
    """记忆库状态：条目/向量覆盖/来源表体量（前端可视化）。"""
    return memory_service.memory_stats(db, current_user.id)


@router.post("/sync", status_code=200)
def sync_memories(db: DbDep, current_user: AiUserDep):
    """把复盘/发现的最新内容同步为记忆索引（幂等）。

    demo 挡下：同上，会写入保留表 memory_items。
    """
    added = memory_service.sync_from_sources(db, current_user.id)
    return {"synced": added}


@router.post("/extract", response_model=MemoryExtractResponse)
def extract_memories(
    payload: MemoryExtractRequest, db: DbDep, current_user: AiUserDep
):
    """从某天记录提炼长期洞察并写入记忆（幂等）。消耗真实额度 → demo 挡下。"""
    insights = memory_service.extract_insights(
        db, payload.date, tz_offset=payload.tz_offset, user_id=current_user.id
    )
    return MemoryExtractResponse(date=payload.date, insights=insights)


@router.get("/extract/{date}", response_model=MemoryExtractResponse)
def get_extracted_memories(date: str, db: DbDep, current_user: UserDep):
    """查看某天已提炼的洞察（未提炼时 skipped=True）。

    纯读 memory_items，不碰 LLM → 不封。访客要能翻看预置的 33 条记忆。
    """
    existing = memory_service.list_memories(
        db,
        kind="insight",
        source_ref=f"insight:{date}",
        user_id=current_user.id,
    )
    return MemoryExtractResponse(date=date, insights=existing, skipped=not existing)


@router.post("/extract-week", response_model=MemoryPatternExtractResponse)
def extract_week_patterns(
    payload: MemoryPatternExtractRequest, db: DbDep, current_user: AiUserDep
):
    """L2 周提炼：从某 ISO 周内的洞察/发现/复盘提炼长期行为模式（幂等）。

    消耗真实额度 → demo 挡下。
    """
    week, monday, sunday, patterns = memory_service.extract_patterns(
        db,
        payload.date,
        tz_offset=payload.tz_offset,
        force=payload.force,
        user_id=current_user.id,
    )
    return MemoryPatternExtractResponse(
        week=week, week_start=monday, week_end=sunday,
        patterns=patterns, skipped=not patterns,
    )


@router.get("/extract-week/{date}", response_model=MemoryPatternExtractResponse)
def get_week_patterns(date: str, db: DbDep, current_user: UserDep):
    """查看某天所在 ISO 周已提炼的模式（未提炼时 skipped=True）。

    纯读 memory_items，不碰 LLM → 不封。
    """
    try:
        monday, sunday, week = memory_service.iso_week_bounds(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    existing = memory_service.get_patterns(db, week, current_user.id)
    return MemoryPatternExtractResponse(
        week=week, week_start=monday, week_end=sunday,
        patterns=existing, skipped=not existing,
    )


@router.post("/ask", response_model=MemoryAskResponse)
def ask_memory(payload: MemoryAskRequest, db: DbDep, current_user: AiUserDep):
    """记忆问答：LLM 基于召回的历史记忆归纳回答（带可追溯来源）。

    消耗真实额度 → demo 挡下。检索本身（POST /memory/search）不封：
    只走本地 Ollama 向量化，零外部成本。
    """
    answer, sources = memory_service.ask_memory(
        db,
        payload.question,
        top_k=payload.top_k,
        kind=payload.kind,
        user_id=current_user.id,
    )
    return MemoryAskResponse(
        question=payload.question,
        answer=answer,
        sources=[
            MemoryAnswerSource(
                id=h.item.id,
                kind=h.item.kind,
                content=h.item.content,
                source_ref=h.item.source_ref,
                similarity=h.similarity,
                final_score=h.final_score,
                anchor_date=h.anchor_date.isoformat() if h.anchor_date else None,
            )
            for h in sources
        ],
    )