# LifeLab

[![CI](https://github.com/nmslcs/LifeLab/actions/workflows/ci.yml/badge.svg)](https://github.com/nmslcs/LifeLab/actions/workflows/ci.yml)

**A self-hosted platform that turns daily life into data — and uses an AI agent to make sense of it.**

LifeLab is an end-to-end, single-user "quantified self" system: low-friction daily logging, automatic
device-usage collection (computer + phone), N-of-1 experiments whose metrics are aggregated from those
raw traces, and an LLM agent that writes daily/weekly/monthly reviews and answers questions grounded in
your own history. Everything runs on one machine, one port, no third-party SaaS.

- **Stack:** FastAPI · SQLAlchemy 2 · PostgreSQL + pgvector · LangGraph · React 19 + TypeScript + Vite · Kotlin (Android)
- **AI:** an OpenAI-compatible LLM behind a LangGraph state machine, with **human-in-the-loop approval before any write**
- **Automation:** a daily scheduled job generates reviews (day / week / month) unattended

> 中文说明见下方 [LifeLab（中文）](#lifelab中文)。

---

## Why it exists

Most habit trackers ask you to grade yourself. LifeLab instead observes what actually happened —
which apps ran for how long, when you were at the computer, what you wrote down — and treats *those*
as the ground truth for experiments like "did going to bed earlier actually improve my focus?".
The AI's job is not to nag; it is to read your own traces back to you, and to be explicit about what
it is inferring versus what it knows.

## Features

| Area | What it does |
|---|---|
| **Logging** | Three record kinds — `Event`, `Thought`, `State` — with low-friction entries ("log again", recent list, instant feedback) |
| **Timeline** | Aggregated day view that merges manual records with passively collected device activity |
| **Experiments** | N-of-1 studies with custom metrics. You write only the metric *name*; the AI infers which data source feeds it and aggregate the daily points |
| **Reviews** | AI-generated day / week / month reviews. Manual records and device data are given equal weight and are cross-checked against each other |
| **Memory** | A RAG index (pgvector) over your history, so the assistant can recall things months back |
| **AI Assistant** | A stateful LangGraph agent with tool-calling. Sessions survive restarts; every DB write is gated behind an approval step |
| **Device collection** | A PC collector (hourly, with browser-site inference) and an Android app (hourly app usage) upload usage traces |
| **Insights** | Findings / problems / summaries extracted from reviews, kept strictly separate from raw records |

## Architecture

```
        React + TypeScript (web)                 Kotlin (Android)
                  │                                      │
                  │  REST                                │  REST (usage stats + widget)
                  ▼                                      ▼
            ┌──────────────────────────────────────────────────┐
            │                  FastAPI backend                 │
            │   routers → services → SQLAlchemy models         │
            │   LangGraph agent (state machine + checkpoint)   │
            └───────┬───────────────────────┬──────────────────┘
                    │                       │
             PostgreSQL + pgvector      LLM / embedding
             (records, vectors,         (OpenAI-compatible;
              agent checkpoints)         embeddings via local Ollama)
                    ▲
        daily scheduled tasks: backup · demo reset · auto review (day/week/month)
```

The web frontend is built by Vite and served **same-origin** by FastAPI in production, so the whole app
is a single port.

## Tech stack

- **Backend:** Python 3.12+ (tested on 3.14), FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, psycopg 3
- **Database:** PostgreSQL 16 + pgvector (vector search for memory). SQLite works too, for a zero-install dev run — agent sessions then live in process memory (they survive page reloads, not restarts)
- **AI:** LangGraph (state graph + Postgres checkpointer), an OpenAI-compatible chat model, local embeddings via Ollama
- **Frontend:** React 19, TypeScript, Vite 8, React Router, Phosphor Icons — no UI framework, hand-written CSS
- **Android:** Kotlin, UsageStats + hourly event reconstruction, a home-screen widget for one-tap logging, an embedded WebView for the data views
- **Ops:** Windows Task Scheduler (service, daily backup, demo reset, auto review), pytest + ruff, GitHub Actions CI

## Getting started

### Prerequisites

- **Python 3.12+**, **Node 20+**
- *Optional:* **Docker Desktop** (for PostgreSQL), **JDK 17 + Android SDK** (for the Android collector)

### 1. Database

By default the backend uses **SQLite** — no setup at all. To use PostgreSQL instead:

```bash
cp .env.example .env          # 填一个强口令到 LIFELAB_DB_PASSWORD
docker compose up -d          # 起 postgres 16 + pgvector
```

### 2. Backend

```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.lock.txt      # 复现实测通过的精确版本组合

cp .env.example .env                      # 填 LLM_API_KEY 等（见文件内注释）
alembic upgrade head                      # 建表（含 agent checkpoint 表）
uvicorn app.main:app --reload             # http://127.0.0.1:8000
```

To use PostgreSQL, set `DATABASE_URL` in `backend/.env` to
`postgresql+psycopg://lifelab:<口令>@localhost:5432/lifelab` (the password must match the root `.env`).

API docs are **off by default**; set `ENABLE_DOCS=true` in `backend/.env` to get `/docs` locally.

### 3. Frontend

```bash
cd frontend
npm ci            # 按 package-lock.json 精确安装
npm run dev       # http://127.0.0.1:5173
```

### 4. Android collector (optional)

Requires JDK 17 and an Android SDK:

```bash
cd android
gradle :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Open the app, enter the server address and a device token (create one via `POST /devices`), then long-press
the home screen to add the **LifeLab 记录** widget for one-tap logging.

### 5. Tests

```bash
cd backend
.venv\Scripts\python -m pytest          # 473 tests, SQLite, no services needed
```

Two things the unit tests don't cover, both run against the demo data with a **real LLM**:

```bash
.venv\Scripts\python scripts\rag_eval.py      # RAG retrieval quality (fixed queries + thresholds)
.venv\Scripts\python scripts\agent_eval.py    # agent tool choice + the write-approval gate
```

`agent_eval.py` asserts that a write request **stops at the approval card** and that no write tool
ever executes unattended — the safety property, as a regression test.

Every LLM call is traced to `~/.lifelab/llm_calls.jsonl` (caller / model / latency / tokens / ok);
summarize it with:

```bash
.venv\Scripts\python scripts\llm_usage.py     # p50/p95 latency, token totals, per-caller split
```

## Production run

Build the frontend once, then serve everything from FastAPI on a single port:

```bash
cd frontend && npm run build        # → frontend/dist, served same-origin
cd ../backend/scripts && serve.cmd  # uvicorn without --reload; logs under %USERPROFILE%\.lifelab\
```

On Windows, `install_tasks.ps1` registers four scheduled tasks: the service itself, a daily database
backup, a daily demo reset, and the daily auto-review job (`install_tasks.ps1 -Uninstall` removes all four).

## Project layout

```
backend/     FastAPI app — routers, services, models, LangGraph agent, Alembic migrations, scripts, tests
frontend/    React + TypeScript + Vite single-page app
android/     Kotlin usage collector + home-screen widget + embedded WebView
```

## Live demo

> `https://lifelab.tail8a5451.ts.net/` — demo account `demo` / `demo1234` (read-only, AI disabled).

---

# LifeLab（中文）

**把日常生活变成数据，再用一个 AI Agent 把它读回给你的自托管平台。**

LifeLab 是一套端到端的单用户「自我量化」系统：低摩擦的每日记录、电脑与手机的**使用时长自动采集**、
指标自动从原始轨迹里聚合出来的 N-of-1 实验，以及一个会写日/周/月复盘、并能就你自己的历史提问作答的
LLM Agent。全部跑在一台机器、一个端口上，不依赖任何第三方 SaaS。

## 它想解决什么

大多数习惯类工具让你给自己打分。LifeLab 反过来——它观察**实际发生了什么**（哪些应用开了多久、
什么时候坐在电脑前、你写下了什么），并把这些当作实验的事实依据，比如「早睡到底有没有让第二天更专注」。
AI 的职责不是督促，而是把你自己的痕迹读回给你，并且**明确区分「它知道的」和「它推测的」**。

## 功能

| 模块 | 说明 |
|---|---|
| **记录** | 三类记录 —— `Event` / `Thought` / `State`，配「再记一次 / 最近记录 / 即时回报」等低摩擦入口 |
| **时间轴** | 按天聚合视图，把人工记录与自动采集的设备活动合并呈现 |
| **实验** | 自定义指标的 N-of-1 实验。你**只写指标名**，AI 识别它该从哪个数据源取数，并逐日聚合成点 |
| **复盘** | AI 生成的日 / 周 / 月复盘。人工记录与设备数据**同地位**，且强制交叉核对 |
| **记忆** | 基于 pgvector 的 RAG 索引，让助手能回忆起几个月前的事 |
| **AI 助手** | 有状态的 LangGraph Agent（工具调用）。会话活过重启；**每一次写库都停在审批卡前等你确认** |
| **设备采集** | PC 采集器（分小时 + 浏览器站点推断）与安卓 App（分小时应用时长）上传使用轨迹 |
| **洞察** | 从复盘中抽取的结论 / 问题 / 小结，与原始记录**严格分离** |

## 技术栈

- **后端**：Python 3.12+（实测 3.14）、FastAPI、SQLAlchemy 2.x、Alembic、Pydantic v2、psycopg 3
- **数据库**：PostgreSQL 16 + pgvector（记忆的向量检索）。也支持 SQLite，用于零安装的开发起步 —— 此时 Agent 会话存在进程内存里（刷新页面还在，重启进程就没了）
- **AI**：LangGraph（状态图 + Postgres 检查点）、OpenAI 兼容的对话模型、本地 Ollama 提供向量化
- **前端**：React 19、TypeScript、Vite 8、React Router、Phosphor Icons —— 不用 UI 框架，样式手写
- **安卓**：Kotlin，UsageStats + 按小时事件重建，桌面小组件一键记录，内置 WebView 看数据
- **运维**：Windows 计划任务（服务 / 每日备份 / demo 重置 / 自动复盘）、pytest + ruff、GitHub Actions CI

## 快速开始

### 前置

- **Python 3.12+**、**Node 20+**
- 可选：**Docker Desktop**（用 PostgreSQL 时）、**JDK 17 + Android SDK**（编译安卓采集器时）

### 1. 数据库

后端默认走 **SQLite**，无需任何配置。要用 PostgreSQL：

```bash
cp .env.example .env          # 往 LIFELAB_DB_PASSWORD 填一个强口令
docker compose up -d          # 起 postgres 16 + pgvector
```

### 2. 后端

```bash
cd backend
python -m venv .venv
# Windows：  .venv\Scripts\activate
# macOS/Linux：  source .venv/bin/activate
pip install -r requirements.lock.txt      # 复现实测通过的精确版本组合

cp .env.example .env                      # 填 LLM_API_KEY 等（见文件内注释）
alembic upgrade head                      # 建表（含 agent checkpoint 表）
uvicorn app.main:app --reload             # http://127.0.0.1:8000
```

用 PostgreSQL 时，把 `backend/.env` 的 `DATABASE_URL` 设为
`postgresql+psycopg://lifelab:<口令>@localhost:5432/lifelab`（口令须与根目录 `.env` 一致）。

接口文档**默认关闭**；本地要看就把 `backend/.env` 里的 `ENABLE_DOCS` 设为 `true`，再访问 `/docs`。

### 3. 前端

```bash
cd frontend
npm ci            # 按 package-lock.json 精确安装
npm run dev       # http://127.0.0.1:5173
```

### 4. 安卓采集器（可选）

需要 JDK 17 与 Android SDK：

```bash
cd android
gradle :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

打开 App 填服务器地址与设备 token（用 `POST /devices` 创建），再长按桌面添加 **LifeLab 记录** 小组件，
即可一键记录。

### 5. 测试

```bash
cd backend
.venv\Scripts\python -m pytest          # 473 个用例，跑在 SQLite 上，不需要任何服务
```

单元测试覆盖不到的两件事，都要**真 LLM** + 演示数据：

```bash
.venv\Scripts\python scripts\rag_eval.py      # RAG 检索质量（固定查询 + 阈值断言）
.venv\Scripts\python scripts\agent_eval.py    # Agent 的工具选择 + 写库审批闸
```

`agent_eval.py` 断言「写库请求必须停在审批卡、且任何写工具都不得无人执行」—— 把安全性质
变成可回归的测试。它刻意**不脚本化 LLM**：要测的正是模型自己挑不挑得对工具。

每次 LLM 调用都会埋点一行到 `~/.lifelab/llm_calls.jsonl`（来源 / 模型 / 延迟 / token / 成败），
汇总看：

```bash
.venv\Scripts\python scripts\llm_usage.py     # p50/p95 延迟、token 总量、按来源拆分
```

## 生产运行

前端 `npm run build` 后由 FastAPI **同源**托管 `frontend/dist`，整站只需一个端口：

```bash
cd frontend && npm run build        # → frontend/dist，同源托管
cd ../backend/scripts && serve.cmd  # uvicorn 无 --reload；日志在 %USERPROFILE%\.lifelab\
```

Windows 上用 `install_tasks.ps1` 装四个计划任务：服务本体、每日库备份、demo 每日重置、每日自动复盘
（`install_tasks.ps1 -Uninstall` 一次卸掉四个）。

## 目录结构

```
backend/     FastAPI 应用 —— 路由、服务、模型、LangGraph Agent、Alembic 迁移、脚本、测试
frontend/    React + TypeScript + Vite 单页应用
android/     Kotlin 使用时长采集器 + 桌面小组件 + 内置 WebView
```

## 在线演示

> `https://lifelab.tail8a5451.ts.net/` —— 演示账号 `demo` / `demo1234`（全站只读，AI 全程禁用）。
