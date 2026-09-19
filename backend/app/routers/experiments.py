import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import AiUserDep, DbDep, UserDep
from app.llm import LLMError
from app.models.experiment import (
    Experiment,
    ExperimentLog,
    ExperimentStatus,
    ExperimentStatusEvent,
)
from app.models.finding import Finding, FindingKind
from app.schemas.experiment import (
    AggregateResult,
    ConcludeResult,
    ExperimentCreate,
    ExperimentDetail,
    ExperimentLogBatchCreate,
    ExperimentLogCreate,
    ExperimentLogRead,
    ExperimentRead,
    ExperimentStats,
    ExperimentStatusChange,
    ExperimentStatusEventRead,
    ExperimentUpdate,
    InferSourcesRequest,
    InferSourcesResponse,
)
from app.services import experiment_service, metric_source_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/experiments", tags=["experiments"])

# 前进迁移（真实过程，写状态历史）：
#   DRAFT → RUNNING → PAUSED ⇄ RUNNING → COMPLETED → CONCLUDED
_ALLOWED_TRANSITIONS: dict[ExperimentStatus, set[ExperimentStatus]] = {
    ExperimentStatus.DRAFT: {ExperimentStatus.RUNNING},
    ExperimentStatus.RUNNING: {ExperimentStatus.PAUSED, ExperimentStatus.COMPLETED},
    ExperimentStatus.PAUSED: {ExperimentStatus.RUNNING, ExperimentStatus.COMPLETED},
    ExperimentStatus.COMPLETED: {ExperimentStatus.CONCLUDED},
    ExperimentStatus.CONCLUDED: set(),  # 终态：只能通过 revert 撤销
}

# 回退（误触保底，撤销最后一步，不写历史）：
#   CONCLUDED → COMPLETED（撤销"下结论"，清结论字段）
#   COMPLETED → RUNNING  （撤销"完成"，清 ended_at/效果分析；若完成前是暂停，补记一次恢复）
_REVERTS: dict[ExperimentStatus, ExperimentStatus] = {
    ExperimentStatus.CONCLUDED: ExperimentStatus.COMPLETED,
    ExperimentStatus.COMPLETED: ExperimentStatus.RUNNING,
}


def _latest_status_event(
    db: Session, exp_id: int, user_id: int
) -> ExperimentStatusEvent | None:
    """取最近一条状态历史（用于回退时撤销被误触的那条）。"""
    return db.scalar(
        select(ExperimentStatusEvent)
        .where(
            ExperimentStatusEvent.experiment_id == exp_id,
            ExperimentStatusEvent.user_id == user_id,
        )
        .order_by(ExperimentStatusEvent.created_at.desc(), ExperimentStatusEvent.id.desc())
        .limit(1)
    )


