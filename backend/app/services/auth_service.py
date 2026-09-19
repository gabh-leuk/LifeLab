"""账号与登录令牌服务。

令牌语义：有效 = 未撤销 AND 未过期 AND 用户未停用（见 resolve_token）。
明文只在签发时返回一次，库中存 sha256——与 device token 同一套做法。
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.models.user import AuthToken, User
from app.security import hash_password, hash_token, new_token, verify_password
from app.utils import ensure_aware

# 登录令牌默认有效期（天）；None 表示永不过期
DEFAULT_TOKEN_DAYS = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username.strip()))


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    is_demo: bool = False,
    display_name: str | None = None,
) -> User:
    user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        is_demo=is_demo,
        display_name=display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_password(db: Session, user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    db.commit()


def authenticate(db: Session, username: str, password: str) -> User | None:
    """校验用户名密码；停用账号一律拒绝（不区分「不存在」与「密码错」）。"""
    user = get_by_username(db, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def issue_token(
    db: Session,
    user: User,
    *,
    name: str | None = None,
    days: int | None = DEFAULT_TOKEN_DAYS,
) -> tuple[str, datetime | None]:
    """签发令牌，返回 (明文, 过期时间)。明文仅此一次；days=None 为永不过期。"""
    token = new_token()
    expires_at = _utcnow() + timedelta(days=days) if days is not None else None
    db.add(
        AuthToken(
            user_id=user.id,
            token_hash=hash_token(token),
            name=name,
            expires_at=expires_at,
        )
    )
    user.last_login_at = _utcnow()
    db.commit()
    return token, expires_at


def resolve_token(db: Session, token: str) -> AuthToken | None:
    """令牌 → AuthToken 行（带 user）。

    三重条件缺一不可：未撤销、未过期、用户未停用。
    命中则顺带刷新 last_used_at（同一分钟内不重复写，省一次 UPDATE）。
    """
    row = db.scalar(
        select(AuthToken)
        .options(joinedload(AuthToken.user))
        .where(
            AuthToken.token_hash == hash_token(token),
            AuthToken.revoked_at.is_(None),
        )
    )
    if row is None or not row.user.is_active:
        return None
    if row.expires_at is not None and ensure_aware(row.expires_at) <= _utcnow():
        return None

    now = _utcnow()
    if row.last_used_at is None or (now - ensure_aware(row.last_used_at)).total_seconds() > 60:
        row.last_used_at = now
        db.commit()
    return row


def revoke_token(db: Session, token: str) -> bool:
    """登出：撤销单个令牌。返回是否真的撤销了一个有效令牌。"""
    row = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == hash_token(token),
            AuthToken.revoked_at.is_(None),
        )
    )
    if row is None:
        return False
    row.revoked_at = _utcnow()
    db.commit()
    return True


def revoke_all_tokens(db: Session, user_id: int) -> int:
    """踢下线：撤销该用户所有未撤销令牌。"""
    result = db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user_id, AuthToken.revoked_at.is_(None))
        .values(revoked_at=_utcnow())
    )
    db.commit()
    return result.rowcount or 0


def list_tokens(db: Session, user_id: int) -> list[AuthToken]:
    """该用户当前有效的令牌（供「已登录设备」展示）。"""
    now = _utcnow()
    rows = db.scalars(
        select(AuthToken)
        .where(AuthToken.user_id == user_id, AuthToken.revoked_at.is_(None))
        .order_by(AuthToken.created_at.desc())
    ).all()
    return [
        r
        for r in rows
        if r.expires_at is None or ensure_aware(r.expires_at) > now
    ]
