from fastapi import APIRouter, Query

from app.deps import DbDep, UserDep
from app.schemas.summary import DailySummary
from app.services import summary_service

router = APIRouter(prefix="/summary", tags=["summary"])


@router.get("/{date}", response_model=DailySummary)
def daily_summary(
    date: str,
    db: DbDep,
    current_user: UserDep,
    tz_offset: int = Query(default=0, ge=-840, le=840, description="UTC 偏移分钟，如东八区=480"),
):
    # 与时间线同用本地时区分天，保证统计与时间线一致
    return summary_service.daily_summary(
        db, date, user_id=current_user.id, tz_offset=tz_offset
    )
