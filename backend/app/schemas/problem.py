from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.problem import ProblemStatus


class ProblemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v.strip()


class ProblemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=2000)
    status: ProblemStatus | None = None


class ProblemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    category: str | None
    note: str | None
    status: ProblemStatus
    source: str = "manual"  # manual | ai（AI 候选经授权写入）
    created_at: datetime
    updated_at: datetime