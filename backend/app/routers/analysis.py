from fastapi import APIRouter, HTTPException

from app.deps import AiUserDep, DbDep, UserDep
from app.llm import LLMError
from app.schemas.analysis import (
    CommitRequest,
    CommitResponse,
    DistillRequest,
    DistillResponse,
    ExperimentDraftRequest,
    ExperimentDraftResponse,
)
from app.services import analysis_service, experiment_design_service

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/distill", response_model=DistillResponse)
def distill_analysis(payload: DistillRequest, db: DbDep, current_user: AiUserDep):
    """把一次问答精炼成问题候选 + 背景候选（只返回，不落库）。消耗额度 → demo 挡下。"""
    try:
        problems, backgrounds = analysis_service.distill(
            db, payload.question, payload.answer, user_id=current_user.id
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"AI 提炼失败：{e}")
    return DistillResponse(
        problem_candidates=problems, background_candidates=backgrounds
    )


@router.post("/commit", response_model=CommitResponse)
def commit_analysis(payload: CommitRequest, db: DbDep, current_user: UserDep):
    """授权写入：把用户勾选的问题候选/背景候选落库（source=ai）。"""
    problem_ids, fact_ids, p_skipped, b_skipped = analysis_service.commit(
        db, payload.problems, payload.background, user_id=current_user.id
    )
    return CommitResponse(
        problem_ids=problem_ids,
        profile_fact_ids=fact_ids,
        problems_skipped=p_skipped,
        background_skipped=b_skipped,
    )


@router.post("/experiment-draft", response_model=ExperimentDraftResponse)
def design_experiment_draft(
    payload: ExperimentDraftRequest, db: DbDep, current_user: AiUserDep
):
    """从长期问题生成实验草稿（只返回，不落库；用户确认后走 POST /experiments）。

    消耗额度 → demo 挡下。
    """
    try:
        draft = experiment_design_service.design_experiment(
            db, payload.problem_id, user_id=current_user.id
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"AI 设计失败：{e}")
    if draft is None:
        raise HTTPException(status_code=404, detail="problem not found")
    return draft