def _get_or_404(db: Session, exp_id: int, user_id: int) -> Experiment:
    exp = experiment_service.get(db, exp_id, user_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return exp


def _get_log_or_404(db: Session, exp: Experiment, log_id: int) -> ExperimentLog:
    log = db.scalar(
        select(ExperimentLog).where(
            ExperimentLog.id == log_id,
            ExperimentLog.experiment_id == exp.id,
            ExperimentLog.user_id == exp.user_id,
        )
    )
    if log is None:
        raise HTTPException(status_code=404, detail="experiment log not found")
    return log


def _apply_inferred_sources(
    db: Session,
    metrics: list[dict],
    infer_keys: list[str],
    *,
    user_id: int,
    is_demo: bool,
    context: str,
) -> list[dict]:
    """给**显式提名**的指标填 source；就地改并返回同一个列表。

    这是「AI 写库」的一条授权例外（见 `docs/MASTER_PLAN.md`「AI 绝对红线」节），
    边界四条：只写 source 一个路由字段、只碰 infer_keys 里的 key、
    输出必过 `validate_metric_source`、**任何失败都原样返回**——
    LLM 挂了/超时/没配 key 都只意味着「来源留空」，绝不能挡住实验创建。

    为什么由请求提名而不是「source==manual 就推断」：manual 是歧义的，
    分不清「用户还没决定」和「用户就是要手动填」（主观评分）。照 manual 推断
    会每次编辑都重来一遍，把有意留成手动的指标翻掉。
    """
    if not infer_keys or is_demo:
        return metrics
    wanted = set(infer_keys)
    nominated = [m for m in metrics if m.get("key") in wanted]
    if not nominated:
        return metrics
    try:
        inferred = metric_source_service.infer_sources(
            db, nominated, user_id=user_id, context=context
        )
    except Exception:  # LLMError / 超时 / 任何意外
        logger.warning("指标来源识别失败，落回手动", exc_info=True)
        return metrics
    for m in metrics:
        src = inferred.get(m.get("key"))
        if src:
            m["source"] = src
    return metrics


def _metric_context(*parts: str | None) -> str:
    return "\n".join(p.strip() for p in parts if p and p.strip())


@router.post("/infer-sources", response_model=InferSourcesResponse)
def infer_sources(
    payload: InferSourcesRequest, db: DbDep, current_user: AiUserDep
):
    """把指标名识别成数据源（无状态、可反复调）。

    与创建实验时的自动识别**故意不同**：那条路失败必须静默落回手动（用户是来
    建实验的，不是来等 AI 的）；这条是用户专门来要识别结果的，失败就该说出来。
    """
    try:
        sources = metric_source_service.infer_sources(
            db,
            [m.model_dump() for m in payload.metrics],
            user_id=current_user.id,
            context=payload.context or "",
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"AI 识别失败：{e}") from e
    return InferSourcesResponse(sources=sources)


@router.post("", response_model=ExperimentRead, status_code=status.HTTP_201_CREATED)
def create_experiment(
    payload: ExperimentCreate, db: DbDep, current_user: UserDep
):
    data = payload.model_dump()
    data.pop("infer_keys", None)
    metrics = [m.model_dump() for m in payload.metrics] if payload.metrics else None
    if metrics:
        metrics = _apply_inferred_sources(
            db,
            metrics,
            payload.infer_keys,
            user_id=current_user.id,
            is_demo=current_user.is_demo,
            context=_metric_context(payload.name, payload.question, payload.hypothesis),
        )
    data["metrics"] = metrics
    exp = Experiment(user_id=current_user.id, **data)
    db.add(exp)
    db.commit()
    db.refresh(exp)
    return exp


@router.get("", response_model=list[ExperimentRead])
def list_experiments(db: DbDep, current_user: UserDep):
    return experiment_service.list_for_user(db, current_user.id)


@router.get("/{exp_id}", response_model=ExperimentDetail)
def get_experiment(exp_id: int, db: DbDep, current_user: UserDep):
    """详情：实验本体 + 状态历史 + 统计（一次取全）。"""
    exp = _get_or_404(db, exp_id, current_user.id)
    events = db.scalars(
        select(ExperimentStatusEvent)
        .where(
            ExperimentStatusEvent.experiment_id == exp.id,
            ExperimentStatusEvent.user_id == current_user.id,
        )
        .order_by(ExperimentStatusEvent.created_at.asc())
    ).all()
    return ExperimentDetail(
        **ExperimentRead.model_validate(exp).model_dump(),
        status_events=[ExperimentStatusEventRead.model_validate(e) for e in events],
        stats=experiment_service.build_stats(db, exp),
    )


@router.patch("/{exp_id}", response_model=ExperimentRead)
def update_experiment(
    exp_id: int, payload: ExperimentUpdate, db: DbDep, current_user: UserDep
):
    exp = _get_or_404(db, exp_id, current_user.id)
    data = payload.model_dump()
    infer_keys = data.pop("infer_keys", [])
    changes = {k: v for k, v in data.items() if v is not None}
    if "metrics" in changes and changes["metrics"] is not None:
        changes["metrics"] = _apply_inferred_sources(
            db,
            [m.model_dump() for m in payload.metrics or []],
            infer_keys,
            user_id=current_user.id,
            is_demo=current_user.is_demo,
            context=_metric_context(
                changes.get("name") or exp.name,
                changes.get("question") or exp.question,
                changes.get("hypothesis") or exp.hypothesis,
            ),
        )
    for k, v in changes.items():
        setattr(exp, k, v)
    if "metrics" in changes:
        # 指标被删/改为 manual 后，旧 AUTO 点会沦为孤儿，显式清理
        experiment_service.prune_orphan_auto_points(db, exp)
    db.commit()
    db.refresh(exp)
    return exp


@router.post("/{exp_id}/status", response_model=ExperimentRead)
def change_status(
    exp_id: int,
    payload: ExperimentStatusChange,
    db: DbDep,
    current_user: UserDep,
):
    """前进迁移，非法迁移 409；每次迁移写 experiment_status_events（真实过程）。

    每状态必填的专属变量（业务校验）：
    - → PAUSED：reason（暂停原因）
    - → COMPLETED：completion_analysis（效果分析）
    - → CONCLUDED：verdict + conclusion + conclusion_reason（判定/结论/依据）
    仅允许前进；误触回退请用 POST /{exp_id}/revert。
    """
    exp = _get_or_404(db, exp_id, current_user.id)
    target = payload.status
    current = ExperimentStatus(exp.status)
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail=f"cannot transition {current.value} -> {target.value}"
            + ("（回退请用 /revert，回退不产生状态历史）" if target in _REVERTS.values() else ""),
        )

    _validate_transition_payload(target, payload)

    now = datetime.now(timezone.utc)
    if target == ExperimentStatus.RUNNING and current != ExperimentStatus.RUNNING:
        exp.started_at = exp.started_at or now  # 首次开始；恢复不覆盖
    if target == ExperimentStatus.COMPLETED:
        exp.ended_at = now
        exp.completion_analysis = payload.completion_analysis
    if target == ExperimentStatus.CONCLUDED:
        exp.ended_at = exp.ended_at or now
        exp.result_verdict = payload.verdict.value if payload.verdict else None
        exp.conclusion = payload.conclusion
        exp.conclusion_reason = payload.conclusion_reason
        exp.conclusion_confidence = payload.conclusion_confidence
        exp.concluded_at = now
    exp.status = target.value

    db.add(
        ExperimentStatusEvent(
            user_id=current_user.id,
            experiment_id=exp.id,
            from_status=current.value,
            to_status=target.value,
            reason=payload.reason,
        )
    )
    db.commit()
    db.refresh(exp)
    return exp


