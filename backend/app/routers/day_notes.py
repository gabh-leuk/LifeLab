from fastapi import APIRouter

from app.deps import DbDep, UserDep
from app.schemas.day_note import DayNoteRead, DayNoteUpsert
from app.services import day_note_service

router = APIRouter(prefix="/day-notes", tags=["day-notes"])


@router.get("/{date}", response_model=DayNoteRead)
def get_day_note(date: str, db: DbDep, current_user: UserDep):
    """读取某日小结（未写时返回空内容，便于前端直接编辑）。"""
    note = day_note_service.get_note(db, date, current_user.id)
    return DayNoteRead(
        date=date,
        content=note.content if note else "",
        updated_at=note.updated_at if note else None,
    )


@router.put("/{date}", response_model=DayNoteRead)
def save_day_note(
    date: str, payload: DayNoteUpsert, db: DbDep, current_user: UserDep
):
    """保存/更新某日小结；空白内容视为清除。"""
    note = day_note_service.upsert_note(db, date, payload.content, current_user.id)
    return DayNoteRead(
        date=date,
        content=note.content if note else "",
        updated_at=note.updated_at if note else None,
    )
