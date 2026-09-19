from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.deps import DbDep, UserDep
from app.schemas.app_rule import AppRuleCreate, AppRuleItem, AppRuleUpdate
from app.services import app_rule_service, rebuild_queue

router = APIRouter(prefix="/app-rules", tags=["app-rules"])

# 可选的「顺手重算」参数：规则写完之后历史 DEVICE 事件不会自己变，
# 前端把当前视图的日期区间带上，后端入队一个后台重算（见 rebuild_queue）。
RebuildStart = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]
RebuildEnd = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]
TzOffset = Annotated[int, Query(ge=-840, le=840)]


def _maybe_rebuild(
    background: BackgroundTasks,
    start: str | None,
    end: str | None,
    tz_offset: int,
    user_id: int,
) -> None:
    try:
        rebuild_queue.enqueue_range_rebuild(
            background, start, end, user_id=user_id, tz_offset=tz_offset
        )
    except ValueError as e:
        # 规则本身已存下来了，只是重算区间不合法——别把整个请求判失败
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("", response_model=list[AppRuleItem])
def list_app_rules(db: DbDep, current_user: UserDep):
    """用户自定义的应用/域名分类规则（优先级从高到低）。"""
    return app_rule_service.list_rules(db, current_user.id)


@router.post("", response_model=AppRuleItem, status_code=status.HTTP_201_CREATED)
def create_app_rule(
    payload: AppRuleCreate,
    background: BackgroundTasks,
    db: DbDep,
    current_user: UserDep,
    rebuild_start: RebuildStart = None,
    rebuild_end: RebuildEnd = None,
    tz_offset: TzOffset = 0,
):
    """新增规则；同 (match_type, scope, match_value) 已存在则覆盖其分类/别名。

    带上 `rebuild_start`/`rebuild_end` 就顺手入队一个区间重算——
    否则已采集的历史 DEVICE 事件仍按旧规则，改规则对过去完全无效。
    """
    item = app_rule_service.create_rule(db, payload, current_user.id)
    _maybe_rebuild(background, rebuild_start, rebuild_end, tz_offset, current_user.id)
    return item


@router.patch("/{rule_id}", response_model=AppRuleItem)
def update_app_rule(
    rule_id: int,
    payload: AppRuleUpdate,
    background: BackgroundTasks,
    db: DbDep,
    current_user: UserDep,
    rebuild_start: RebuildStart = None,
    rebuild_end: RebuildEnd = None,
    tz_offset: TzOffset = 0,
):
    result = app_rule_service.update_rule(db, rule_id, payload, current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="app rule not found")
    _maybe_rebuild(background, rebuild_start, rebuild_end, tz_offset, current_user.id)
    return result


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app_rule(
    rule_id: int,
    background: BackgroundTasks,
    db: DbDep,
    current_user: UserDep,
    rebuild_start: RebuildStart = None,
    rebuild_end: RebuildEnd = None,
    tz_offset: TzOffset = 0,
):
    """删除规则（分类回到内置词表判定）。"""
    if not app_rule_service.delete_rule(db, rule_id, current_user.id):
        raise HTTPException(status_code=404, detail="app rule not found")
    _maybe_rebuild(background, rebuild_start, rebuild_end, tz_offset, current_user.id)
