import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import get_current_device
from app.db import get_db
from app.models.device import Device
from app.schemas.device import (
    UsageHourlyIngestRequest,
    UsageIngestResponse,
    UsageSessionIngestRequest,
    UsageSessionIngestResponse,
)
from app.schemas.event import (
    DeviceEventCreate,
    DeviceEventCreateResponse,
    EventRead,
    RecordStatsResponse,
)
from app.schemas.event_type import EventTypeItem
from app.services import (
    behavior_service,
    device_service,
    event_service,
    event_type_service,
    experiment_service,
)
from app.utils import local_today

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

DbDep = Annotated[Session, Depends(get_db)]
DeviceDep = Annotated[Device, Depends(get_current_device)]


def _local_tz_offset_minutes() -> int:
    """聚合 AUTO 数据点用的本地日界线偏移（分钟）。

    采集器上报的 date 是它本地的日期；AUTO 点必须与前端复盘用同一日界线，
    否则同一天会产生两套点。后端与用户同机同时区，故取服务器本地偏移。
    """
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0



def _sync_experiments(db: Session, date: str, user_id: int) -> None:
    """采集数据落地后立即重算该日实验 AUTO 点（幂等）。

    让「睡前手机」这类当日指标无需手动点「立即聚合」就能出点；
    聚合失败不影响上报（数据已提交），只记日志。
    user_id 取自设备行（ingest 走设备认证，没有登录态）。
    """
    try:
        experiment_service.aggregate_day(
            db, date, user_id=user_id, tz_offset=_local_tz_offset_minutes()
        )
    except Exception:
        logger.exception("采集后自动聚合失败 date=%s", date)


@router.post("/usage/hourly", response_model=UsageIngestResponse)
def ingest_usage_hourly(payload: UsageHourlyIngestRequest, db: DbDep, device: DeviceDep):
    """采集器上报某日的分小时应用使用时长（同设备同日整体替换，幂等）。

    每次上报须带上当天**全部小时**（不是只传当前小时），否则会冲掉早先小时。
    请求头：X-Device-Token: <创建设备时返回的明文 token>
    """
    received, replaced = device_service.ingest_usage_hourly(
        db, device, payload.date, payload.entries
    )
    _sync_experiments(db, payload.date, device.user_id)
    return UsageIngestResponse(
        device_id=device.id, date=payload.date, received=received, replaced=replaced
    )


@router.post("/usage/sessions", response_model=UsageSessionIngestResponse)
def ingest_usage_sessions(
    payload: UsageSessionIngestRequest, db: DbDep, device: DeviceDep
):
    """采集器上报某日的精确前台会话（同设备同日整体替换，幂等）。

    落地后立即把会话合并为行为段并重建该设备的 DEVICE 事件（同类人工事件优先）。
    请求头：X-Device-Token: <创建设备时返回的明文 token>
    """
    received, replaced = device_service.ingest_sessions(
        db, device, payload.date, payload.entries
    )
    result = behavior_service.rebuild_device_events(
        db, payload.date, device, tz_offset=_local_tz_offset_minutes()
    )
    _sync_experiments(db, payload.date, device.user_id)
    return UsageSessionIngestResponse(
        device_id=device.id,
        date=payload.date,
        received=received,
        replaced=replaced,
        segments=result["segments"],
    )


# ── 设备侧手动记录（安卓主屏小组件）──────────────────────────
#
# 为什么在 /ingest 而不是复用 POST /events：安卓只持有设备令牌（见 app/auth.py
# 的双链路说明），而 /events 走登录令牌。设备行自带 user_id，所以「谁的数据」
# 依然确定。这里的写路径复用 event_service，与网页端同一套校验与落库逻辑。


@router.post(
    "/events",
    response_model=DeviceEventCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_event(payload: DeviceEventCreate, db: DbDep, device: DeviceDep):
    """设备令牌记一条手动事件（人在手机上按的钮 → source=MANUAL）。

    响应里带回今天的条数与连续天数：小组件点一下就能刷新头部，
    不必再发一次 GET。
    """
    event = event_service.create_event(
        db,
        user_id=device.user_id,
        type=payload.type,
        note=payload.note,
        tags=payload.tags,
    )
    stats = event_service.record_stats(
        db,
        user_id=device.user_id,
        date=local_today(),
        tz_offset=_local_tz_offset_minutes(),
    )
    return DeviceEventCreateResponse(
        event=EventRead.model_validate(event),
        today_count=stats["today_count"],
        streak_days=stats["streak_days"],
    )


@router.get("/record-stats", response_model=RecordStatsResponse)
def ingest_record_stats(db: DbDep, device: DeviceDep):
    """今天的记录条数与连续记录天数（小组件首屏/周期刷新用）。"""
    date = local_today()
    stats = event_service.record_stats(
        db,
        user_id=device.user_id,
        date=date,
        tz_offset=_local_tz_offset_minutes(),
    )
    return RecordStatsResponse(date=date, **stats)


@router.get("/event-types", response_model=list[EventTypeItem])
def ingest_event_types(db: DbDep, device: DeviceDep):
    """类型清单（小组件的按钮就来自这里），与 GET /event-types 同一实现。"""
    return event_type_service.list_event_types(db, user_id=device.user_id)
