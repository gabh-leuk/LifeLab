"""长期问题库的读写。

写路径原先内联在 `routers/problems.py` 里。M6 第二步把它抽出来，让 Agent 工具
与 HTTP 路由共用同一条落库路径 —— 否则 Agent 只能内部发 HTTP 才能建问题。
`analysis_service.commit` 的 AI 候选写入也走这里。

事务边界：`create` 只 flush，**commit 由调用方决定** —— 剖析的「问题候选 + 背景
候选」要在一次事务里落地，service 自己 commit 会把它劈成两半。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.problem import Problem, ProblemStatus


def get(db: Session, problem_id: int, user_id: int) -> Problem | None:
    return db.scalar(
        select(Problem).where(Problem.id == problem_id, Problem.user_id == user_id)
    )


def list_for_user(
    db: Session,
    user_id: int,
    *,
    status: str | None = None,
    limit: int | None = None,
) -> Sequence[Problem]:
    stmt = (
        select(Problem)
        .where(Problem.user_id == user_id)
        .order_by(Problem.updated_at.desc())
    )
    if status:
        stmt = stmt.where(Problem.status == status.upper())
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.scalars(stmt).all()


def create(
    db: Session,
    *,
    user_id: int,
    title: str,
    category: str | None = None,
    note: str | None = None,
    source: str = "manual",
    source_ref: str | None = None,
) -> Problem:
    """建一个问题（flush 但**不 commit**，见模块 docstring）。"""
    problem = Problem(
        user_id=user_id,
        title=title,
        category=category,
        note=note,
        status=ProblemStatus.OPEN.value,
        source=source,
        source_ref=source_ref,
    )
    db.add(problem)
    db.flush()
    return problem
