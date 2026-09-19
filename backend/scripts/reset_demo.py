"""一键把 demo 账号恢复到初始演示状态（个人账号不受影响）。

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\reset_demo.py
    .venv\\Scripts\\python.exe scripts\\reset_demo.py --full
    .venv\\Scripts\\python.exe scripts\\reset_demo.py --keep-devices

这是 `seed_demo.run()` 的薄封装——「重置」与「首次 seed」共用同一条代码路径，
不会随着改动而漂移。它会清空 demo 名下的业务数据与设备，再重新灌一遍，
所以**先确认要丢的数据已经导出**（scripts/export_real_device_data.py）。

**默认保留 AI 产物**（daily_reviews / period_reviews / memory_items）：
那 10 篇日报、2 篇周报、1 篇月报、33 条记忆是用真实 LLM 跑出来的，seed_demo
不产它们，清掉就回不来——而它们正是 demo 的展示主体。`--full` 才连它们一起清。
保留是严密的：这三张表的写入端点已对 demo 全封（app/deps.py 的 require_ai_enabled），
所以保留区里不会有访客写入的脏数据。

只允许重置 `is_demo=True` 的账号：手滑传成个人账号会被拒绝。
要给别的账号灌数据，用 `seed_demo.py --user <名>`（那里是显式意图，会连 AI 产物一起清）。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import seed_demo  # 同目录，靠上面的 sys.path 解析
from sqlalchemy.orm import Session

from app.db import engine
from app.services import auth_service


def keep_tables(full: bool) -> tuple[str, ...]:
    """默认保留 AI 产物；`--full` 才连它们一起清。

    单独拎出来是为了能被测试直接钉住 —— 这是公网 demo 的展示保命线，
    不该只能靠「跑一遍看看」来验证。
    """
    return () if full else tuple(seed_demo.AI_ARTIFACT_TABLES)


def main() -> None:
    parser = argparse.ArgumentParser(description="重置 demo 账号到初始演示状态")
    parser.add_argument("--user", default="demo", help="要重置的演示账号（默认 demo）")
    parser.add_argument(
        "--keep-devices", action="store_true", help="保留设备，只重刷事件/实验"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="连 AI 产物（日报/周月报/记忆）一起清。默认保留 —— 那是 demo 的展示主体",
    )
    args = parser.parse_args()

    with Session(engine) as db:
        user = auth_service.get_by_username(db, args.user)
        if user is None:
            sys.exit(f"没有账号 {args.user!r}")
        if not user.is_demo:
            sys.exit(
                f"拒绝重置：{user.username!r} 不是演示账号（is_demo=False）。\n"
                f"若确实要给它灌演示数据，请用 seed_demo.py --user {user.username}"
            )

        keep = keep_tables(args.full)
        print(f"重置演示账号：{user.username}（id={user.id}）")
        seed_demo.run(db, user.id, keep_devices=args.keep_devices, keep=keep)

    print("done. 用 demo 账号重新登录即可看到初始状态。")


if __name__ == "__main__":
    main()
