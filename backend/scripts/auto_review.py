"""每天自动生成复盘 —— 昨天日复盘；周一追加上周周复盘；每月 1 号追加上月月复盘。

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\auto_review.py
    .venv\\Scripts\\python.exe scripts\\auto_review.py --user <名>   # 只跑一个账号
    .venv\\Scripts\\python.exe scripts\\auto_review.py --dry-run     # 只说要跑什么
    .venv\\Scripts\\python.exe scripts\\auto_review.py --date 2026-09-21 --dry-run

由 Windows 计划任务 "LifeLab Auto Review" 每天 04:07 调起（见 install_tasks.ps1）。
计划任务没有控制台，print 会丢 —— 所以同时写 ~/.lifelab/auto_review.log。

默认只跑 is_demo=False 的启用账号：demo 的 AI 端点被 require_ai_enabled 全封，
跑它只会白烧额度。要单独跑谁用 --user。

幂等：_has_daily / _has_period 与两个 service 自己的幂等判断逐字一致（见各自的注释），
所以重复跑不会重复调 LLM，日志里的「生成 N 篇」也是准的。

红线例外：生成日复盘会顺带写记忆索引（review_service 里的 replace_insights），
即「AI 自动写库」。这是用户明确授权的例外，三条边界见 docs/LEARNING_PATH.md：
只对个人账号、产物永远是 source=ai 且不改原始记录、用户可删（删除连带清记忆索引）。
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import engine
from app.models.period_review import PeriodReview
from app.models.review import DailyReview
from app.models.user import User
from app.services import auth_service, period_review_service, review_service

LOG_PATH = Path.home() / ".lifelab" / "auto_review.log"


def log(msg: str) -> None:
    """带时间戳输出：控制台 + 追加日志（后台跑时唯一可见处）。"""
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def local_tz_offset_minutes() -> int:
    """本机 UTC 偏移（分钟，东八区 = 480）。后端与用户同机，见 routers/ingest.py 的论证。"""
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return int(offset.total_seconds() // 60)


def _has_daily(db: Session, date: str, user_id: int) -> bool:
    """与 review_service.generate_review 的幂等判断逐字一致：status == "ok" 即跳过。

    注意别改成「structured 非空才算」：没记录的一天后端会存一行
    「这一天没有任何记录，无法复盘」（structured=None, status=ok），那是终态，
    再判成未完成就会天天重跑。生成失败的行是 status="error"，交给服务自己重试。
    """
    row = db.scalar(
        select(DailyReview).where(
            DailyReview.user_id == user_id, DailyReview.date == date
        )
    )
    return bool(row and row.status == "ok")


def _has_period(db: Session, period_type: str, date: str, user_id: int) -> bool:
    """与 period_review_service.generate_period_review 的幂等判断一致。

    这里比日复盘多要求 structured 非空 —— 周/月的「素材不足」占位是允许再次尝试的
    （等素材攒够再补），所以不能算完成。
    """
    key, _, _ = period_review_service.period_bounds(period_type, date)
    row = db.scalar(
        select(PeriodReview).where(
            PeriodReview.user_id == user_id,
            PeriodReview.period_type == period_type,
            PeriodReview.period_key == key,
        )
    )
    return bool(row and row.status == "ok" and row.structured is not None)


def run_for_user(
    db: Session, user_id: int, username: str, yesterday: str, today: str, *, dry: bool
) -> int:
    """给一个账号补上该有的复盘。返回实际生成的篇数。"""
    made = 0
    tz = local_tz_offset_minutes()

    if _has_daily(db, yesterday, user_id):
        log(f"  {username}: {yesterday} 日复盘已有，跳过")
    elif dry:
        log(f"  {username}: 会生成 {yesterday} 日复盘")
    else:
        row = review_service.generate_review(
            db, yesterday, tz_offset=tz, user_id=user_id
        )
        made += 1
        log(f"  {username}: {yesterday} 日复盘 → {row.status}")

    # 周一跑昨天（= 周日）正好落在上一周；其余日子不碰周复盘
    if datetime.fromisoformat(today).weekday() == 0:
        if _has_period(db, "week", yesterday, user_id):
            log(f"  {username}: 上一周 周复盘已有，跳过")
        elif dry:
            log(f"  {username}: 会生成上一周 周复盘")
        else:
            row = period_review_service.generate_period_review(
                db, "week", yesterday, tz_offset=tz, user_id=user_id
            )
            made += 1
            log(f"  {username}: 上一周 周复盘 → {row.status}")

    # 1 号跑昨天（= 上月末）正好落在上一月
    if today.endswith("-01"):
        if _has_period(db, "month", yesterday, user_id):
            log(f"  {username}: 上一月 月复盘已有，跳过")
        elif dry:
            log(f"  {username}: 会生成上一月 月复盘")
        else:
            row = period_review_service.generate_period_review(
                db, "month", yesterday, tz_offset=tz, user_id=user_id
            )
            made += 1
            log(f"  {username}: 上一月 月复盘 → {row.status}")

    return made


def resolve_targets(only_user: str | None) -> list[tuple[int, str]]:
    """要跑的账号 (id, username)。默认所有非 demo 的启用账号。"""
    with Session(engine) as db:
        if only_user:
            user = auth_service.get_by_username(db, only_user)
            if user is None:
                log(f"没有账号 {only_user!r}")
                return []
            return [(user.id, user.username)]
        return [
            (u.id, u.username)
            for u in db.scalars(
                select(User)
                .where(User.is_demo.is_(False), User.is_active.is_(True))
                .order_by(User.id)
            )
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description="自动生成昨天/上周/上月的复盘")
    parser.add_argument("--user", help="只跑这个账号（默认所有非 demo 的启用账号）")
    parser.add_argument(
        "--date",
        help="按这一天的视角跑（YYYY-MM-DD，默认今天）。周一/1 号的周月分支靠它验证",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印会做什么，不调 LLM")
    args = parser.parse_args()

    now = datetime.now().astimezone()
    if args.date:
        try:
            base = datetime.fromisoformat(args.date).date()
        except ValueError:
            log(f"--date 不是合法日期：{args.date!r}（要 YYYY-MM-DD）")
            return
    else:
        base = now.date()
    today = base.isoformat()
    yesterday = (base - timedelta(days=1)).isoformat()
    log(f"自动复盘开始：今天 {today}（周{'一二三四五六日'[base.weekday()]}），目标日 {yesterday}")

    total = 0
    for user_id, username in resolve_targets(args.user):
        try:
            with Session(engine) as db:
                total += run_for_user(
                    db, user_id, username, yesterday, today, dry=args.dry_run
                )
        except Exception as exc:  # noqa: BLE001 —— 一个账号失败不该拖垮其余
            log(f"  {username}: 失败 —— {type(exc).__name__}: {exc}")

    log(f"自动复盘结束：生成 {total} 篇")


if __name__ == "__main__":
    main()
