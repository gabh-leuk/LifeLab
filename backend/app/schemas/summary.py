from datetime import datetime

from pydantic import BaseModel


class TypeCount(BaseModel):
    type: str
    count: int


class DailySummary(BaseModel):
    date: str
    event_total: int
    by_type: list[TypeCount]
    thought_total: int
    state_total: int
    avg_energy: float | None
    avg_focus: float | None
    avg_irritation: float | None
    first_event_at: datetime | None
    last_event_at: datetime | None
