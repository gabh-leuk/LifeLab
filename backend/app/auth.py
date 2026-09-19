"""认证依赖：设备令牌（机器）与登录令牌（人）。

两条链路互不干扰：
- `/ingest/*` 走设备令牌（X-Device-Token），采集器无人值守上传，不要求登录。
- 其余业务接口走登录令牌（Authorization: Bearer），用户身份即数据归属。

设备行自带 user_id，因此设备侧不需要用户会话。

`/ingest/*` 除了采集数据，还有一条**人主动触发**的写入：`POST /ingest/events`
（安卓主屏小组件点一下记一条，`source=MANUAL`）。它走设备令牌只是因为安卓只
持有设备令牌，不改变「谁按的钮」这个语义 —— 归属仍由设备行的 user_id 决定。
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.device import Device
from app.models.user import User
from app.services import auth_service, device_service

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def get_current_device(
    db: Annotated[Session, Depends(get_db)],
    x_device_token: Annotated[str | None, Header()] = None,
) -> Device:
    if not x_device_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 X-Device-Token 请求头",
        )
    device = device_service.get_device_by_token(db, x_device_token)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="设备 token 无效",
        )
    return device


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Authorization: Bearer <token> → User。

    统一 401：不区分「没带」「令牌无效」「已撤销」「已过期」，避免给探测者信息。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或格式错误的 Authorization 请求头",
            headers=_UNAUTHORIZED_HEADERS,
        )
    row = auth_service.resolve_token(db, authorization[7:].strip())
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers=_UNAUTHORIZED_HEADERS,
        )
    return row.user
