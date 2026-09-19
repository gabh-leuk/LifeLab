"""事件类型服务：可记录类型清单（内置 + 自定义）+ 类型 → 行为大类解析。

内置类型不入库（表里只有自定义项），本模块负责把两者合成一份统一清单，
并作为「类型 key 是否合法」的唯一判定入口——`create_event` / `update_event`
都走 `resolve_event_type_category`，解析不出即 422。
"""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.behavior_categories import EVENT_TYPE_CATEGORY
from app.models.event import EventType
from app.models.event_type import CUSTOM_KEY_PREFIX, CustomEventType
from app.schemas.event_type import EventTypeCreate, EventTypeUpdate

# 不可作为记录按钮的内置类型：
# - PHONE_START / GAME_START：在线行为，设备采集已覆盖，手记只会重复
# - SLEEP_START：旧键，已被 BED_START 取代（历史数据仍可读）
# - DEVICE_ACTIVITY：设备采集自动落库的行为段
# 注意：这里只决定「按钮里出现哪些」，EVENT_TYPE_CATEGORY 的映射一律保留，
# 否则历史事件、旧实验指标源（event_duration:GAME_START）会解析不到大类。
HIDDEN_BUILTINS = frozenset(
    {
        EventType.PHONE_START.value,
        EventType.GAME_START.value,
        EventType.SLEEP_START.value,
        EventType.DEVICE_ACTIVITY.value,
    }
)

BUILTIN_LABELS: dict[str, str] = {
    "LEARNING_START": "学习",
    "GAME_START": "游戏",
    "PHONE_START": "手机",
    "MEAL_START": "吃饭",
    "BED_START": "上床",
    "SLEEP_START": "上床",
    "OUT_START": "出门",
    "EXERCISE_START": "运动",
    "CHORES_START": "家务",
    "SOCIAL_OFFLINE_START": "线下社交",
    "OTHER_START": "其他",
    "DEVICE_ACTIVITY": "设备使用",
}


def creatable_builtins() -> list[str]:
    """可作为记录按钮的内置类型 key（顺序即 `EVENT_TYPE_CATEGORY` 的声明顺序）。"""
    return [key for key in EVENT_TYPE_CATEGORY if key not in HIDDEN_BUILTINS]


def _builtin_item(key: str, order: int) -> dict:
    return {
        "key": key,
        "label": BUILTIN_LABELS.get(key, key),
        "category": EVENT_TYPE_CATEGORY[key],
        "icon": None,
        "order": order,
        "id": None,
        "builtin": True,
        "archived": False,
    }


def _custom_item(row: CustomEventType, order: int) -> dict:
    return {
        "key": row.key,
        "label": row.label,
        "category": row.category,
        "icon": row.icon,
        "order": order,
        "id": row.id,
        "builtin": False,
        "archived": row.archived,
    }


def list_event_types(
    db: Session, *, user_id: int, include_archived: bool = False
) -> list[dict]:
    """内置在前（顺序固定），自定义按 sort_order 排在后面。"""
    items = [_builtin_item(key, i) for i, key in enumerate(creatable_builtins())]
    stmt = select(CustomEventType).where(CustomEventType.user_id == user_id)
    if not include_archived:
        stmt = stmt.where(CustomEventType.archived.is_(False))
    rows = db.scalars(stmt.order_by(CustomEventType.sort_order, CustomEventType.id)).all()
    base = len(items)
    items.extend(_custom_item(row, base + i) for i, row in enumerate(rows))
    return items


def resolve_event_type_category(
    db: Session, type_key: str, *, user_id: int
) -> str | None:
    """类型 key → 行为大类；类型不存在（或已归档）返回 None，调用方据此 422。

    内置优先：自定义 key 一律带 `custom_` 前缀，不会与内置撞名。
    """
    builtin = EVENT_TYPE_CATEGORY.get(type_key)
    if builtin is not None:
        return builtin
    row = db.scalar(
        select(CustomEventType).where(
            CustomEventType.user_id == user_id,
            CustomEventType.key == type_key,
            CustomEventType.archived.is_(False),
        )
    )
    return row.category if row is not None else None


def _load(db: Session, type_id: int, user_id: int) -> CustomEventType | None:
    return db.scalar(
        select(CustomEventType).where(
            CustomEventType.id == type_id, CustomEventType.user_id == user_id
        )
    )


def _new_key(db: Session, user_id: int) -> str:
    """生成不冲突的 `custom_<8hex>`；不以 `_START` 结尾（前端据此去后缀取标签）。"""
    for _ in range(10):
        key = f"{CUSTOM_KEY_PREFIX}{secrets.token_hex(4)}"
        exists = db.scalar(
            select(CustomEventType.id).where(
                CustomEventType.user_id == user_id, CustomEventType.key == key
            )
        )
        if exists is None:
            return key
    raise RuntimeError("无法生成唯一的事件类型 key")


def create_event_type(
    db: Session, payload: EventTypeCreate, *, user_id: int
) -> dict:
    orders = db.scalars(
        select(CustomEventType.sort_order).where(CustomEventType.user_id == user_id)
    ).all()
    row = CustomEventType(
        user_id=user_id,
        key=_new_key(db, user_id),
        label=payload.label,
        category=payload.category,
        icon=payload.icon,
        sort_order=(max(orders) + 1) if orders else 0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _custom_item(row, len(creatable_builtins()) + row.sort_order)


def update_event_type(
    db: Session,
    type_id: int,
    payload: EventTypeUpdate,
    *,
    user_id: int,
) -> dict | None:
    """只改显式传入的字段（`icon=None` 表示清空图标）。返回 None = 不存在。"""
    row = _load(db, type_id, user_id)
    if row is None:
        return None
    data = payload.model_dump(exclude_unset=True)
    for field in ("label", "category", "icon", "sort_order", "archived"):
        if field in data:
            setattr(row, field, data[field])
    db.commit()
    db.refresh(row)
    return _custom_item(row, len(creatable_builtins()) + row.sort_order)


def archive_event_type(db: Session, type_id: int, *, user_id: int) -> bool:
    """软删：置 archived=true。事件行保留 type/category 快照，历史不丢。"""
    row = _load(db, type_id, user_id)
    if row is None:
        return False
    row.archived = True
    db.commit()
    return True
