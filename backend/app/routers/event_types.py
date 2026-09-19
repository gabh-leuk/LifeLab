from fastapi import APIRouter, HTTPException, Query, status

from app.deps import DbDep, UserDep
from app.schemas.event_type import EventTypeCreate, EventTypeItem, EventTypeUpdate
from app.services import event_type_service

router = APIRouter(prefix="/event-types", tags=["event-types"])


@router.get("", response_model=list[EventTypeItem])
def list_event_types(
    db: DbDep,
    current_user: UserDep,
    include_archived: bool = Query(default=False, description="含已归档（管理面板用）"),
):
    """内置 + 自定义的合并清单（记录页的按钮就是这个顺序）。"""
    return event_type_service.list_event_types(
        db, user_id=current_user.id, include_archived=include_archived
    )


@router.post("", response_model=EventTypeItem, status_code=status.HTTP_201_CREATED)
def create_event_type(
    payload: EventTypeCreate, db: DbDep, current_user: UserDep
):
    """新建自定义类型；key 由后端生成（`custom_<8hex>`），客户端不指定。"""
    return event_type_service.create_event_type(db, payload, user_id=current_user.id)


@router.patch("/{type_id}", response_model=EventTypeItem)
def update_event_type(
    type_id: int, payload: EventTypeUpdate, db: DbDep, current_user: UserDep
):
    result = event_type_service.update_event_type(
        db, type_id, payload, user_id=current_user.id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="event type not found")
    return result


@router.delete("/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_event_type(type_id: int, db: DbDep, current_user: UserDep):
    """软删（归档）。历史事件的 type/category 是快照，不受影响。"""
    if not event_type_service.archive_event_type(
        db, type_id, user_id=current_user.id
    ):
        raise HTTPException(status_code=404, detail="event type not found")
