from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.deps import DbDep, UserDep
from app.schemas.app_rule import UnclassifiedApp
from app.schemas.device import (
    DeviceCreate,
    DeviceCreateResponse,
    DeviceRead,
    KnownApp,
    UsageHourlyDayResponse,
    UsageHourlyItem,
    UsageOverviewDevice,
    UsageOverviewResponse,
)
from app.services import behavior_service, device_service, rebuild_queue

router = APIRouter(prefix="/devices", tags=["devices"])

# 日期查询参数统一用这个：格式错了由 FastAPI 直接 422，路由里不必再 parse
DayQuery = Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]


@router.post("", response_model=DeviceCreateResponse, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: DbDep, current_user: UserDep):
    """创建设备并返回明文 token（仅此一次，采集器需保存）。"""
    device, token = device_service.create_device(
        db, name=payload.name, platform=payload.platform, user_id=current_user.id
    )
    return DeviceCreateResponse(
        device=DeviceRead.model_validate(device), token=token
    )


@router.get("", response_model=list[DeviceRead])
def list_devices(db: DbDep, current_user: UserDep):
    return device_service.list_devices(db, current_user.id)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, db: DbDep, current_user: UserDep):
    """吊销设备（连带删除其上报的使用数据）。"""
    if not device_service.delete_device(db, device_id, current_user.id):
        raise HTTPException(status_code=404, detail="device not found")


@router.post("/rebuild-activity")
def rebuild_activity(
    db: DbDep,
    current_user: UserDep,
    date: str = Query(description="YYYY-MM-DD"),
    tz_offset: int = Query(default=0, ge=-840, le=840),
):
    """按当前会话/小时数据重算某日所有设备的行为段（DEVICE 事件）。

    改分类规则或补数据后用；幂等：先删该日 DEVICE 事件再重建。
    """
    result = behavior_service.rebuild_for_date(
        db, date, user_id=current_user.id, tz_offset=tz_offset
    )
    return {"date": date, **result}


@router.post("/rebuild-activity-range", status_code=status.HTTP_202_ACCEPTED)
def rebuild_activity_range(
    background: BackgroundTasks,
    start: DayQuery,
    end: DayQuery,
    current_user: UserDep,
    tz_offset: int = Query(default=0, ge=-840, le=840),
):
    """重算 [start, end]（含两端）所有设备的行为段，**走后台任务**。

    改分类规则后一次补齐历史用。上限 `MAX_REBUILD_DAYS`（90）天，超出 422；
    区间非法同样 422（入队前先同步校验，不会静默失败）。
    返回 202：任务已入队，不等待完成——前端随后刷新即可看到已算完的部分。
    """
    try:
        days = behavior_service.plan_range(start, end)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    # user_id 必须在请求期捉成普通 int：后台任务自开 Session，拿不到 current_user
    background.add_task(
        rebuild_queue.rebuild_activity_range,
        start,
        end,
        user_id=current_user.id,
        tz_offset=tz_offset,
    )
    return {"queued": True, "start": start, "end": end, "days": len(days)}


@router.get("/uncategorized-apps", response_model=list[UnclassifiedApp])
def uncategorized_apps(
    db: DbDep,
    current_user: UserDep,
    start: DayQuery,
    end: DayQuery,
    min_seconds: int = Query(default=60, ge=0),
):
    """[start, end] 内仍没被认出大类的应用，按时长降序（供逐条标记）。"""
    return device_service.uncategorized_apps(
        db, start, end, min_seconds=min_seconds, user_id=current_user.id
    )


@router.get("/known-apps", response_model=list[KnownApp])
def known_apps(
    db: DbDep,
    current_user: UserDep,
    start: DayQuery,
    end: DayQuery,
    limit: int = Query(default=60, ge=1, le=200),
):
    """[start, end] 内用过的全部应用/网站 key，按时长降序。

    与 `/uncategorized-apps` 口径不同：这里**要**站点行（`web:<域名>`），
    因为指标的 `usage_duration:<app>` 里也能写站点 —— 这是 UI 里选任意应用/网站
    的唯一来源。
    """
    return device_service.known_apps(
        db, start, end, limit=limit, user_id=current_user.id
    )


@router.get("/usage-hourly/{date}", response_model=UsageHourlyDayResponse)
def usage_hourly_day(date: str, db: DbDep, current_user: UserDep):
    """查看某日的分小时使用时长（供核验时段采集）。"""
    rows = device_service.usage_hourly_for_day(db, date, current_user.id)
    if not rows:
        raise HTTPException(status_code=404, detail="该日无时段数据")
    return UsageHourlyDayResponse(
        date=date,
        total_seconds=sum(r.seconds for r in rows),
        items=[UsageHourlyItem.model_validate(r) for r in rows],
    )


@router.get("/usage-overview/{date}", response_model=UsageOverviewResponse)
def usage_overview(date: str, db: DbDep, current_user: UserDep):
    """某日各设备使用概览（分小时），供只读展示。

    无任何数据返回空列表（不 404），方便前端直接渲染"这一天没有采集数据"。
    """
    rows = device_service.usage_overview_for_day(db, date, current_user.id)
    return UsageOverviewResponse(
        date=date,
        devices=[UsageOverviewDevice(**r) for r in rows],
    )
