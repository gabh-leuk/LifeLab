"""共享依赖别名。

此前 16 个 router 各自写一遍 `DbDep = Annotated[Session, Depends(get_db)]`。
引入用户依赖后若继续各自定义会再复制一遍，故统一收在这里。
认证逻辑仍在 app/auth.py，这里只放别名。
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_device, get_current_user
from app.db import get_db
from app.models.device import Device
from app.models.user import User

DbDep = Annotated[Session, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]
DeviceDep = Annotated[Device, Depends(get_current_device)]

AI_DEMO_NOTICE = (
    "演示账号不开放 AI 生成：这是公共 demo，AI 生成会消耗开发者额度。"
    "预置的 10 篇日报 / 2 篇周报 / 1 篇月报 / 33 条记忆可直接查看。"
)


def require_ai_enabled(user: UserDep) -> User:
    """AI 端点的守卫：demo 账号一律 403。

    demo 是公网可写的，任何人都能反复触发 AI 生成——每次都是真实的 DeepSeek
    调用（10 天日报就是 10 次），额度会被陌生人和爬虫烧掉。而演示并不需要现场
    跑 AI：产物已经预置好了，静态展示反而更快更好看。

    拒绝信息写成人话并给出替代，因为 404/403 对访客太冷。
    """
    if user.is_demo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=AI_DEMO_NOTICE
        )
    return user


AiUserDep = Annotated[User, Depends(require_ai_enabled)]
