from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StateCreate(BaseModel):
    energy: int = Field(ge=0, le=5)
    focus: int = Field(ge=0, le=5)
    irritation: int = Field(ge=0, le=5)


class StateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    energy: int
    focus: int
    irritation: int
    timestamp: datetime
