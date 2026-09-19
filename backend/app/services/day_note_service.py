"""每日小结服务：用户手写小结（复盘前的可选上下文）。

小结与 AI 复盘解耦：复盘可反复重生成，小结始终保留；
生成复盘时由 review_service 读取并注入 prompt。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.day_note import DayNote


def get_note(db: Session, date: str, user_id: int) -> DayNote | None:
    return db.scalar(
        select(DayNote).where(
            DayNote.user_id == user_id, DayNote.date == date
        )
    )


def get_note_content(db: Session, date: str, user_id: int) -> str:
    note = get_note(db, date, user_id)
    return note.content.strip() if note and note.content else ""


def upsert_note(
    db: Session, date: str, content: str, user_id: int
) -> DayNote | None:
    """写入或清除小结（空白内容 = 清除该日小结）。清除时返回 None。"""
    note = get_note(db, date, user_id)
    text = content.strip()
    if not text:
        if note is not None:
            db.delete(note)
            db.commit()
        return None
    if note is not None:
        note.content = text
        db.commit()
        db.refresh(note)
        return note
    note = DayNote(user_id=user_id, date=date, content=text)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note
