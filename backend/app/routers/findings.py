from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import DbDep, UserDep
from app.models.finding import Finding
from app.schemas.finding import FindingCreate, FindingRead, FindingUpdate
from app.services import finding_service, memory_service

router = APIRouter(prefix="/findings", tags=["findings"])


def _get_or_404(db: Session, finding_id: int, user_id: int) -> Finding:
    finding = db.scalar(
        select(Finding).where(Finding.id == finding_id, Finding.user_id == user_id)
    )
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding


@router.post("", response_model=FindingRead, status_code=status.HTTP_201_CREATED)
def create_finding(payload: FindingCreate, db: DbDep, current_user: UserDep):
    finding = finding_service.create(db, payload, user_id=current_user.id)
    db.commit()
    db.refresh(finding)
    return finding


@router.get("", response_model=list[FindingRead])
def list_findings(
    db: DbDep,
    current_user: UserDep,
    problem_id: int | None = Query(default=None, description="按问题过滤"),
    kind: str | None = Query(default=None, description="OBSERVATION/HYPOTHESIS/CONCLUSION"),
):
    return finding_service.list_for_user(
        db, current_user.id, problem_id=problem_id, kind=kind
    )


@router.get("/{finding_id}", response_model=FindingRead)
def get_finding(finding_id: int, db: DbDep, current_user: UserDep):
    return _get_or_404(db, finding_id, current_user.id)


@router.patch("/{finding_id}", response_model=FindingRead)
def update_finding(
    finding_id: int, payload: FindingUpdate, db: DbDep, current_user: UserDep
):
    finding = _get_or_404(db, finding_id, current_user.id)
    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    for k, v in changes.items():
        setattr(finding, k, v)
    db.commit()
    db.refresh(finding)
    return finding


@router.delete("/{finding_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_finding(finding_id: int, db: DbDep, current_user: UserDep):
    finding = _get_or_404(db, finding_id, current_user.id)
    db.delete(finding)
    db.commit()
    memory_service.remove_by_source(db, "finding", finding_id, current_user.id)
