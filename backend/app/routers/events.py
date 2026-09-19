from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import DbDep, UserDep
from app.models.event import Event
from app.schemas.event import (
    EventCreate,
    EventEndRequest,
    EventRead,
    EventUpdate,
    RecordStatsResponse,
)
from app.services import event_service, memory_service
from app.services.event_type_service import resolve_event_type_category
from app.utils import ensure_aware, local_today, parse_day_range

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=EventRead, status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, db: DbDep, current_user: UserDep):
    return event_service.create_event(
        db,
        user_id=current_user.id,
        type=payload.type,
        category=payload.category,
        note=payload.note,
        tags=payload.tags,
        timestamp=payload.timestamp,
        source=payload.source,
        confidence=payload.confidence,
    )


@router.get("", response_model=list[EventRead])
def list_events(
    db: DbDep,
    current_user: UserDep,
    date: str | None = Query(default=None, description="YYYY-MM-DD，按日过滤"),
    tz_offset: int = Query(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(Event)
        .where(Event.user_id == current_user.id)
        .order_by(Event.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    if date:
        day_start, day_end = parse_day_range(date, tz_offset)
        stmt = stmt.where(Event.timestamp >= day_start, Event.timestamp < day_end)
    return db.scalars(stmt).all()


# 必须声明在 /{event_id} 之前，否则 "record-stats" 会被当成 event_id 去转 int（422）
@router.get("/record-stats", response_model=RecordStatsResponse)
def record_stats(
    db: DbDep,
    current_user: UserDep,
    date: str | None = Query(default=None, description="YYYY-MM-DD，默认服务器本地今天"),
    tz_offset: int = Query(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480"),
):
    """某日的记录条数与连续记录天数（记录页的即时回报，非 AI 的确定数字）。

    与 `/ingest/record-stats` 共用 `event_service.record_stats` —— 两处口径必须
    一致，否则手机记的和网页上看到的不是同一个数。
    """
    day = date or local_today()
    return RecordStatsResponse(
        date=day,
        **event_service.record_stats(
            db, user_id=current_user.id, date=day, tz_offset=tz_offset
        ),
    )


def _get_or_404(db: Session, event_id: int, user_id: int) -> Event:
    event = db.scalar(
        select(Event).where(Event.id == event_id, Event.user_id == user_id)
    )
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@router.get("/{event_id}", response_model=EventRead)
def get_event(event_id: int, db: DbDep, current_user: UserDep):
    return _get_or_404(db, event_id, current_user.id)


@router.patch("/{event_id}/end", response_model=EventRead)
def end_event(event_id: int, payload: EventEndRequest, db: DbDep, current_user: UserDep):
    """用户显式结束某事件。写 ended_at；不传则用服务器当前时间。"""
    event = _get_or_404(db, event_id, current_user.id)
    ended = payload.ended_at
    if ended is None:
        ended = datetime.now(timezone.utc)
    start = ensure_aware(event.timestamp)
    if ended < start:
        raise HTTPException(
            status_code=422, detail="ended_at must be after event start"
        )
    event.ended_at = ended
    db.commit()
    db.refresh(event)
    return event


@router.patch("/{event_id}", response_model=EventRead)
def update_event(event_id: int, payload: EventUpdate, db: DbDep, current_user: UserDep):
    """就地修订一条记录（补备注/加标签/改类型）。

    只改显式传入的字段；改 `type` 时同步重算 `category`，除非同时显式给了 `category`。
    """
    event = _get_or_404(db, event_id, current_user.id)
    data = payload.model_dump(exclude_unset=True)

    if "type" in data:
        key = data["type"]
        if not key:
            raise HTTPException(status_code=422, detail="type 不能为空")
        resolved = resolve_event_type_category(db, key, user_id=current_user.id)
        if resolved is None:
            raise HTTPException(status_code=422, detail=f"未知的事件类型 {key}")
        event.type = key
        if "category" not in data:
            event.category = resolved

    if "category" in data:
        event.category = data["category"]
    if "note" in data:
        event.note = data["note"]
    if "tags" in data:
        tags = data["tags"]
        event.tags_raw = ",".join(tags) if tags else None

    db.commit()
    db.refresh(event)
    return event


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: int, db: DbDep, current_user: UserDep):
    """删除一条事件记录（补记错删/不再需要时用）。级联清记忆索引。"""
    event = _get_or_404(db, event_id, current_user.id)
    db.delete(event)
    db.commit()
    memory_service.remove_by_source(db, "event", event_id, current_user.id)
