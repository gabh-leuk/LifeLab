"""登录、令牌生命周期、改密、设备端与用户端的鉴权边界。

令牌语义的三重条件（未撤销 / 未过期 / 用户未停用）逐个钉住——
漏掉任何一条都是一个可用后门。
"""

import pytest

from app import ratelimit
from app.auth import get_current_user
from app.main import app
from app.security import hash_password, hash_token, verify_password
from app.services import auth_service, device_service


@pytest.fixture(autouse=True)
def _use_real_auth():
    """本模块测的是真鉴权，必须卸掉 conftest 的全局登录桩。

    不卸的话每个请求都直接拿到 stub 用户，token 校验根本没跑——全绿但全是假的。
    test_me_requires_header 是这条前提的自检：桩若还在，它会返回 200。
    """
    app.dependency_overrides.pop(get_current_user, None)
    yield


@pytest.fixture
def demo_user(db_session):
    return auth_service.create_user(
        db_session, username="demo", password="demo1234", is_demo=True
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── 密码哈希 ──────────────────────────────────────────


def test_password_roundtrip():
    stored = hash_password("hunter2")
    assert stored.startswith("scrypt$")
    assert verify_password("hunter2", stored) is True
    assert verify_password("hunter3", stored) is False


def test_password_salt_differs_each_time():
    a, b = hash_password("same"), hash_password("same")
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)


def test_verify_rejects_malformed_hash():
    for bad in ("", "plaintext", "scrypt$1$2", "md5$a$b$c$d$e"):
        assert verify_password("x", bad) is False


def test_hash_token_is_sha256_hex():
    h = hash_token("abc")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


# ── 登录 ────────────────────────────────────────────


