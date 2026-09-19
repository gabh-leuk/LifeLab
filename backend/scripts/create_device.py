"""给账号建一台采集设备并打印明文 token（只在创建时出现一次）。

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\create_device.py personal
    .venv\\Scripts\\python.exe scripts\\create_device.py personal --name 我的手机 --platform android

采集器把 token 写进自己的配置（PC：~/.lifelab/pc_collector.json；安卓：应用内设置），
之后上报 /ingest/* 时带 `X-Device-Token` 头即可，不需要用户登录态。

刻意不在迁移里给 personal 预置设备：token 会进代码或迁移文件，那就等于公开了。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session

from app.db import engine
from app.services import auth_service, device_service


def main() -> None:
    parser = argparse.ArgumentParser(description="为账号创建设备并打印 token")
    parser.add_argument("username", help="设备归属的账号用户名")
    parser.add_argument("--name", default="我的设备", help="设备展示名")
    parser.add_argument(
        "--platform", default="android", choices=("pc", "android"), help="设备平台"
    )
    args = parser.parse_args()

    with Session(engine) as db:
        user = auth_service.get_by_username(db, args.username)
        if user is None:
            sys.exit(f"没有账号 {args.username!r}：先跑 scripts/create_user.py")
        device, token = device_service.create_device(
            db, name=args.name, platform=args.platform, user_id=user.id
        )
        # 先把要打印的字段取出来：Session 关掉后 ORM 对象就 detached 了
        username, user_id = user.username, user.id
        device_id, device_name, platform = device.id, device.name, device.platform

    print(f"已创建设备：{device_name}（id={device_id}，{platform}）")
    print(f"归属账号：{username}（id={user_id}）")
    print()
    print(f"token（仅此一次，请立刻写入采集器配置）：{token}")


if __name__ == "__main__":
    main()
