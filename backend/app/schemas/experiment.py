from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.behavior_categories import CATEGORIES
from app.models.event import EventType
from app.models.event_type import CUSTOM_KEY_PREFIX
from app.models.experiment import ExperimentStatus, Verdict

METRIC_DIRECTIONS = ("up_good", "down_good", "neutral")

_ALLOWED_STATE_FIELDS = ("energy", "focus", "irritation")
_EVENT_TYPES = {e.value for e in EventType}
_CATEGORIES = set(CATEGORIES)

_SOURCE_ERROR = (
    "metric source 必须为 manual | event_duration:<type> | "
    "event_count:<type> | event_duration_category:<cat> | "
    "event_count_category:<cat> | state_avg:<energy|focus|irritation> | "
    "usage_duration:<app> | usage_platform:<android|pc> | usage_total"
    "（usage_* 可加时段后缀 @起-止，如 usage_platform:pc@22-24）"
)


def split_time_window(source: str) -> tuple[str, tuple[int, int] | None]:
    """拆出可选时段后缀：`usage_platform:pc@22-24` → ("usage_platform:pc", (22, 24))。

    无后缀或格式非法则窗口为 None（由 validate_metric_source 决定是否拒绝）。
    """
    base, sep, window = source.partition("@")
    if not sep:
        return source, None
    start_s, dash, end_s = window.partition("-")
    if not dash:
        return source, None
    try:
        return base, (int(start_s), int(end_s))
    except ValueError:
        return source, None


def _validate_base(source: str) -> str:
    if source in ("manual", "usage_total"):
        return source
    kind, sep, arg = source.partition(":")
    if (
        sep
        and kind in ("event_duration", "event_count")
        and (arg in _EVENT_TYPES or arg.startswith(CUSTOM_KEY_PREFIX))
    ):
        return source
    if (
        sep
        and kind in ("event_duration_category", "event_count_category")
        and arg in _CATEGORIES
    ):
        return source
    if sep and kind == "state_avg" and arg in _ALLOWED_STATE_FIELDS:
        return source
    if sep and kind == "usage_duration" and 0 < len(arg) <= 200:
        return source
    if sep and kind == "usage_platform" and arg in ("android", "pc"):
        return source
    raise ValueError(_SOURCE_ERROR)


def _is_usage_base(base: str) -> bool:
    return base == "usage_total" or base.startswith(("usage_platform:", "usage_duration:"))


def validate_metric_source(source: str) -> str:
    """数据源白名单：

    manual / event_duration:<type> / event_count:<type> /
    state_avg:<field> / usage_duration:<app> / usage_platform:<android|pc> /
    usage_total；usage_* 可加时段后缀 @起-止（0<=起<止<=24，不跨午夜）。

    非法 source 在聚合时只会静默产生 None，所以在入口直接拒绝。
    """
    base, window = split_time_window(source)
    if "@" in source and window is None:
        raise ValueError(_SOURCE_ERROR)
    _validate_base(base)
    if window is not None:
        if not _is_usage_base(base):
            raise ValueError("时段后缀 @起-止 只能用于 usage_total / usage_platform / usage_duration")
        start, end = window
        if not (0 <= start < end <= 24):
            raise ValueError("时段必须为 0<=起<止<=24（不支持跨午夜）")
    return source


class MetricDef(BaseModel):
    """结构化指标定义：实验设计时声明，数据点挂到 key 上。

    source 决定数据从哪来：
    - manual                      手动录入（默认）
    - event_duration:LEARNING_START  某事件类型当天总时长（分钟）
    - event_count:EXERCISE_START     某事件类型当天次数
    - state_avg:focus               当天状态均值（energy/focus/irritation）
    - usage_duration:<app>          某应用当天使用时长（分钟，设备采集）
    - usage_platform:android|pc     某类设备当天使用总时长（分钟，设备采集）
    - usage_total                   全部设备当天使用总时长（分钟）
    - usage_*@起-止                  上述 usage_* 可加时段后缀，只统计该小时窗口
                                    （如 usage_platform:pc@22-24，0<=起<止<=24，不跨午夜）
    """

    key: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=20)
    direction: str = Field(default="neutral", pattern="^(up_good|down_good|neutral)$")
    source: str = Field(default="manual", max_length=50)

    @field_validator("source")
    @classmethod
    def _source_whitelisted(cls, v: str) -> str:
        return validate_metric_source(v)


class MetricSourceQuery(BaseModel):
    """要识别数据源的指标 —— 只给名字和单位，source 正是要问的东西。"""

    key: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=20)


class InferSourcesRequest(BaseModel):
    metrics: list[MetricSourceQuery] = Field(min_length=1, max_length=10)
    context: str | None = Field(default=None, max_length=2000)


class InferSourcesResponse(BaseModel):
    """只含识别出来的 key；没认出来的不出现（调用方保持 manual）。"""

    sources: dict[str, str]


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    question: str | None = Field(default=None, max_length=5000)
    hypothesis: str | None = Field(default=None, max_length=5000)
    variable: str | None = Field(default=None, max_length=5000)
    indicator: str | None = Field(default=None, max_length=5000)
    metrics: list[MetricDef] = Field(default_factory=list, max_length=10)
    expected_days: int | None = Field(default=None, ge=1, le=365)
    baseline_note: str | None = Field(default=None, max_length=5000)
    infer_keys: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="要由 AI 识别数据源的指标 key（用户只写了名字、还没定来源的那几行）；"
        "给的才识别，不给一律不动",
    )


class ExperimentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    question: str | None = Field(default=None, max_length=5000)
    hypothesis: str | None = Field(default=None, max_length=5000)
    variable: str | None = Field(default=None, max_length=5000)
    indicator: str | None = Field(default=None, max_length=5000)
    metrics: list[MetricDef] | None = Field(default=None, max_length=10)
    expected_days: int | None = Field(default=None, ge=1, le=365)
    baseline_note: str | None = Field(default=None, max_length=5000)
    completion_analysis: str | None = Field(default=None, max_length=5000)
    note: str | None = Field(default=None, max_length=5000)
    infer_keys: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="要由 AI 识别数据源的指标 key；只对本次提交里出现的 key 生效",
    )


class ExperimentStatusChange(BaseModel):
    """状态迁移请求：暂停必须给原因；完成必须给效果分析；下结论必须给依据。"""

    status: ExperimentStatus
    reason: str | None = Field(default=None, max_length=5000, description="暂停原因/回退说明/备注")
    completion_analysis: str | None = Field(
        default=None, max_length=5000, description="完成时的效果分析（必填）"
    )
    verdict: Verdict | None = Field(
        default=None, description="下结论时的判定（必填）"
    )
    conclusion: str | None = Field(default=None, max_length=5000, description="结论文本（必填）")
    conclusion_reason: str | None = Field(
        default=None, max_length=5000, description="结论依据（必填）"
    )
    conclusion_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    write_finding: bool = Field(
        default=False, description="是否同时把结论写入知识库 Finding（挂到 problem_id）"
    )
    problem_id: int | None = Field(default=None, description="结论写入的 Finding 挂到哪个问题")


class ExperimentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    question: str | None
    hypothesis: str | None
    variable: str | None
    indicator: str | None
    metrics: list[MetricDef] | None
    expected_days: int | None
    baseline_note: str | None
    status: ExperimentStatus
    started_at: datetime | None
    ended_at: datetime | None
    completion_analysis: str | None
    result_verdict: Verdict | None
    conclusion: str | None
    conclusion_reason: str | None
    conclusion_confidence: float | None
    concluded_at: datetime | None
    note: str | None
    created_at: datetime
    updated_at: datetime


# ── 数据点 ──────────────────────────────────────────────


class ExperimentLogCreate(BaseModel):
    metric: str = Field(min_length=1, max_length=100)
    value: float
    note: str | None = Field(default=None, max_length=2000)
    timestamp: datetime | None = None  # 不传 = 服务器当前时间（支持补记）


class ExperimentLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    experiment_id: int
    metric: str
    value: float
    note: str | None
    timestamp: datetime
    source: str
    created_at: datetime


# ── 状态历史 ─────────────────────────────────────────────


class ExperimentStatusEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    experiment_id: int
    from_status: ExperimentStatus | None
    to_status: ExperimentStatus
    reason: str | None
    created_at: datetime


# ── 统计 ────────────────────────────────────────────────


class MetricStats(BaseModel):
    metric: str
    unit: str | None = None
    count: int
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    latest: float | None = None
    first: float | None = None
    change: float | None = None  # latest - first
    first_half_mean: float | None = None
    second_half_mean: float | None = None
    trend: str  # up | down | flat
    direction: str = "neutral"
    improved: bool | None = None  # 结合 direction 判断是否变好


class ExperimentStats(BaseModel):
    experiment_id: int
    total_logs: int
    metric_stats: list[MetricStats]
    running_days: float | None = None  # 有效运行时长（天，扣除暂停）
    paused_total_days: float | None = None
    days_elapsed: float | None = None  # 从首次开始到现在/结束


class ExperimentDetail(ExperimentRead):
    """详情 = 实验本体 + 状态历史 + 统计（供前端一次取全）。"""

    status_events: list[ExperimentStatusEventRead] = Field(default_factory=list)
    stats: ExperimentStats | None = None


class ConcludeResult(BaseModel):
    """下结论响应：实验 + 可选写入的 Finding。"""

    experiment: ExperimentRead
    finding_id: int | None = None


class ExperimentLogBatchCreate(BaseModel):
    """批量补录数据点（如从历史记录一次性导入）。"""

    logs: list[ExperimentLogCreate] = Field(min_length=1, max_length=200)

    @field_validator("logs")
    @classmethod
    def _unique_metric_required(cls, v: list[ExperimentLogCreate]) -> list[ExperimentLogCreate]:
        if not v:
            raise ValueError("logs must not be empty")
        return v


class AggregateResult(BaseModel):
    """数据聚合结果：按天从 events/states 算出的 AUTO 数据点。"""

    experiment_id: int
    created: int       # 新增数据点
    updated: int       # 重算覆盖的自动数据点
    deleted: int       # 清掉的无效自动数据点（如某天无数据）
    days: int          # 覆盖的天数
    metrics: list[str] = Field(default_factory=list)  # 参与的指标 key