@router.post("/{exp_id}/revert", response_model=ExperimentRead)
def revert_status(exp_id: int, db: DbDep, current_user: UserDep):
    """误触保底：撤销最后一步前进（CONCLUDED→COMPLETED，COMPLETED→RUNNING）。

    回退是"撤销"而不是"新过程"：删掉被误触的那条状态历史，
    让历史只记录真实的暂停/恢复/完成/结论时间。
    - CONCLUDED → COMPLETED：清空结论字段（判定/结论/依据/置信度/结论时间）
    - COMPLETED  → RUNNING：清空 ended_at 与效果分析；若完成前处于暂停，
      补记一条 "PAUSED → RUNNING" 历史，保持时间轴连贯。
    """
    exp = _get_or_404(db, exp_id, current_user.id)
    current = ExperimentStatus(exp.status)
    target = _REVERTS.get(current)
    if target is None:
        raise HTTPException(
            status_code=409,
            detail=f"{current.value} 不支持回退（回退仅用于撤销误触的完成/结论）",
        )

    last = _latest_status_event(db, exp.id, current_user.id)
    # 撤销被误触的那条历史；若历史与状态不一致（脏数据）则跳过删除
    if last is not None and last.to_status == current.value:
        db.delete(last)

    if current == ExperimentStatus.CONCLUDED:
        # 撤销"下结论"：完成仍是真实的（保留 ended_at 与效果分析），只清结论
        exp.result_verdict = None
        exp.conclusion = None
        exp.conclusion_reason = None
        exp.conclusion_confidence = None
        exp.concluded_at = None
    else:  # COMPLETED → RUNNING
        # 撤销"完成"：完成本身是误触，效果分析一并清除
        exp.ended_at = None
        exp.completion_analysis = None
        # 完成前若在暂停中，删除的历史是 "PAUSED → COMPLETED"；
        # 状态回到 RUNNING 意味着暂停已结束，补记恢复，时间轴才连贯
        if last is not None and last.from_status == ExperimentStatus.PAUSED.value:
            db.add(
                ExperimentStatusEvent(
                    user_id=current_user.id,
                    experiment_id=exp.id,
                    from_status=ExperimentStatus.PAUSED.value,
                    to_status=ExperimentStatus.RUNNING.value,
                    reason="（撤销误触完成，暂停结束）",
                )
            )

    exp.status = target.value
    db.commit()
    db.refresh(exp)
    return exp


