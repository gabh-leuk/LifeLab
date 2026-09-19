from pydantic import BaseModel, Field

from app.schemas.experiment import MetricDef

# AI 候选的分类约束与 profile/service 保持一致
BACKGROUND_CATEGORY_PATTERN = "^(identity|habit|preference|context|constraint)$"


class DistillRequest(BaseModel):
    """把一次问答精炼成两类可沉淀候选（问题/背景）。"""

    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=10000)


class ProblemCandidate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=2000)
    confidence: float = Field(default=0.5, ge=0, le=1)


class BackgroundCandidate(BaseModel):
    category: str = Field(pattern=BACKGROUND_CATEGORY_PATTERN)
    content: str = Field(min_length=1, max_length=300)
    confidence: float = Field(default=0.5, ge=0, le=1)


class DistillResponse(BaseModel):
    problem_candidates: list[ProblemCandidate]
    background_candidates: list[BackgroundCandidate]


class CommitRequest(BaseModel):
    """用户勾选后的授权写入（空列表 = 不写该类）。"""

    problems: list[ProblemCandidate] = Field(default_factory=list, max_length=20)
    background: list[BackgroundCandidate] = Field(default_factory=list, max_length=20)


class CommitResponse(BaseModel):
    problem_ids: list[int]
    profile_fact_ids: list[int]
    problems_skipped: int = 0  # 重复/空白被跳过
    background_skipped: int = 0  # 重复/超配额被跳过


# ── AI 实验设计（问题 → 可执行实验草稿，用户确认后创建） ─────


class ExperimentDraftRequest(BaseModel):
    """从某个长期问题出发设计实验。"""

    problem_id: int


class DraftEvidence(BaseModel):
    """设计依据：来源可追溯（发现/记忆/背景）。"""

    kind: str
    ref: str | None = None
    title: str | None = None
    similarity: float | None = None


class ExperimentDraftResponse(BaseModel):
    """实验草稿：前端可编辑，确认后走 POST /experiments 创建（source=ai）。"""

    problem_id: int
    name: str
    question: str
    hypothesis: str
    variable: str
    indicator: str
    metrics: list[MetricDef]
    expected_days: int
    baseline_note: str | None = None
    rationale: str = ""  # 设计思路（给人看，不写进实验）
    evidence: list[DraftEvidence] = Field(default_factory=list)
