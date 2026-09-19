"""预置/改密账号。

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\create_user.py personal
    .venv\\Scripts\\python.exe scripts\\create_user.py personal --password 'xxx' --display-name 我
    .venv\\Scripts\\python.exe scripts\\create_user.py personal --reset-password

不带 --password 时交互输入（不回显）。既不给也不交互时随机生成并打印一次。
不做自助注册：账号一律从这台机器上预置。
"""

import argparse
import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session

from app.db import engine
from app.services import auth_service


def _resolve_password(args) -> str:
    if args.password:
        return args.password
    if sys.stdin.isatty():
        pw = getpass.getpass("新密码（≥8 位，不回显）: ")
        if pw != getpass.getpass("再输一遍: "):
            sys.exit("两次输入不一致")
        return pw
    pw = secrets.token_urlsafe(12)
    print("（非交互环境，已随机生成密码）")
    return pw


def main() -> None:
    parser = argparse.ArgumentParser(description="创建账号或改密")
    parser.add_argument("username", help="登录用户名")
    parser.add_argument("--password", help="密码；省略则交互输入或随机生成")
    parser.add_argument("--display-name", help="界面上显示的名字")
    parser.add_argument("--demo", action="store_true", help="标记为演示账号（可一键重置）")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="账号已存在时只改密码（会撤销该账号全部登录令牌）",
    )
    args = parser.parse_args()

    password = _resolve_password(args)
    if len(password) < 8:
        sys.exit("密码至少 8 位")

    with Session(engine) as db:
        existing = auth_service.get_by_username(db, args.username)
        if existing is not None:
            if not args.reset_password:
                sys.exit(
                    f"账号 {existing.username!r}（id={existing.id}）已存在。"
                    f"要改密请加 --reset-password"
                )
            auth_service.set_password(db, existing, password)
            revoked = auth_service.revoke_all_tokens(db, existing.id)
            print(f"已改密：{existing.username}（id={existing.id}），撤销 {revoked} 个令牌")
            return

        user = auth_service.create_user(
            db,
            username=args.username,
            password=password,
            is_demo=args.demo,
            display_name=args.display_name,
        )
        print(f"已创建：{user.username}（id={user.id}，is_demo={user.is_demo}）")


if __name__ == "__main__":
    main()
