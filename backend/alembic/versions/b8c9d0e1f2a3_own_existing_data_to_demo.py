"""存量业务数据归 demo + 清除真实设备痕迹

Revision ID: b8c9d0e1f2a3
Revises: f1a2b3c4d5e6

Create Date: 2026-09-13

**本迁移会删除数据，且 downgrade 无法恢复。**

删除的是你的**真实设备使用痕迹**（应用集合、时段分布无法靠改字段消除）。
执行前必须先留档：

    .venv\\Scripts\\python.exe scripts\\export_real_device_data.py

demo 设备数据随后由 `scripts/seed_demo.py` 按人设重新合成——合出来的数据与
实验叙述自洽（基线夜间手机 60-90 分钟 → 干预 10-25 分钟）。

两步：
1. 15 张业务表归一：`user_id` 全部改成 demo 的 id。迁移时点上 personal 还没有
   任何数据，所以「归给 demo」等价于「全部归给 demo」；带 `<> demo` 条件只是幂等防御。
2. 删除 DEVICE 来源的 events 与 4 张设备表。
   `events.device_id` 没有外键，所以清 events 必须显式做，不能指望级联。

**预期副作用**：devices 被清空后，PC/安卓采集器立刻 401。这是设计内的，
需用新 token 重配（`scripts/create_device.py` 或 seed 打印的 token）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 除设备表外所有带 user_id 的业务表
BUSINESS_TABLES = (
    "app_category_rules",
    "daily_reviews",
    "day_notes",
    "event_types",
    "events",
    "experiment_logs",
    "experiment_status_events",
    "experiments",
    "findings",
    "memory_items",
    "period_reviews",
    "problems",
    "profile_facts",
    "state_records",
    "thoughts",
)

# 按依赖序删（子表在前）；events 先单独处理
DEVICE_TABLES = (
    "device_app_sessions",
    "device_usage_hourly",
    "device_usage_daily",
    "devices",
)


def upgrade() -> None:
    bind = op.get_bind()

    demo_id = bind.execute(
        sa.text("SELECT id FROM users WHERE username = 'demo'")
    ).scalar()
    if demo_id is None:
        raise RuntimeError("找不到 demo 账号：先跑迁移 f1a2b3c4d5e6")

    for table in BUSINESS_TABLES:
        result = bind.execute(
            sa.text(f"UPDATE {table} SET user_id = :demo WHERE user_id <> :demo"),
            {"demo": demo_id},
        )
        if result.rowcount:
            print(f"  {table}: {result.rowcount} 行改归 demo(id={demo_id})")

    # 设备痕迹：先 DEVICE 事件，再设备三表，最后设备本身
    deleted = bind.execute(sa.text("DELETE FROM events WHERE source = 'DEVICE'"))
    print(f"  删除 source=DEVICE 的 events: {deleted.rowcount} 行")

    for table in DEVICE_TABLES:
        result = bind.execute(sa.text(f"DELETE FROM {table}"))
        print(f"  删除 {table}: {result.rowcount} 行")


def downgrade() -> None:
    """不可逆：设备痕迹已删，无法重建。"""
