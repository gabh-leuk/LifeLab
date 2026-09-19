"""demo 每日重置的保留语义。

那 10 篇日报 / 2 篇周报 / 1 篇月报 / 33 条记忆是用**真实 LLM** 跑出来的，
seed_demo 不产它们——清掉就回不来，而它们正是 demo 的展示主体。所以重置默认
keep 这三张表。这里钉住「保留了哪些、清掉了哪些、没碰谁」，以及 keep 的名字
确实对得上真表（打错字会静默失效：名字对不上就不是「保留」，而是照删）。
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reset_demo
import seed_demo
from sqlalchemy import select

from app.db import Base
from app.models import AgentThread, DailyReview, Device, Event, MemoryItem, PeriodReview
from app.services import device_service


def _seed(db, uid: int) -> None:
    db.add(DailyReview(user_id=uid, date="2026-09-01", review_text="日报正文"))
    db.add(
        PeriodReview(
            user_id=uid,
            period_type="week",
            period_key="2026-W36",
            period_start="2026-08-31",
            period_end="2026-09-06",
            review_text="周报正文",
        )
    )
    db.add(MemoryItem(user_id=uid, kind="insight", content="一条洞察"))
    ts = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    db.add(Event(user_id=uid, timestamp=ts, type="LEARNING_START", ended_at=ts))
    device_service.create_device(db, name="测试机", platform="pc", user_id=uid)
    db.commit()


def _count(db, model, uid: int | None = None) -> int:
    stmt = select(model)
    if uid is not None:
        stmt = stmt.where(model.user_id == uid)
    return len(db.scalars(stmt).all())


def test_wipe_without_keep_clears_everything(db_session):
    """不传 keep 就是原来的行为：连 AI 产物一起清（seed_demo --user 走这条）。"""
    _seed(db_session, 1)
    seed_demo.wipe(db_session, 1)
    for model in (DailyReview, PeriodReview, MemoryItem, Event, Device):
        assert _count(db_session, model) == 0, model.__name__


def test_wipe_keep_preserves_ai_artifacts_and_clears_the_rest(db_session):
    _seed(db_session, 1)
    seed_demo.wipe(db_session, 1, keep=seed_demo.AI_ARTIFACT_TABLES)

    for model in (DailyReview, PeriodReview, MemoryItem):
        assert _count(db_session, model) == 1, f"{model.__name__} 被误清"
    for model in (Event, Device):
        assert _count(db_session, model) == 0, f"{model.__name__} 没被清"


def test_wipe_never_touches_other_users(db_session):
    """重置 demo 不能动个人账号 —— 每一张表都按 user_id 验一遍。"""
    _seed(db_session, 1)
    _seed(db_session, 2)

    seed_demo.wipe(db_session, 1, keep=seed_demo.AI_ARTIFACT_TABLES)

    for model in (DailyReview, PeriodReview, MemoryItem, Event, Device):
        assert _count(db_session, model, uid=2) == 1, f"动了个人账号的 {model.__name__}"
    # demo 自己的非保留表已清空（保留表另有测试）
    assert _count(db_session, Event, uid=1) == 0
    assert _count(db_session, Device, uid=1) == 0


def test_ai_artifact_table_names_are_real_and_relevant():
    """名字打错会静默失效：keep 对不上就变成照删，而测试如果只数「还剩几行」
    会直接红——但若是把名字写成一张不存在的表，DELETEs 会全跑、假绿。"""
    for name in seed_demo.AI_ARTIFACT_TABLES:
        assert name in Base.metadata.tables, f"{name} 不是已知表"
        assert name in seed_demo.BUSINESS_TABLES, f"{name} 不在 BUSINESS_TABLES 里，keep 无意义"


def test_reset_demo_defaults_to_keeping_ai_artifacts():
    """默认必须保留，--full 才是清。反了的话每天 03:17 会悄悄清空 demo 展示。"""
    assert reset_demo.keep_tables(full=False) == tuple(seed_demo.AI_ARTIFACT_TABLES)
    assert reset_demo.keep_tables(full=True) == ()


def test_agent_replays_are_regenerated_not_preserved(db_session):
    """Agent 回放是**本脚本种的内容**，不是要保的 AI 产物：重置必须清掉再重种。

    放保留区会撞 `thread_id` 唯一约束（回放用确定性命名），也会留下对不上的旧文案。
    """
    seed_demo.seed_agent_replays(db_session, 1)
    n = len(seed_demo.AGENT_REPLAYS)
    assert _count(db_session, AgentThread, 1) == n

    seed_demo.wipe(db_session, 1, keep=seed_demo.AI_ARTIFACT_TABLES)
    assert _count(db_session, AgentThread, 1) == 0, "回放不该被当成保留的 AI 产物"

    # 重种不撞唯一约束 —— 这条就是「放进 BUSINESS_TABLES」这个决定的验收条件
    seed_demo.seed_agent_replays(db_session, 1)
    assert _count(db_session, AgentThread, 1) == n


def test_agent_replays_never_touch_other_users(db_session):
    seed_demo.seed_agent_replays(db_session, 1)
    seed_demo.seed_agent_replays(db_session, 2)

    seed_demo.wipe(db_session, 1, keep=seed_demo.AI_ARTIFACT_TABLES)

    assert _count(db_session, AgentThread, 2) == len(seed_demo.AGENT_REPLAYS)
