"""演示数据：按账号清空业务数据后，写入 2026-08-31 ~ 2026-09-09 的连贯数据。

用法（backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\seed_demo.py                    # 默认 demo 账号
    .venv\\Scripts\\python.exe scripts\\seed_demo.py --user personal    # 给别的账号灌
    .venv\\Scripts\\python.exe scripts\\seed_demo.py --keep-devices     # 保留设备，只重刷事件

数据设定（人设：后端工程师，想改掉睡前刷手机）：
- 基线 08-31 ~ 09-02：夜间手机 60-90 分钟，专注 2-3
- 暂停 09-03 ~ 09-04：周末旅行，实验暂停（用于验证暂停期排除聚合）
- 干预 09-05 ~ 09-09：手机 10-25 分钟，专注 3-4
- 1 个运行中实验（5 个指标：手记手机时长/设备实测手机时长/学习时长/专注/烦躁）
- 3 条用户背景、2 个长期问题，供全文流程测试
- 3 条 Agent 只读会话回放（`AGENT_REPLAYS`）：实验数据问答、写库审批、生成日报
- 2 台设备（工作电脑/手机）+ 精确会话 + 分小时 + 按日，覆盖设备采集全链路

**单一事实源**：夜间手机的时长只写在 `DAYS` 的 `PHONE_START` 事件里；
设备会话由它派生（见 `_night_sessions`），所以「手记」与「设备实测」天然同源。
后者刻意比前者短几分钟——现实中拿起/放下手机与点记录并不在同一瞬间。

**只按 user_id 清理，不用 TRUNCATE**：这是一键重置 demo 的底座，
绝不会碰到个人账号的数据（早期版本用全局 TRUNCATE，那是会误删的写法）。

注意：这是开发数据，review/insight/pattern 不在这里生成——
跑一遍复盘流程（POST /reviews 等）让 AI 提炼，才是真实的端到端验证。

**正因为不生成，AI 产物一旦被清就回不来**：`seed_demo.py --user demo` 会连
daily_reviews / period_reviews / memory_items 一起清空。公网 demo 的每日重置
走 `reset_demo.py`，它默认 keep 这三张表（见 AI_ARTIFACT_TABLES）。
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import engine
from app.models import (
    AgentThread,
    AppCategoryRule,
    Event,
    EventType,
    Experiment,
    ExperimentStatus,
    ExperimentStatusEvent,
    Problem,
    ProblemStatus,
    ProfileFact,
    StateRecord,
    Thought,
)
from app.schemas.device import UsageHourlyEntry, UsageSessionEntry
from app.services import (
    app_rule_service,
    auth_service,
    behavior_service,
    device_service,
    experiment_service,
)

TZ = timezone(timedelta(hours=8))  # 本机东八区
TZ_OFFSET_MIN = 480

# 需要按 user_id 清空的业务表（memory/review 等产物也清，让提炼流程真实重跑）
#
# `agent_threads`（M6）在这里，而**不在** AI_ARTIFACT_TABLES：demo 的 /agent/run
# 被封（403），它名下的会话行全是本脚本种的**只读回放快照**（见 AGENT_REPLAYS），
# 每次重置都要重种。放保留区反而会撞 thread_id 唯一约束，也会留下对不上的旧文案。
# 个人账号的真实会话按 user_id 隔离，清不到；langgraph 的 checkpoint 四表同样
# 只在真实会话里出现，demo 一条都不会有。
BUSINESS_TABLES = [
    "daily_reviews",
    "day_notes",
    "period_reviews",
    "problems",
    "findings",
    "profile_facts",
    "memory_items",
    "agent_threads",
    "experiment_logs",
    "experiment_status_events",
    "experiments",
    "events",
    "thoughts",
    "state_records",
    "app_category_rules",
    "event_types",
]

# 设备三表（子表在前）
DEVICE_TABLES = [
    "device_app_sessions",
    "device_usage_hourly",
    "devices",
]

# AI 产物表：内容由**真实 LLM** 跑出来，本脚本不产它们（见文件头注释）。
# 公网 demo 每日重置必须保留这三张表，否则展示内容被清空且不会恢复。
# 严密性依赖 app/deps.py 的 require_ai_enabled：那三张表的写入端点已对 demo 全封，
# 所以保留区里不会有访客写入的脏数据。
AI_ARTIFACT_TABLES = ["daily_reviews", "period_reviews", "memory_items"]

# (日, 事件列表) 事件: (本地小时, 分钟, 类型, 时长分钟, 备注)
DAYS: list[tuple[str, list[tuple[int, int, EventType, int | None, str | None]]]] = [
    ("2026-08-31", [
        (8, 30, EventType.LEARNING_START, 60, "看 FastAPI 文档"),
        (10, 0, EventType.LEARNING_START, 120, None),
        (12, 20, EventType.MEAL_START, 40, None),
        (14, 0, EventType.LEARNING_START, 180, "写项目代码"),
        (18, 10, EventType.MEAL_START, 45, None),
        (19, 30, EventType.GAME_START, 60, None),
        (21, 0, EventType.PHONE_START, 90, "刷短视频，停不下来"),
        (23, 30, EventType.SLEEP_START, 480, None),
    ]),
    ("2026-09-01", [
        (8, 40, EventType.LEARNING_START, 50, None),
        (10, 0, EventType.LEARNING_START, 120, "读 pgvector 文档"),
        (12, 30, EventType.MEAL_START, 35, None),
        (14, 0, EventType.LEARNING_START, 150, None),
        (18, 0, EventType.MEAL_START, 40, None),
        (19, 30, EventType.GAME_START, 60, "打了两局"),
        (21, 0, EventType.PHONE_START, 80, None),
        (23, 40, EventType.SLEEP_START, 460, None),
    ]),
    ("2026-09-02", [
        (8, 30, EventType.LEARNING_START, 70, None),
        (10, 0, EventType.LEARNING_START, 130, "调试向量检索"),
        (12, 30, EventType.MEAL_START, 40, None),
        (14, 0, EventType.LEARNING_START, 190, None),
        (18, 10, EventType.MEAL_START, 40, None),
        (21, 0, EventType.PHONE_START, 70, "今晚开始：22:30 后手机放客厅"),
        (23, 30, EventType.SLEEP_START, 480, None),
    ]),
    ("2026-09-03", [
        (8, 0, EventType.OUT_START, 720, "周末旅行"),
        (21, 0, EventType.PHONE_START, 120, "在酒店刷手机"),
        (23, 50, EventType.SLEEP_START, 500, None),
    ]),
    ("2026-09-04", [
        (9, 0, EventType.OUT_START, 600, "继续旅行"),
        (20, 0, EventType.MEAL_START, 60, "在外面吃饭"),
        (21, 30, EventType.PHONE_START, 120, None),
        (23, 30, EventType.SLEEP_START, 520, None),
    ]),
    ("2026-09-05", [
        (10, 0, EventType.LEARNING_START, 90, "旅行回来先整理笔记"),
        (12, 30, EventType.MEAL_START, 40, None),
        (15, 0, EventType.LEARNING_START, 120, None),
        (18, 0, EventType.MEAL_START, 40, None),
        (21, 0, EventType.PHONE_START, 20, "手机留在客厅"),
        (23, 20, EventType.SLEEP_START, 470, None),
    ]),
    ("2026-09-06", [
        (10, 0, EventType.LEARNING_START, 120, None),
        (12, 30, EventType.MEAL_START, 45, None),
        (15, 0, EventType.LEARNING_START, 120, "整理实验数据"),
        (18, 30, EventType.MEAL_START, 40, None),
        (19, 30, EventType.OUT_START, 60, "散步"),
        (21, 0, EventType.PHONE_START, 15, None),
        (23, 10, EventType.SLEEP_START, 470, None),
    ]),
    ("2026-09-07", [
        (8, 20, EventType.LEARNING_START, 70, "上午状态不错"),
        (10, 0, EventType.LEARNING_START, 120, None),
        (12, 30, EventType.MEAL_START, 40, None),
        (14, 0, EventType.LEARNING_START, 210, "写同步逻辑"),
        (18, 0, EventType.MEAL_START, 45, None),
        (21, 0, EventType.PHONE_START, 25, None),
        (23, 20, EventType.SLEEP_START, 460, None),
    ]),
    ("2026-09-08", [
        (8, 20, EventType.LEARNING_START, 80, None),
        (10, 0, EventType.LEARNING_START, 120, "专注度明显变好"),
        (12, 30, EventType.MEAL_START, 40, None),
        (14, 0, EventType.LEARNING_START, 180, None),
        (18, 0, EventType.MEAL_START, 40, None),
        (21, 0, EventType.PHONE_START, 10, "几乎没怎么碰手机"),
        (23, 15, EventType.SLEEP_START, 465, None),
    ]),
    ("2026-09-09", [
        (8, 20, EventType.LEARNING_START, 70, None),
        (10, 0, EventType.LEARNING_START, 130, None),
        (12, 30, EventType.MEAL_START, 40, None),
        (14, 0, EventType.LEARNING_START, 200, "复盘一周数据"),
        (18, 0, EventType.MEAL_START, 40, None),
        (21, 0, EventType.PHONE_START, 15, None),
        (23, 10, EventType.SLEEP_START, 470, None),
    ]),
]

# (日, 本地时, 分, 内容, 标签)
THOUGHTS: list[tuple[str, int, int, str, str]] = [
    ("2026-08-31", 22, 40, "说好十点半放下手机，结果还是刷到了十二点", "手机,自控"),
    ("2026-09-01", 21, 30, "刷手机时其实没有真的在放松，只是习惯性滑动", "手机,觉察"),
    ("2026-09-02", 23, 0, "第一晚把手机放客厅，入睡快了很多", "实验,睡眠"),
    ("2026-09-03", 22, 0, "旅行路上实验暂停，晚上还是忍不住刷了会儿", "实验,旅行"),
    ("2026-09-05", 22, 30, "回来继续实验，发现手机不进卧室就不太想看", "实验,手机"),
    ("2026-09-07", 12, 40, "这周上午的专注明显比上周好", "专注,实验"),
    ("2026-09-09", 21, 40, "好像已经不太依赖睡前刷手机了", "实验,睡眠"),
]

# (日, 本地时, 精力, 专注, 烦躁)
STATES: list[tuple[str, int, int, int, int]] = [
    ("2026-08-31", 10, 3, 2, 2), ("2026-08-31", 15, 2, 2, 3), ("2026-08-31", 21, 3, 1, 2),
    ("2026-09-01", 10, 3, 2, 3), ("2026-09-01", 15, 2, 2, 3), ("2026-09-01", 21, 3, 2, 2),
    ("2026-09-02", 10, 3, 3, 2), ("2026-09-02", 15, 3, 2, 2), ("2026-09-02", 21, 3, 2, 2),
    ("2026-09-03", 10, 2, 1, 3), ("2026-09-03", 15, 2, 1, 3), ("2026-09-03", 21, 3, 2, 2),
    ("2026-09-04", 10, 2, 1, 3), ("2026-09-04", 15, 2, 2, 2), ("2026-09-04", 21, 3, 2, 2),
    ("2026-09-05", 10, 4, 3, 2), ("2026-09-05", 15, 3, 3, 2), ("2026-09-05", 21, 3, 3, 1),
    ("2026-09-06", 10, 4, 3, 1), ("2026-09-06", 15, 4, 3, 1), ("2026-09-06", 21, 3, 3, 1),
    ("2026-09-07", 10, 4, 4, 1), ("2026-09-07", 15, 3, 4, 1), ("2026-09-07", 21, 4, 3, 1),
    ("2026-09-08", 10, 4, 4, 2), ("2026-09-08", 15, 3, 3, 2), ("2026-09-08", 21, 3, 3, 1),
    ("2026-09-09", 10, 4, 4, 1), ("2026-09-09", 15, 3, 3, 1), ("2026-09-09", 21, 3, 3, 1),
]

# (日, 内容)
DAY_NOTES: list[tuple[str, str]] = [
    ("2026-09-02", "准备开始睡前手机隔离：22:30 后手机放客厅充电。今晚是第一天。"),
    ("2026-09-05", "旅行回来，重新开始实验。白天有点累，但晚上没把手机带进卧室。"),
    ("2026-09-08", "上午效率很高，下午开完会之后有点烦躁，晚上没怎么刷手机。"),
]

PROFILE_FACTS: list[tuple[str, str]] = [
    ("identity", "一名后端工程师，正在往 AI 应用方向转"),
    ("habit", "习惯晚睡，理想入睡时间是 23:30 前"),
    ("context", "工作日白天需要长时间高强度编码，容易忽略休息"),
]

# ── Agent 只读回放（M6 第二步） ────────────────────────────
#
# demo 的 AI 全封（`/agent/run` 一律 403），所以它攒不出 checkpoint —— Agent 页
# 在公网作品集里原本只有一个灰掉的输入框，看不出这个功能长什么样。这里种的是
# **静态会话快照**：前端 `GET /agent/threads/{id}` 原样回放，读得到、点不动。
#
# 数字取自上面的 DAYS/STATES（基线夜间手机 70-90 分钟 → 干预期 10-25 分钟；
# 专注 2 分上下 → 3 分以上），文案是手写的，不随时间漂移；改演示数据时记得同步。


def _replay(user_text: str, answer: str, steps: list[dict]) -> dict:
    """拼一份会话快照。

    `messages` 必须**含最终那条 assistant 回答**：前端渲染的是 messages 而不是
    `answer`（真实图那边也一样 —— 回答本来就在 messages 里）。`answer` 只是把
    「最后一轮说了什么」显式带出来，省得回放也要倒着找一遍。

    回答按源码里的缩进写成多行，这里统一 dedent 掉 —— 前端会 trim 每一行，
    但存进库的 JSON 应该就是干净的正文。
    """
    text = dedent(answer).strip()
    return {
        "status": "answer",
        "answer": text,
        "messages": [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": text},
        ],
        "steps": steps,
        "pending": [],
    }


AGENT_REPLAYS: list[tuple[str, dict]] = [
    (
        "我上周的实验数据怎么样？",
        _replay(
            "我上周的实验数据怎么样？",
            """
            ## 睡前手机隔离 · 上周概览

            - **夜间手机**：基线三天（08-31~09-02）70-90 分钟；「手机不进卧室」之后（09-05 起）落在 10-25 分钟。
            - **学习时长**：从 6 小时上下升到 6.5-7 小时，09-07 之后更稳。
            - **专注**：基线 2 分上下，09-05 之后基本 3 分以上。
            - **烦躁**：从 2-3 降到 1-2。

            方向是对的。09-03、09-04 你在旅行、实验暂停，那两晚手机又回到 120 分钟 —— 说明变化不是「时间往后走」自己带来的。

            要不要我把「手机不进卧室」记成一条结论？会先弹确认卡。
            """,
            [
                {
                    "tool": "list_experiments",
                    "args": {},
                    "result": {
                        "experiments": [
                            {
                                "id": 1,
                                "name": "睡前手机隔离",
                                "status": "RUNNING",
                                "question": "睡前把手机留在客厅，能否减少夜间手机使用并提升次日专注？",
                                "metrics": [
                                    "phone_min",
                                    "night_phone_device",
                                    "study_min",
                                    "focus_avg",
                                    "irritation_avg",
                                ],
                            }
                        ]
                    },
                    "approved": None,
                },
                {
                    "tool": "get_experiment_stats",
                    "args": {"experiment_id": 1},
                    "result": {
                        "experiment": "睡前手机隔离",
                        "status": "RUNNING",
                        "experiment_id": 1,
                        "total_logs": 50,
                        "running_days": 14.3,
                        "paused_total_days": 2.0,
                        "metric_stats": [
                            {
                                "metric": "phone_min",
                                "unit": "分钟",
                                "count": 10,
                                "mean": 49.4,
                                "first_half_mean": 83.3,
                                "second_half_mean": 17.0,
                                "trend": "down",
                                "direction": "down_good",
                                "improved": True,
                            },
                            {
                                "metric": "focus_avg",
                                "unit": None,
                                "count": 10,
                                "mean": 2.7,
                                "first_half_mean": 2.0,
                                "second_half_mean": 3.3,
                                "trend": "up",
                                "direction": "up_good",
                                "improved": True,
                            },
                        ],
                    },
                    "approved": None,
                },
            ],
        ),
    ),
    (
        "把「手机不进卧室」记成一条结论",
        _replay(
            "把「手机不进卧室」记成一条结论",
            """
            已记入知识库（结论 · 挂到「为什么睡前难以放下手机？」）

            > 手机不进卧室，就不太会想看：实验第 5 天之后夜间手机从 70-90 分钟降到 10-25 分钟，且旅行暂停的两晚立刻反弹回 120 分钟。

            下一步建议：把「充电器放客厅」固定成睡前动作，这样不需要每天重新决定一次。
            """,
            [
                {
                    "tool": "create_finding",
                    "args": {
                        "title": "手机不进卧室，就不太会想看",
                        "observation": "实验第 5 天之后夜间手机从 70-90 分钟降到 10-25 分钟",
                        "kind": "CONCLUSION",
                        "problem_id": 1,
                        "interpretation": "物理隔离去掉了「顺手拿起来」这一步，比靠意志力抵抗更省力",
                        "next_step": "把「充电器放客厅」固定成睡前动作",
                    },
                    "result": {
                        "created": True,
                        "finding_id": 1,
                        "kind": "CONCLUSION",
                        "title": "手机不进卧室，就不太会想看",
                    },
                    "approved": True,
                }
            ],
        ),
    ),
    (
        "帮我复盘一下 9 月 9 日",
        _replay(
            "帮我复盘一下 9 月 9 日",
            """
            ## 2026-09-09 日报

            这天记了 8 段行为、1 条想法、3 次状态打分，平均专注 3.3、烦躁 1.0 —— 是这段时间里状态最好的一天。

            **亮点**：上午 8:20 就开始学习，下午连续 200 分钟没有中断。

            **注意**：晚上 21:00 还是有 15 分钟手机。你已经连续三天稳定在 10-25 分钟，说明「手机不进卧室」已经变成习惯了，但还没完全消失。

            日报已经生成并入库，同时把这一天的数据同步到了实验 1。
            """,
            [
                {
                    "tool": "get_review",
                    "args": {"period_type": "day", "date": "2026-09-09"},
                    "result": {
                        "period_type": "day",
                        "date": "2026-09-09",
                        "note": None,
                        "counts": {
                            "events": 13,
                            "thoughts": 1,
                            "states": 3,
                            "avg_energy": 3.3,
                            "avg_focus": 3.3,
                            "avg_irritation": 1.0,
                        },
                        "review_text": None,
                    },
                    "approved": None,
                },
                {
                    "tool": "generate_review",
                    "args": {"period_type": "day", "date": "2026-09-09"},
                    "result": {
                        "generated": True,
                        "period_type": "day",
                        "date": "2026-09-09",
                        "status": "ok",
                        "error": None,
                        "review_text": "状态最好的一天：上午 8:20 进入学习…",
                    },
                    "approved": True,
                },
            ],
        ),
    ),
]


# ── 设备数据合成 ──────────────────────────────────────────

PC_DEVICE = ("工作电脑", "pc")
PHONE_DEVICE = ("手机", "android")

# 内置关键词没覆盖到的包名，显式补规则（顺带演示「自定义规则」这个功能）
DEMO_RULES: tuple[tuple[str, str, str], ...] = (
    ("app", "com.ss.android.ugc.aweme", "video"),
    ("app", "tv.danmaku.bili", "video"),
    ("app", "com.tencent.mm", "social"),
)

# 夜间手机：长夜铺 4 个应用（刷视频为主），短夜只剩社交 + 论坛
NIGHT_APPS_HEAVY = (
    ("com.ss.android.ugc.aweme", "抖音", 45),
    ("tv.danmaku.bili", "哔哩哔哩", 30),
    ("com.tencent.mm", "微信", 15),
    ("com.zhihu.android", "知乎", 10),
)
NIGHT_APPS_LIGHT = (
    ("com.tencent.mm", "微信", 55),
    ("com.zhihu.android", "知乎", 45),
)
NIGHT_HEAVY_MIN = 45  # 睡前手机 ≥ 此分钟数算「长夜」

# 白天 PC：学习时段里写代码，穿插查文档（域名轮换，免得每天一模一样）
PC_APPS = (
    ("Code.exe", "Visual Studio Code", 50),
    ("WindowsTerminal.exe", "Windows Terminal", 20),
)
PC_DOCS = ("web:github.com", "web:fastapi.tiangolo.com", "web:docs.sqlalchemy.org")
PC_DOCS_WEIGHT = 30


@dataclass
class _Session:
    app: str
    label: str | None
    start: datetime  # 本地时区
    end: datetime


def local_tz_dt(day: str, hour: int, minute: int = 0) -> datetime:
    """本地时区的时刻（会话分桶要用它，否则小时/日期会落错）。"""
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, minute, tzinfo=TZ)


def local_dt(day: str, hour: int, minute: int = 0) -> datetime:
    """UTC 时刻（事件/状态等直接入库的字段用它）。"""
    return local_tz_dt(day, hour, minute).astimezone(timezone.utc)


def _to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _day_events(day: str):
    for d, events in DAYS:
        if d == day:
            return events
    return []


def _split(start: datetime, minutes: int, pool) -> list[_Session]:
    """按权重把一段窗口切成若干条会话；累计取整，保证首尾严丝合缝。"""
    total = sum(w for *_, w in pool)
    out: list[_Session] = []
    acc = 0.0
    cursor = start
    for app, label, weight in pool:
        acc += minutes * weight / total
        end = start + timedelta(minutes=round(acc))
        if end > cursor:
            out.append(_Session(app, label, cursor, end))
            cursor = end
    return out


def _night_sessions(day: str) -> list[_Session]:
    """夜间手机会话——时长完全来自 DAYS 里的 PHONE_START（单一事实源）。"""
    window = next(
        (
            (local_tz_dt(day, h, mi), dur)
            for h, mi, etype, dur, _ in _day_events(day)
            if etype is EventType.PHONE_START and dur
        ),
        None,
    )
    if window is None:
        return []
    start, minutes = window
    # 设备实测比手记短几分钟：拿起和放下手机晚于/早于点记录的那一刻
    head = min(4, max(1, minutes // 10))
    tail = max(1, head - 1)
    minutes -= head + tail
    if minutes <= 0:
        return []
    pool = NIGHT_APPS_HEAVY if minutes >= NIGHT_HEAVY_MIN else NIGHT_APPS_LIGHT
    return _split(start + timedelta(minutes=head), minutes, pool)


def _pc_sessions(day: str, day_index: int) -> list[_Session]:
    """白天 PC 会话——时长取自当天各段 LEARNING_START（学习即写代码）。"""
    out: list[_Session] = []
    docs = PC_DOCS[day_index % len(PC_DOCS)]
    pool = (*PC_APPS, (docs, docs.split(":", 1)[1], PC_DOCS_WEIGHT))
    for hour, minute, etype, dur, _note in _day_events(day):
        if etype is not EventType.LEARNING_START or not dur:
            continue
        out.extend(_split(local_tz_dt(day, hour, minute), dur, pool))
    return out


def _hourly_entries(sessions: list[_Session]) -> list[UsageHourlyEntry]:
    """会话 → 分小时桶（跨小时按重叠秒数拆开）。"""
    bucket: dict[tuple[str, int, str], tuple[str | None, int]] = {}
    for s in sessions:
        cursor = s.start
        while cursor < s.end:
            hour_start = cursor.replace(minute=0, second=0, microsecond=0)
            nxt = min(hour_start + timedelta(hours=1), s.end)
            key = (hour_start.strftime("%Y-%m-%d"), hour_start.hour, s.app)
            label, seconds = bucket.get(key, (s.label, 0))
            bucket[key] = (label, seconds + int((nxt - cursor).total_seconds()))
            cursor = nxt
    return [
        UsageHourlyEntry(app=app, label=label, hour=hour, seconds=seconds)
        for (_date, hour, app), (label, seconds) in sorted(bucket.items())
    ]


def wipe(
    db: Session, user_id: int, *, keep_devices: bool = False, keep: Sequence[str] = ()
) -> None:
    """按 user_id 清空该账号的业务数据（含 memory 等产物）。

    刻意不用 TRUNCATE：那会把个人账号的数据一起抹掉。
    `keep` 里的表跳过 —— 公网 demo 的每日重置靠它保住 AI 产物（见 AI_ARTIFACT_TABLES）。
    """
    tables = list(BUSINESS_TABLES) if keep_devices else [*DEVICE_TABLES, *BUSINESS_TABLES]
    skip = set(keep)
    for table in tables:
        if table in skip:
            continue
        db.execute(text(f"DELETE FROM {table} WHERE user_id = :uid"), {"uid": user_id})
    db.commit()


def seed_rules(db: Session, user_id: int) -> None:
    for scope, match_value, category in DEMO_RULES:
        db.add(
            AppCategoryRule(
                user_id=user_id,
                match_type="substring",
                scope=scope,
                match_value=app_rule_service.normalize_match_value(
                    "substring", scope, match_value
                ),
                category=category,
            )
        )
    db.commit()


def seed_content(db: Session, user_id: int) -> int:
    """手记内容（事件 / 想法 / 状态 / 日注 / 背景 / 问题 / 实验），返回实验 id。"""
    for day, events in DAYS:
        for hour, minute, etype, dur, note in events:
            ts = local_dt(day, hour, minute)
            db.add(
                Event(
                    user_id=user_id,
                    timestamp=ts,
                    type=etype.value,
                    note=note,
                    ended_at=ts + timedelta(minutes=dur) if dur else None,
                )
            )

    for day, hour, minute, content, tags in THOUGHTS:
        db.add(
            Thought(
                user_id=user_id,
                timestamp=local_dt(day, hour, minute),
                content=content,
                tags_raw=tags,
            )
        )

    for day, hour, energy, focus, irritation in STATES:
        db.add(
            StateRecord(
                user_id=user_id,
                timestamp=local_dt(day, hour),
                energy=energy,
                focus=focus,
                irritation=irritation,
            )
        )

    from app.models import DayNote

    for day, content in DAY_NOTES:
        db.add(DayNote(user_id=user_id, date=day, content=content))

    for category, content in PROFILE_FACTS:
        db.add(
            ProfileFact(
                user_id=user_id,
                category=category,
                content=content,
                source="manual",
                status="active",
                last_confirmed_at=local_dt("2026-08-30", 20),
            )
        )

    for title, category, note in [
        ("为什么睡前难以放下手机？", "精力", "睡前刷手机已成为自动化习惯，想弄清触发条件。"),
        ("为什么下午容易犯困？", "精力", "午后效率明显低于上午，怀疑与午餐和睡眠有关。"),
    ]:
        db.add(
            Problem(
                user_id=user_id,
                title=title,
                category=category,
                note=note,
                status=ProblemStatus.OPEN,
                source="manual",
            )
        )

    exp = Experiment(
        user_id=user_id,
        name="睡前手机隔离",
        question="睡前把手机留在客厅，能否减少夜间手机使用并提升次日专注？",
        hypothesis="睡前不把手机带进卧室，会减少手机使用时长，并让次日专注度上升",
        variable="睡前（22:30 后）手机是否留在客厅",
        indicator="手机时长 / 学习时长 / 专注 / 烦躁",
        metrics=[
            {"key": "phone_min", "name": "手机时长", "unit": "分钟", "direction": "down_good", "source": "event_duration:PHONE_START"},
            {"key": "night_phone_device", "name": "睡前手机（设备实测）", "unit": "分钟", "direction": "down_good", "source": "usage_platform:android@21-24"},
            {"key": "study_min", "name": "学习时长", "unit": "分钟", "direction": "up_good", "source": "event_duration:LEARNING_START"},
            {"key": "focus_avg", "name": "平均专注", "unit": None, "direction": "up_good", "source": "state_avg:focus"},
            {"key": "irritation_avg", "name": "平均烦躁", "unit": None, "direction": "down_good", "source": "state_avg:irritation"},
        ],
        expected_days=14,
        baseline_note="基线（08-31 ~ 09-02）：夜间手机 60-90 分钟，专注 2-3",
        status=ExperimentStatus.RUNNING.value,
        started_at=local_dt("2026-08-31", 8, 0),
    )
    db.add(exp)
    db.flush()

    transitions = [
        (None, "RUNNING", "开始实验：睡前手机放客厅", "2026-08-31", 8, 0),
        ("RUNNING", "PAUSED", "周末出门旅行，无法执行", "2026-09-03", 0, 0),
        ("PAUSED", "RUNNING", "旅行结束，恢复实验", "2026-09-05", 8, 0),
    ]
    for from_status, to_status, reason, day, hour, minute in transitions:
        db.add(
            ExperimentStatusEvent(
                user_id=user_id,
                experiment_id=exp.id,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                created_at=local_dt(day, hour, minute),
            )
        )

    db.commit()
    return exp.id


def seed_agent_replays(db: Session, user_id: int) -> None:
    """种 demo 的只读 Agent 会话回放（见 AGENT_REPLAYS）。

    `thread_id` 用确定性命名而不是 uuid：重置会先删再种，撞唯一约束反而是好事
    （说明 wipe 漏了这张表）。`updated_at` 递减，让侧栏顺序稳定。
    """
    now = datetime.now(timezone.utc)
    for index, (title, replay) in enumerate(AGENT_REPLAYS, start=1):
        stamp = now - timedelta(hours=index - 1)
        db.add(
            AgentThread(
                user_id=user_id,
                thread_id=f"replay-{user_id}-{index}",
                title=title,
                replay=replay,
                created_at=stamp - timedelta(minutes=5),
                updated_at=stamp,
            )
        )
    db.commit()


def seed_device_data(db: Session, user_id: int) -> list[tuple[str, str]]:
    """建 2 台设备并灌入会话/分小时/按日三类数据，再重建 DEVICE 事件与实验 AUTO 点。

    走的是采集器同一套 `device_service.ingest_*`，所以 demo 数据与真实上报
    在落库形状上完全一致。返回 [(设备名, 明文 token)]。
    """
    pc, pc_token = device_service.create_device(
        db, name=PC_DEVICE[0], platform=PC_DEVICE[1], user_id=user_id
    )
    phone, phone_token = device_service.create_device(
        db, name=PHONE_DEVICE[0], platform=PHONE_DEVICE[1], user_id=user_id
    )
    rules = app_rule_service.load_rules(db, user_id)

    for day_index, (day, _events) in enumerate(DAYS):
        for device, sessions in (
            (pc, _pc_sessions(day, day_index)),
            (phone, _night_sessions(day)),
        ):
            if not sessions:
                continue
            device_service.ingest_sessions(
                db,
                device,
                day,
                [
                    UsageSessionEntry(
                        app=s.app,
                        label=s.label,
                        start_ms=_to_ms(s.start),
                        end_ms=_to_ms(s.end),
                    )
                    for s in sessions
                ],
            )
            device_service.ingest_usage_hourly(db, device, day, _hourly_entries(sessions))

        # 先重建 DEVICE 事件（与手记判重），再让实验的 AUTO 点从设备数据出点
        behavior_service.rebuild_for_date(
            db, day, user_id=user_id, tz_offset=TZ_OFFSET_MIN, rules=rules
        )
        experiment_service.aggregate_day(
            db, day, user_id=user_id, tz_offset=TZ_OFFSET_MIN
        )

    return [(pc.name, pc_token), (phone.name, phone_token)]


def run(
    db: Session,
    user_id: int,
    *,
    keep_devices: bool = False,
    keep: Sequence[str] = (),
) -> None:
    """一键重置的共享实现：reset_demo.py 直接调它，避免两条代码路径漂移。"""
    wipe(db, user_id, keep_devices=keep_devices, keep=keep)
    if keep:
        print(f"保留 AI 产物表：{', '.join(keep)}")
    seed_rules(db, user_id)
    exp_id = seed_content(db, user_id)
    seed_agent_replays(db, user_id)
    print(f"seeded content: {len(DAYS)} days, experiment id={exp_id}")
    print(f"  Agent 只读回放：{len(AGENT_REPLAYS)} 条会话")

    if keep_devices:
        print("已按 --keep-devices 保留设备数据（DEVICE 事件已随 events 清掉，未重建）")
        return

    for name, token in seed_device_data(db, user_id):
        print(f"  设备「{name}」token: {token}")


def main() -> None:
    parser = argparse.ArgumentParser(description="给某账号重灌演示数据（只动该账号）")
    parser.add_argument("--user", default="demo", help="账号用户名（默认 demo）")
    parser.add_argument(
        "--keep-devices", action="store_true", help="保留设备与设备数据，只重刷事件/实验"
    )
    args = parser.parse_args()

    with Session(engine) as db:
        user = auth_service.get_by_username(db, args.user)
        if user is None:
            sys.exit(f"没有账号 {args.user!r}：先跑 scripts/create_user.py")
        print(f"目标账号：{user.username}（id={user.id}）")
        run(db, user.id, keep_devices=args.keep_devices)

    print("done. next: run the review flow via API.")


if __name__ == "__main__":
    main()