@router.post("/{exp_id}/conclude", response_model=ConcludeResult)
def conclude_experiment(
    exp_id: int,
    payload: ExperimentStatusChange,
    db: DbDep,
    current_user: UserDep,
):
    """下结论（COMPLETED → CONCLUDED），可勾选把结论一键写入知识库 Finding。

    红线：是否写入知识库由用户显式授权（write_finding=true），AI 不自动写。
    """
    if payload.status != ExperimentStatus.CONCLUDED:
        raise HTTPException(status_code=422, detail="status must be CONCLUDED")
    exp = change_status(exp_id, payload, db, current_user)

    finding_id = None
    if payload.write_finding:
        finding = Finding(
            user_id=current_user.id,
            problem_id=payload.problem_id,
            kind=FindingKind.CONCLUSION.value,
            title=f"实验结论：{exp.name}"[:200],
            observation=payload.conclusion or "",
            evidence=exp.completion_analysis,
            interpretation=payload.conclusion_reason,
            confidence=(
                payload.conclusion_confidence
                if payload.conclusion_confidence is not None
                else 0.5
            ),
        )
        db.add(finding)
        db.commit()
        db.refresh(finding)
        finding_id = finding.id

    return ConcludeResult(
        experiment=ExperimentRead.model_validate(exp), finding_id=finding_id
    )


def _validate_transition_payload(target: ExperimentStatus, payload: ExperimentStatusChange) -> None:
    """按目标状态校验专属必填变量。"""
    missing: list[str] = []
    if target == ExperimentStatus.PAUSED and not (payload.reason or "").strip():
        missing.append("暂停原因 (reason)")
    if target == ExperimentStatus.COMPLETED and not (payload.completion_analysis or "").strip():
        missing.append("效果分析 (completion_analysis)")
    if target == ExperimentStatus.CONCLUDED:
        if payload.verdict is None:
            missing.append("判定 (verdict)")
        if not (payload.conclusion or "").strip():
            missing.append("结论 (conclusion)")
        if not (payload.conclusion_reason or "").strip():
            missing.append("结论依据 (conclusion_reason)")
    if missing:
        raise HTTPException(
            status_code=422,
            detail="缺少必填内容：" + "、".join(missing),
        )


# ── 数据点 ─────────────────────────────────────────────────


