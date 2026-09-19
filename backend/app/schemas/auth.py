from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None
    is_demo: bool


class LoginResponse(BaseModel):
    """登录结果：token 明文只在这里出现一次。"""

    token: str
    expires_at: datetime | None
    user: UserRead


class TokenRead(BaseModel):
    """当前有效的登录令牌（供「已登录设备」展示，不含明文）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)
