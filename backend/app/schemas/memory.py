from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

MEMORY_KINDS = "^(thought|event|review|finding|problem|insight|pattern)$"

# 边界一：手动写入接口只允许"提炼类"（review/finding/insight/pattern），
# 原始 kind（thought/event/state/problem）一律拒绝——它们只作提炼素材。
MANUAL_MEMORY_KINDS = "^(review|finding|insight|pattern)$"


class MemoryItemCreate(BaseModel):
    """写入一条语义记忆（通常由服务/Agent 调用，而非前端直接创建）。

    kind 仅限提炼类；原始记录请走各自的业务表，不要塞进向量库。
    """

    kind: str = Field(pattern=MANUAL_MEMORY_KINDS)
    content: str = Field(min_length=1, max_length=10000)
    source_ref: str | None = Field(default=None, max_length=255)
    embed: bool = True  # 是否生成向量（默认 true）


class MemoryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    kind: str
    content: str
    source_ref: str | None
    embedding: str | None = Field(default=None, exclude=True)  # 内部用，不返回（太长）
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_embedding(self) -> bool:
        """是否已向量化（前端标记用，不传原始向量字符串）。"""
        return bool(self.embedding)


class MemorySearchRequest(BaseModel):
    """语义检索：查与 query 最相似的记忆。"""

    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=50)
    kind: str | None = Field(default=None, pattern=MEMORY_KINDS)
    min_similarity: float | None = Field(
        default=None, ge=0.0, le=1.0, description="覆盖默认相似度门槛（调试用）"
    )


class MemorySearchHit(BaseModel):
    item: MemoryItemRead
    similarity: float  # 原始余弦相似度 0-1
    final_score: float  # 时间权重后的排序分
    anchor_date: str | None = None  # 内容锚点日期（时间权重依据）


class MemorySearchDebugRequest(BaseModel):
    """检索诊断请求（不截断候选，便于看完整分布）。"""

    query: str = Field(min_length=1, max_length=1000)
    kind: str | None = Field(default=None, pattern=MEMORY_KINDS)
    limit: int = Field(default=50, ge=1, le=200)
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    time_weight: float | None = Field(default=None, ge=0.0, le=1.0)


class MemoryDebugHit(MemorySearchHit):
    recency: float  # 时间新鲜度 0-1
    half_life_days: float  # 该条目的衰减半衰期
    passed_threshold: bool  # 是否通过相似度门槛（前端画门槛线用）


class MemoryDebugResponse(BaseModel):
    query: str
    threshold: float
    time_weight: float
    mode: str  # vector | keyword
    hits: list[MemoryDebugHit]


class MemoryKindStat(BaseModel):
    kind: str
    count: int
    embedded: int


class MemoryStatsResponse(BaseModel):
    total: int
    embedded: int
    by_kind: list[MemoryKindStat]
    source_counts: dict[str, int]


class MemorySearchResponse(BaseModel):
    query: str
    hits: list[MemorySearchHit]


class MemoryAskRequest(BaseModel):
    """向记忆问答：LLM 基于召回的历史记忆归纳回答。"""

    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    kind: str | None = Field(default=None, pattern=MEMORY_KINDS)
    min_similarity: float | None = Field(
        default=None, ge=0.0, le=1.0, description="覆盖默认相似度门槛（调试用）"
    )


class MemoryAnswerSource(BaseModel):
    id: int
    kind: str
    content: str
    source_ref: str | None
    similarity: float | None = None
    final_score: float | None = None
    anchor_date: str | None = None


class MemoryAskResponse(BaseModel):
    question: str
    answer: str
    sources: list[MemoryAnswerSource]  # 引用的记忆片段（可追溯）


class MemoryExtractRequest(BaseModel):
    """从某天记录提炼长期洞察并写入记忆。"""

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    tz_offset: int = Field(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480")


class MemoryExtractResponse(BaseModel):
    date: str
    insights: list[MemoryItemRead]
    skipped: bool = False


class MemoryPatternExtractRequest(BaseModel):
    """从某一周（ISO 周）的洞察/发现中提炼长期行为模式。"""

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="该周内任意一天，按 ISO 周对齐")
    tz_offset: int = Field(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480")
    force: bool = False  # True 则覆盖该周已提炼的模式


class MemoryPatternExtractResponse(BaseModel):
    week: str  # 形如 2026-W37
    week_start: str  # 该 ISO 周周一（YYYY-MM-DD）
    week_end: str  # 该 ISO 周周日（YYYY-MM-DD）
    patterns: list[MemoryItemRead]
    skipped: bool = False