@router.post("/{exp_id}/logs", response_model=ExperimentLogRead, status_code=status.HTTP_201_CREATED)
def create_log(
    exp_id: int, payload: ExperimentLogCreate, db: DbDep, current_user: UserDep
):
    exp = _get_or_404(db, exp_id, current_user.id)
    log = _build_log(exp, payload)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.post(
    "/{exp_id}/logs/batch",
    response_model=list[ExperimentLogRead],
    status_code=status.HTTP_201_CREATED,
)
def create_logs_batch(
    exp_id: int,
    payload: ExperimentLogBatchCreate,
    db: DbDep,
    current_user: UserDep,
):
    exp = _get_or_404(db, exp_id, current_user.id)
    logs = [_build_log(exp, item) for item in payload.logs]
    db.add_all(logs)
    db.commit()
    for log in logs:
        db.refresh(log)
    return logs


def _build_log(exp: Experiment, payload: ExperimentLogCreate) -> ExperimentLog:
    return ExperimentLog(
        user_id=exp.user_id,
        experiment_id=exp.id,
        metric=payload.metric.strip(),
        value=payload.value,
        note=payload.note,
        timestamp=payload.timestamp or datetime.now(timezone.utc),
        source="MANUAL",
    )


@router.get("/{exp_id}/logs", response_model=list[ExperimentLogRead])
def list_logs(
    exp_id: int,
    db: DbDep,
    current_user: UserDep,
    metric: str | None = None,
    limit: int = 500,
):
    exp = _get_or_404(db, exp_id, current_user.id)
    stmt = (
        select(ExperimentLog)
        .where(
            ExperimentLog.experiment_id == exp.id,
            ExperimentLog.user_id == current_user.id,
        )
        .order_by(ExperimentLog.timestamp.desc())
        .limit(limit)
    )
    if metric:
        stmt = stmt.where(ExperimentLog.metric == metric)
    return db.scalars(stmt).all()


@router.delete("/{exp_id}/logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_log(exp_id: int, log_id: int, db: DbDep, current_user: UserDep):
    exp = _get_or_404(db, exp_id, current_user.id)
    log = _get_log_or_404(db, exp, log_id)
    db.delete(log)
    db.commit()


# ── 统计 / 状态历史 ────────────────────────────────────────


@router.get("/{exp_id}/stats", response_model=ExperimentStats)
def get_stats(exp_id: int, db: DbDep, current_user: UserDep):
    exp = _get_or_404(db, exp_id, current_user.id)
    return experiment_service.build_stats(db, exp)


@router.post("/{exp_id}/aggregate", response_model=AggregateResult)
def aggregate_experiment(
    exp_id: int,
    db: DbDep,
    current_user: UserDep,
    tz_offset: int = 0,
):
    """把运行期间的 events/states 按天聚合为 AUTO 数据点（幂等重算）。

    仅处理声明了 source 的指标（event_duration:/event_count:/state_avg:）。
    手动数据点不受影响；某天源数据消失时对应 AUTO 点被清除。
    """
    exp = _get_or_404(db, exp_id, current_user.id)
    result = experiment_service.aggregate_logs(db, exp, tz_offset=tz_offset)
    return AggregateResult(experiment_id=exp.id, **result)


@router.get("/{exp_id}/status-events", response_model=list[ExperimentStatusEventRead])
def list_status_events(exp_id: int, db: DbDep, current_user: UserDep):
    exp = _get_or_404(db, exp_id, current_user.id)
    return db.scalars(
        select(ExperimentStatusEvent)
        .where(
            ExperimentStatusEvent.experiment_id == exp.id,
            ExperimentStatusEvent.user_id == current_user.id,
        )
        .order_by(ExperimentStatusEvent.created_at.asc())
    ).all()


@router.delete("/{exp_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experiment(exp_id: int, db: DbDep, current_user: UserDep):
    """删除实验（含其全部数据点与状态历史）。"""
    exp = _get_or_404(db, exp_id, current_user.id)
    for log in db.scalars(
        select(ExperimentLog).where(ExperimentLog.experiment_id == exp.id)
    ).all():
        db.delete(log)
    for ev in db.scalars(
        select(ExperimentStatusEvent).where(
            ExperimentStatusEvent.experiment_id == exp.id
        )
    ).all():
        db.delete(ev)
    db.delete(exp)
    db.commit()
