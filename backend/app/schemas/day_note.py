from datetime import datetime

from pydantic import BaseModel, Field


class DayNoteUpsert(BaseModel):
    """每日小结写入：复盘前的可选上下文（空白 = 清除）。"""

    content: str = Field(default="", max_length=5000)


class DayNoteRead(BaseModel):
    date: str
    content: str
    updated_at: datetime | None = None
