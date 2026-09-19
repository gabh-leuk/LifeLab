"""把「区间重算 DEVICE 事件」丢进后台任务。

改分类规则后历史不会自动变——`device_app_sessions` 里存的 `category` 是采集当时的
快照，只有重算才会按当前规则重新判类。规则写接口收到 `rebuild_start`/`rebuild_end`
就调这里入队，请求立刻返回。

后台任务跑在响应之后，**请求作用域的 Session 已经关闭**，所以这里自开 `SessionLocal`。
"""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks

from app.db import SessionLocal
from app.services import behavior_service

logger = logging.getLogger(__name__)


def rebuild_activity_range(
    start: str, end: str, *, user_id: int, tz_offset: int = 0
) -> dict | None:
    """在**新会话**里重算 [start, end] 的 DEVICE 事件；失败只记日志，不影响已返回的响应。

    `user_id` 必须由请求期捕获后传进来：后台任务跑在响应之后，
    此时 `current_user` 依赖已随请求作用域销毁。
    """
    db = SessionLocal()
    try:
        return behavior_service.rebuild_range(
            db, start, end, user_id=user_id, tz_offset=tz_offset
        )
    except ValueError as e:
        logger.warning("区间重算跳过 start=%s end=%s：%s", start, end, e)
        return None
    except Exception:
        logger.exception("区间重算失败 start=%s end=%s", start, end)
        return None
    finally:
        db.close()


def enqueue_range_rebuild(
    background: BackgroundTasks,
    start: str | None,
    end: str | None,
    *,
    user_id: int,
    tz_offset: int = 0,
) -> bool:
    """`start`/`end` 都给齐才入队；返回是否已入队。

    同时先跑一次 `plan_range` 做同步校验：格式错或区间超限时**当场**抛 ValueError
    （路由层转 422），而不是等到后台任务里才悄悄失败。
    """
    if not start or not end:
        return False
    behavior_service.plan_range(start, end)
    background.add_task(
        rebuild_activity_range, start, end, user_id=user_id, tz_offset=tz_offset
    )
    return True