def test_login_success(client, demo_user):
    r = client.post("/auth/login", json={"username": "demo", "password": "demo1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"]
    assert body["user"]["username"] == "demo"
    assert body["user"]["is_demo"] is True
    assert body["expires_at"] is not None


def test_login_wrong_password(client, demo_user):
    r = client.post("/auth/login", json={"username": "demo", "password": "nope"})
    assert r.status_code == 401


def test_login_unknown_user_same_error(client, demo_user):
    """账号不存在与密码错返回同一句话，不给探测者信息。"""
    a = client.post("/auth/login", json={"username": "demo", "password": "x"})
    b = client.post("/auth/login", json={"username": "ghost", "password": "x"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_login_inactive_user_rejected(client, db_session):
    user = auth_service.create_user(db_session, username="off", password="pw12345678")
    user.is_active = False
    db_session.commit()
    r = client.post("/auth/login", json={"username": "off", "password": "pw12345678"})
    assert r.status_code == 401


def test_login_is_case_and_space_tolerant_on_username(client, demo_user):
    r = client.post("/auth/login", json={"username": "  demo  ", "password": "demo1234"})
    assert r.status_code == 200


# ── 登录失败限流 ──────────────────────────────────────


def _wrong(username: str = "demo", password: str = "nope"):
    return {"username": username, "password": password}


def test_login_locks_after_max_failures(client, demo_user):
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.post("/auth/login", json=_wrong()).status_code == 401

    r = client.post("/auth/login", json=_wrong())
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0

    # 锁定期内连正确口令也不放行 —— 否则「猜中就直接登录」把限流绕过去了
    assert client.post(
        "/auth/login", json=_wrong(password="demo1234")
    ).status_code == 429


def test_login_lock_is_per_username(client, db_session, demo_user):
    """把一个账号打锁，不能连累另一个账号。"""
    auth_service.create_user(db_session, username="other", password="pw12345678")
    for _ in range(ratelimit.MAX_FAILURES + 1):
        client.post("/auth/login", json=_wrong("demo"))
    assert client.post("/auth/login", json=_wrong("demo")).status_code == 429
    assert client.post(
        "/auth/login", json=_wrong("other", "pw12345678")
    ).status_code == 200


def test_login_success_clears_failure_count(client, demo_user):
    for _ in range(ratelimit.MAX_FAILURES - 1):
        assert client.post("/auth/login", json=_wrong()).status_code == 401
    assert client.post(
        "/auth/login", json=_wrong(password="demo1234")
    ).status_code == 200

    # 计数已清零：再错 MAX_FAILURES-1 次仍应是 401，而不是接着攒到 429
    for _ in range(ratelimit.MAX_FAILURES - 1):
        assert client.post("/auth/login", json=_wrong()).status_code == 401


def test_login_lock_key_ignores_case_and_space(client, demo_user):
    """换大小写/加空格不能刷新计数，否则限流形同虚设。"""
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.post("/auth/login", json=_wrong("  DEMO  ")).status_code == 401
    assert client.post("/auth/login", json=_wrong("demo")).status_code == 429


def test_retry_after_expires_when_window_slides():
    """窗口滑过最早那次失败就解锁（用注入的 now，不碰真时钟）。"""
    ratelimit.reset()
    base = 1000.0
    for i in range(ratelimit.MAX_FAILURES):
        ratelimit.record_failure("x", now=base + i)
    assert ratelimit.retry_after("x", now=base + ratelimit.MAX_FAILURES) > 0
    assert ratelimit.retry_after("x", now=base + ratelimit.WINDOW_SECONDS) == 0


def test_limiter_key_table_is_bounded():
    """拿随机用户名喷洒不能把计数表撑爆。"""
    ratelimit.reset()
    for i in range(ratelimit.MAX_KEYS + 50):
        ratelimit.record_failure(f"spray-{i}", now=1000.0)
    assert len(ratelimit._failures) <= ratelimit.MAX_KEYS


# ── 令牌校验 ─────────────────────────────────────────


def test_me_requires_header(client, demo_user):
    r = client.get("/auth/me")
    assert r.status_code == 401
    assert "Bearer" in r.headers.get("www-authenticate", "")


def test_me_with_valid_token(client, db_session, demo_user):
    token, _ = auth_service.issue_token(db_session, demo_user)
    r = client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 200
    assert r.json()["username"] == "demo"


def test_me_rejects_garbage_and_malformed_header(client, demo_user):
    assert client.get("/auth/me", headers=_bearer("nonsense")).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Token abc"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer"}).status_code == 401


def test_expired_token_rejected(client, db_session, demo_user):
    token, expires_at = auth_service.issue_token(db_session, demo_user, days=-1)
    assert expires_at is not None
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401


def test_token_without_expiry_is_valid(client, db_session, demo_user):
    token, expires_at = auth_service.issue_token(db_session, demo_user, days=None)
    assert expires_at is None
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 200


def test_token_rejected_when_user_deactivated(client, db_session, demo_user):
    token, _ = auth_service.issue_token(db_session, demo_user)
    demo_user.is_active = False
    db_session.commit()
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401


# ── 登出 ────────────────────────────────────────────


def test_logout_invalidates_token(client, db_session, demo_user):
    token, _ = auth_service.issue_token(db_session, demo_user)
    headers = _bearer(token)
    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_logout_is_idempotent(client, demo_user):
    """已失效的令牌再登出也返回 204（前端不必处理这种边界）。"""
    r = client.post("/auth/logout", headers=_bearer("whatever"))
    assert r.status_code == 204


def test_logout_does_not_touch_other_tokens(client, db_session, demo_user):
    token_a, _ = auth_service.issue_token(db_session, demo_user, name="A")
    token_b, _ = auth_service.issue_token(db_session, demo_user, name="B")
    client.post("/auth/logout", headers=_bearer(token_a))
    assert client.get("/auth/me", headers=_bearer(token_b)).status_code == 200


def test_logout_all_revokes_every_token(client, db_session, demo_user):
    token_a, _ = auth_service.issue_token(db_session, demo_user)
    token_b, _ = auth_service.issue_token(db_session, demo_user)
    r = client.post("/auth/logout-all", headers=_bearer(token_a))
    assert r.status_code == 200
    assert r.json()["revoked"] == 2
    for t in (token_a, token_b):
        assert client.get("/auth/me", headers=_bearer(t)).status_code == 401


def test_logout_all_is_scoped_to_own_user(client, db_session, demo_user):
    """踢下线只影响自己：另一个账号的令牌不受影响。"""
    other = auth_service.create_user(db_session, username="other", password="pw12345678")
    mine, _ = auth_service.issue_token(db_session, demo_user)
    theirs, _ = auth_service.issue_token(db_session, other)
    client.post("/auth/logout-all", headers=_bearer(mine))
    assert client.get("/auth/me", headers=_bearer(theirs)).status_code == 200


# ── 改密 ────────────────────────────────────────────


def test_change_password_requires_old_password(client, db_session, demo_user):
    token, _ = auth_service.issue_token(db_session, demo_user)
    r = client.post(
        "/auth/password",
        json={"old_password": "wrong", "new_password": "brandnew123"},
        headers=_bearer(token),
    )
    assert r.status_code == 400


def test_change_password_revokes_all_tokens(client, db_session, demo_user):
    token, _ = auth_service.issue_token(db_session, demo_user)
    headers = _bearer(token)
    r = client.post(
        "/auth/password",
        json={"old_password": "demo1234", "new_password": "brandnew123"},
        headers=headers,
    )
    assert r.status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post(
        "/auth/login", json={"username": "demo", "password": "brandnew123"}
    ).status_code == 200
    assert client.post(
        "/auth/login", json={"username": "demo", "password": "demo1234"}
    ).status_code == 401


def test_short_new_password_rejected(client, db_session, demo_user):
    token, _ = auth_service.issue_token(db_session, demo_user)
    r = client.post(
        "/auth/password",
        json={"old_password": "demo1234", "new_password": "short"},
        headers=_bearer(token),
    )
    assert r.status_code == 422


# ── 令牌清单 ─────────────────────────────────────────


def test_list_tokens_excludes_revoked_and_expired(client, db_session, demo_user):
    auth_service.issue_token(db_session, demo_user, name="keep")
    dead, _ = auth_service.issue_token(db_session, demo_user, name="dead")
    auth_service.issue_token(db_session, demo_user, name="expired", days=-1)
    auth_service.revoke_token(db_session, dead)

    token, _ = auth_service.issue_token(db_session, demo_user, name="current")
    r = client.get("/auth/tokens", headers=_bearer(token))
    assert r.status_code == 200
    assert {t["name"] for t in r.json()} == {"keep", "current"}


def test_list_tokens_scoped_to_own_user(client, db_session, demo_user):
    other = auth_service.create_user(db_session, username="other", password="pw12345678")
    auth_service.issue_token(db_session, other, name="别人的")
    token, _ = auth_service.issue_token(db_session, demo_user, name="我的")
    r = client.get("/auth/tokens", headers=_bearer(token))
    assert {t["name"] for t in r.json()} == {"我的"}


# ── 边界：设备端不走用户鉴权 ──────────────────────────


def test_ingest_needs_device_token_not_bearer(client, db_session, demo_user):
    """采集器只带 X-Device-Token，没有 Authorization 也必须能上报。

    给 /ingest/* 顺手加 UserDep 会直接打断无人值守的采集器——这条是那个的护栏。
    """
    _, token = device_service.create_device(
        db_session, name="测试机", platform="pc", user_id=1
    )
    body = {"date": "2026-09-06", "entries": [{"app": "chrome.exe", "hour": 9, "seconds": 600}]}

    assert client.post("/ingest/usage/hourly", json=body).status_code == 401
    r = client.post("/ingest/usage/hourly", json=body, headers={"X-Device-Token": token})
    assert r.status_code == 200, r.text
    assert r.json()["received"] == 1
