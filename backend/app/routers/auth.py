"""登录/登出/改密。

不做自助注册：账号由 scripts/create_user.py 预置（部署时用）。
"""

from fastapi import APIRouter, Header, HTTPException, status

from app import ratelimit
from app.deps import DbDep, UserDep
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    PasswordChange,
    TokenRead,
    UserRead,
)
from app.security import verify_password
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _raw_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization[7:].strip()


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: DbDep):
    """用户名 + 密码换登录令牌。失败一律 401（不区分账号不存在与密码错）。

    连续失败达阈值先 429（按用户名计，见 app/ratelimit.py）：公网入口下拿不到
    真实 IP，且撞库的直接动机是烧 LLM 额度。计数在建库查询之前就拦下，锁定期
    连 scrypt 都不跑。
    """
    wait = ratelimit.retry_after(payload.username)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"登录失败次数过多，请 {wait} 秒后再试",
            headers={"Retry-After": str(wait)},
        )
    user = auth_service.authenticate(db, payload.username, payload.password)
    if user is None:
        ratelimit.record_failure(payload.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    ratelimit.clear(payload.username)
    token, expires_at = auth_service.issue_token(db, user, name="网页端")
    return LoginResponse(
        token=token, expires_at=expires_at, user=UserRead.model_validate(user)
    )


@router.get("/me", response_model=UserRead)
def me(current_user: UserDep):
    """校验令牌并返回当前用户（前端启动时用它回填会话）。"""
    return UserRead.model_validate(current_user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(db: DbDep, authorization: str | None = Header(default=None)):
    """登出当前令牌（幂等：令牌已失效也返回 204）。"""
    token = _raw_token(authorization)
    if token:
        auth_service.revoke_token(db, token)


@router.post("/logout-all")
def logout_all(db: DbDep, current_user: UserDep):
    """踢下线：撤销该用户全部令牌（含当前这个）。"""
    return {"revoked": auth_service.revoke_all_tokens(db, current_user.id)}


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChange, db: DbDep, current_user: UserDep):
    """改密后撤销全部令牌 → 所有端需重新登录（改密即踢下线）。"""
    if not verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="原密码不正确"
        )
    auth_service.set_password(db, current_user, payload.new_password)
    auth_service.revoke_all_tokens(db, current_user.id)


@router.get("/tokens", response_model=list[TokenRead])
def list_tokens(db: DbDep, current_user: UserDep):
    """当前有效的登录令牌（供「已登录设备」核验，便于发现异常登录）。"""
    return auth_service.list_tokens(db, current_user.id)
