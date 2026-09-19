from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import DbDep, UserDep
from app.models.thought import Thought
from app.schemas.thought import ThoughtCreate, ThoughtRead
from app.services import memory_service
from app.utils import parse_day_range

router = APIRouter(prefix="/thoughts", tags=["thoughts"])


def _tags_to_str(tags: list[str]) -> str:
    return ",".join(t.strip() for t in tags if t.strip())


@router.post("", response_model=ThoughtRead, status_code=status.HTTP_201_CREATED)
def create_thought(payload: ThoughtCreate, db: DbDep, current_user: UserDep):
    thought = Thought(
        user_id=current_user.id,
        content=payload.content.strip(),
        tags_raw=_tags_to_str(payload.tags),
        source=payload.source,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(thought)
    db.commit()
    db.refresh(thought)
    return thought


@router.get("", response_model=list[ThoughtRead])
def list_thoughts(
    db: DbDep,
    current_user: UserDep,
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
    tz_offset: int = Query(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480"),
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = (
        select(Thought)
        .where(Thought.user_id == current_user.id)
        .order_by(Thought.timestamp.desc())
        .limit(limit)
    )
    if date:
        day_start, day_end = parse_day_range(date, tz_offset)
        stmt = stmt.where(Thought.timestamp >= day_start, Thought.timestamp < day_end)
    return db.scalars(stmt).all()


@router.delete("/{thought_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thought(thought_id: int, db: DbDep, current_user: UserDep):
    thought = db.get(Thought, thought_id)
    if thought is None or thought.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="thought not found")
    db.delete(thought)
    db.commit()
    # 级联：清掉对应记忆索引
    memory_service.remove_by_source(db, "thought", thought_id, current_user.id)
