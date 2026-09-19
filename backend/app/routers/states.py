from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import DbDep, UserDep
from app.models.state import StateRecord
from app.schemas.state import StateCreate, StateRead
from app.services import memory_service
from app.utils import parse_day_range

router = APIRouter(prefix="/states", tags=["states"])


@router.post("", response_model=StateRead, status_code=status.HTTP_201_CREATED)
def create_state(payload: StateCreate, db: DbDep, current_user: UserDep):
    state = StateRecord(
        user_id=current_user.id,
        energy=payload.energy,
        focus=payload.focus,
        irritation=payload.irritation,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


@router.get("", response_model=list[StateRead])
def list_states(
    db: DbDep,
    current_user: UserDep,
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
    tz_offset: int = Query(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480"),
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = (
        select(StateRecord)
        .where(StateRecord.user_id == current_user.id)
        .order_by(StateRecord.timestamp.desc())
        .limit(limit)
    )
    if date:
        day_start, day_end = parse_day_range(date, tz_offset)
        stmt = stmt.where(
            StateRecord.timestamp >= day_start, StateRecord.timestamp < day_end
        )
    return db.scalars(stmt).all()


@router.delete("/{state_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_state(state_id: int, db: DbDep, current_user: UserDep):
    state = db.get(StateRecord, state_id)
    if state is None or state.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="state not found")
    db.delete(state)
    db.commit()
    # 级联：清掉对应记忆索引
    memory_service.remove_by_source(db, "state", state_id, current_user.id)
