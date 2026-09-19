from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import DbDep, UserDep
from app.models.problem import Problem
from app.schemas.problem import ProblemCreate, ProblemRead, ProblemUpdate
from app.services import memory_service, problem_service

router = APIRouter(prefix="/problems", tags=["problems"])


def _get_or_404(db: Session, problem_id: int, user_id: int) -> Problem:
    problem = db.scalar(
        select(Problem).where(Problem.id == problem_id, Problem.user_id == user_id)
    )
    if problem is None:
        raise HTTPException(status_code=404, detail="problem not found")
    return problem


@router.post("", response_model=ProblemRead, status_code=status.HTTP_201_CREATED)
def create_problem(payload: ProblemCreate, db: DbDep, current_user: UserDep):
    problem = problem_service.create(db, user_id=current_user.id, **payload.model_dump())
    db.commit()
    db.refresh(problem)
    return problem


@router.get("", response_model=list[ProblemRead])
def list_problems(
    db: DbDep,
    current_user: UserDep,
    status_filter: str | None = Query(default=None, alias="status"),
):
    return problem_service.list_for_user(
        db, current_user.id, status=status_filter
    )


@router.get("/{problem_id}", response_model=ProblemRead)
def get_problem(problem_id: int, db: DbDep, current_user: UserDep):
    return _get_or_404(db, problem_id, current_user.id)


@router.patch("/{problem_id}", response_model=ProblemRead)
def update_problem(
    problem_id: int, payload: ProblemUpdate, db: DbDep, current_user: UserDep
):
    problem = _get_or_404(db, problem_id, current_user.id)
    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    for k, v in changes.items():
        setattr(problem, k, v)
    db.commit()
    db.refresh(problem)
    return problem


@router.delete("/{problem_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_problem(problem_id: int, db: DbDep, current_user: UserDep):
    problem = _get_or_404(db, problem_id, current_user.id)
    db.delete(problem)
    db.commit()
    memory_service.remove_by_source(db, "problem", problem_id, current_user.id)
