from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORY_PATTERN = "^(identity|habit|preference|context|constraint)$"


class ProfileFactCreate(BaseModel):
    """用户确认的背景事实（手写即确认；AI 候选须走授权写入）。"""

    category: str = Field(pattern=CATEGORY_PATTERN)
    content: str = Field(min_length=1, max_length=300)
    source_ref: str | None = Field(default=None, max_length=255)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v.strip()


class ProfileFactUpdate(BaseModel):
    category: str | None = Field(default=None, pattern=CATEGORY_PATTERN)
    content: str | None = Field(default=None, min_length=1, max_length=300)
    status: Literal["active", "archived"] | None = None

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("content must not be blank")
        return v.strip() if v is not None else None


class ProfileFactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    category: str
    content: str
    source: str
    source_ref: str | None
    confidence: float | None
    status: str
    last_confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ── 画像晋升（跨周/跨天复现的提炼产物 → 候选 → 确认写入） ──


class PromotionMember(BaseModel):
    kind: str
    ref: str | None
    content: str
    similarity: float | None = None  # 与代表表述的相似度


class PromotionCandidate(BaseModel):
    content: str
    category: str = Field(pattern=CATEGORY_PATTERN)
    confidence: float = Field(ge=0, le=1)
    source_ref: str | None = None
    source_kind: str  # pattern | insight
    spans: list[str] = Field(default_factory=list)  # 覆盖的周/天键
    avg_similarity: float = 0.0
    members: list[PromotionMember] = Field(default_factory=list)


class PromotionScanResponse(BaseModel):
    candidates: list[PromotionCandidate]
    scanned_patterns: int
    scanned_insights: int
    mode: str  # refined | raw


class PromotionCommitRequest(BaseModel):
    """用户确认的晋升候选（可编辑内容/分类）。"""

    candidates: list[PromotionCandidate] = Field(min_length=1, max_length=20)


class PromotionCommitResponse(BaseModel):
    fact_ids: list[int]
    skipped: int = 0  # 重复/超配额跳过数


__all__ = [
    "CATEGORY_PATTERN",
    "ProfileFactCreate",
    "ProfileFactRead",
    "ProfileFactUpdate",
    "PromotionCandidate",
    "PromotionCommitRequest",
    "PromotionCommitResponse",
    "PromotionMember",
    "PromotionScanResponse",
]
