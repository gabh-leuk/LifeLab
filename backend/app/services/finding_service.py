"""发现/结论（知识库条目）的读写。

写路径原先内联在 `routers/findings.py` 里，读路径内联在 `agent/tools.py` 里。
M6 第二步统一收拢到这里，Agent 工具与 HTTP 路由共用。

Finding 没有 `source` 列 —— 「AI 产物必须确认后才写」这条红线在 Finding 上
靠**审批卡**落地（Agent 的 create_finding 是写工具，先中断等人批），不靠来源标记。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finding import Finding
from app.schemas.finding import FindingCreate


def get(db: Session, finding_id: int, user_id: int) -> Finding | None:
    return db.scalar(
        select(Finding).where(Finding.id == finding_id, Finding.user_id == user_id)
    )


def list_for_user(
    db: Session,
    user_id: int,
    *,
    problem_id: int | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> Sequence[Finding]:
    stmt = (
        select(Finding)
        .where(Finding.user_id == user_id)
        .order_by(Finding.created_at.desc())
    )
    if problem_id is not None:
        stmt = stmt.where(Finding.problem_id == problem_id)
    if kind:
        stmt = stmt.where(Finding.kind == kind.upper())
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


def create(db: Session, payload: FindingCreate, *, user_id: int) -> Finding:
    """建一条发现（flush 但**不 commit**，事务边界归调用方）。"""
    finding = Finding(
        user_id=user_id,
        problem_id=payload.problem_id,
        kind=payload.kind.value,
        title=payload.title,
        observation=payload.observation,
        evidence=payload.evidence,
        interpretation=payload.interpretation,
        confidence=payload.confidence,
        next_step=payload.next_step,
    )
    db.add(finding)
    db.flush()
    return finding
