from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ThoughtCreate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    source: str = "MANUAL"

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


class ThoughtRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    content: str
    tags: list[str] = []
    source: str
    timestamp: datetime
