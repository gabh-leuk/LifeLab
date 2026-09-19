from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewStructured(BaseModel):
    """LLM 结构化输出。kind 区分：事实/感受/推测/结论。

    insights 是 L1 索引条目（可空）：与复盘共用一次 LLM 调用产出，
    由 review_service 写入 memory（kind=insight, source_ref=insight:{date}），
    避免"复盘后再单独提炼一次"的重复调用。
    """

    day_summary: str = Field(description="一两句话总结这一天")
    highlights: list[str] = Field(description="值得记录/做到的事")
    concerns: list[str] = Field(description="值得关注的问题")
    patterns: list[str] = Field(description="观察到的可能规律，开头需注明是推测")
    suggestions: list[str] = Field(description="明天可以做的 1-3 件事")
    insights: list[str] = Field(
        default_factory=list,
        description="对用户长期有价值、可复用的规律/偏好/改进建议（每条一句话）",
    )


class ReviewGenerateRequest(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    force: bool = False  # True 则重新生成覆盖


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    date: str
    review_text: str
    structured: dict | None
    source_data: dict | None
    model: str | None
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime


class ReviewList(BaseModel):
    date: str
    reviews: list[ReviewRead]


class ReviewIndexItem(BaseModel):
    """复盘索引的一项：日/周/月同构，让前端一个列表渲染三档。

    只带摘要与元信息 —— 正文仍走 GET /reviews/{date} 与
    GET /reviews/period/{type}/{date}，列表不该把整篇 markdown 拖下来。
    """

    kind: Literal["day", "week", "month"]
    # 日 = "2026-09-16"；周 = "2026-W37"；月 = "2026-09"
    key: str
    label: str
    # 仅周/月：该期间内的一天。详情端点 GET /reviews/period/{type}/{date} 收的是
    # 日期而不是 period_key，前端拿它回查就不用自己算 ISO 周。
    period_start: str | None = None
    summary: str = ""
    status: str
    # 仅周/月：期间素材在生成后有更新（详情里的 stale 同一口径）
    stale: bool = False
    updated_at: datetime


class PeriodStructured(BaseModel):
    """周/月复盘的结构化输出（周与月同构，复用一套渲染/入库逻辑）。"""

    period_summary: str = Field(default="", description="一段话总结该期间")
    patterns: list[str] = Field(
        default_factory=list, description="跨日/跨周反复出现的模式（推测需标注）"
    )
    missed_insights: list[str] = Field(
        default_factory=list, description="日/周提炼遗漏、值得长期保留的洞察"
    )
    suggestions: list[str] = Field(default_factory=list, description="下一期间的建议")


class PeriodReviewGenerateRequest(BaseModel):
    """生成周/月复盘：date 为该期间内任意一天（周按 ISO 对齐，月按自然月）。"""

    period_type: Literal["week", "month"]
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    force: bool = False
    tz_offset: int = Field(default=0, ge=-840, le=840)


class PeriodReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    period_type: str
    period_key: str
    period_start: str
    period_end: str
    review_text: str
    structured: dict | None
    source_data: dict | None
    model: str | None
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime
    stale: bool = False  # 期间素材在复盘生成后有更新（非 ORM 字段，按需计算）
    material_count: int = 0  # 当前可分析素材条数（非 ORM 字段）
