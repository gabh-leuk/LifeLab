from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExperimentStatus(StrEnum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CONCLUDED = "CONCLUDED"


class Verdict(StrEnum):
    """结论判定：假设是否被数据支持。"""

    SUPPORTED = "SUPPORTED"                    # 支持
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"  # 部分支持
    REFUTED = "REFUTED"                        # 推翻
    INCONCLUSIVE = "INCONCLUSIVE"              # 不确定/数据不足


class Experiment(Base):
    """行为实验：问题→假设→变量→指标→数据记录→效果分析→结论。

    每个状态有专属变量：
    - DRAFT：设计字段（metrics/expected_days/baseline_note）
    - RUNNING：数据点（experiment_logs）
    - PAUSED：暂停原因（experiment_status_events.reason 必填）
    - COMPLETED：效果分析（completion_analysis）
    - CONCLUDED：结论 + 依据 + 判定 + 置信度
    """

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200))
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    variable: Mapped[str | None] = mapped_column(Text, nullable=True)  # 改变什么
    indicator: Mapped[str | None] = mapped_column(Text, nullable=True)  # 观察什么（兼容旧自由文本）
    # 结构化指标清单：[{key, name, unit, direction}]，direction: up_good|down_good|neutral
    metrics: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expected_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 预计周期
    baseline_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 基线说明
    status: Mapped[str] = mapped_column(
        String(20), default=ExperimentStatus.DRAFT, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ── COMPLETED 专属：效果分析（必填才允许完成） ──
    completion_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── CONCLUDED 专属：结论 ──
    result_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    concluded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ExperimentLog(Base):
    """实验数据点：某指标在某时刻的观测值（手动录入，后续可接自动聚合）。"""

    __tablename__ = "experiment_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    metric: Mapped[str] = mapped_column(String(100))  # 指标 key 或名称
    value: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(20), default="MANUAL")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class ExperimentStatusEvent(Base):
    """实验状态迁移历史：每次迁移记 from/to/原因/时间，形成实验时间轴。

    暂停必须写原因（业务层校验）；完成需效果分析；下结论需依据。
    """

    __tablename__ = "experiment_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
