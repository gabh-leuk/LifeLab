from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.finding import FindingKind


class FindingCreate(BaseModel):
    problem_id: int | None = None
    kind: FindingKind = FindingKind.OBSERVATION
    title: str = Field(min_length=1, max_length=200)
    observation: str = Field(min_length=1, max_length=5000)
    evidence: str | None = Field(default=None, max_length=5000)
    interpretation: str | None = Field(default=None, max_length=5000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    next_step: str | None = Field(default=None, max_length=500)


class FindingUpdate(BaseModel):
    problem_id: int | None = None
    kind: FindingKind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    observation: str | None = Field(default=None, min_length=1, max_length=5000)
    evidence: str | None = Field(default=None, max_length=5000)
    interpretation: str | None = Field(default=None, max_length=5000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    next_step: str | None = Field(default=None, max_length=500)


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    problem_id: int | None
    kind: FindingKind
    title: str
    observation: str
    evidence: str | None
    interpretation: str | None
    confidence: float
    next_step: str | None
    created_at: datetime