import os
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ratelimit
from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app

# 默认以 demo(id=1) 身份访问 API。
# 必须是 1：大量存量测试绕过 API 直接构造 user_id=1 的模型，
# 若 stub 用别的 id，这些测试会静默查空、假绿。
STUB_USER_ID = 1


@pytest.fixture
def db_session(tmp_path):
    """每测试独立临时库；若被 client 依赖则共享同一库。"""
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    session = TestingSessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _stub_current_user():
    """所有测试自动通过登录校验；跨用户测试用 login_as 切换身份。

    默认 **is_demo=False**：桩代表「一个正常登录用户」。身份 id 取 1 是因为
    大量存量测试直接构造 user_id=1 的模型，与是否 demo 无关。默认 True 的话
    每次碰 AI 端点都会撞上 demo 封锁（app/deps.py 的 require_ai_enabled），
    逼得所有 AI 测试都去改桩 —— 那是把默认值设错了。
    要测封锁行为，用 as_demo。
    """

    def _stub():
        return SimpleNamespace(id=STUB_USER_ID, username="demo", is_demo=False)

    app.dependency_overrides[get_current_user] = _stub
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def login_as():
    """切换当前登录用户 id：login_as(2) 即模拟个人账号访问。"""

    def _set(uid: int):
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=uid, username=f"u{uid}", is_demo=False
        )

    return _set


@pytest.fixture
def as_demo():
    """把当前身份切成 demo 演示账号（is_demo=True），用于验证 AI 封锁。"""

    def _set():
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=STUB_USER_ID, username="demo", is_demo=True
        )

    return _set


@pytest.fixture
def client(db_session):
    """基于 db_session 的 TestClient，可单独或与 db_session 组合使用。"""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    # 只清理自己装的那个：不能用 clear()，否则会连 autouse 的登录 stub 一起清掉
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _reset_login_ratelimit():
    """限流计数是模块级内存状态，会跨测试累积。

    不重置的话，「连错 5 次」的用例会把 demo 打锁，后面所有登录用例集体 429。
    """
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture(autouse=True)
def _hermetic_context_query(monkeypatch):
    """测试密封：context_service 的检索查询生成默认走确定性桩，绝不触网。

    需要验证真实行为的测试可在用例内再次 monkeypatch 覆盖此桩。
    """
    monkeypatch.setattr(
        "app.services.context_service.chat_json",
        lambda *a, **k: {"query": "测试检索查询"},
    )


@pytest.fixture(autouse=True)
def _hermetic_llm_trace(monkeypatch, tmp_path):
    """测试密封：LLM 埋点写进临时目录，绝不碰真实的 ~/.lifelab/llm_calls.jsonl。

    真实埋点只在「有人真发起 LLM 调用」时才写，但开发机的 trace 文件是个人使用
    痕迹，不该被跑测试污染；顺带也让 test_llm_trace 有个干净的起点。
    """
    monkeypatch.setattr("app.llm._trace_path", lambda: tmp_path / "llm_calls.jsonl")
