from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  # 确保所有 ORM 模型注册到 Base.metadata
from app.config import get_settings
from app.routers import (
    agent,
    analysis,
    app_rules,
    auth,
    day_notes,
    devices,
    event_types,
    events,
    experiments,
    findings,
    ingest,
    memory,
    problems,
    profile,
    reviews,
    states,
    summary,
    thoughts,
    timeline,
)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)

# 开发期跨域：
# - 本机前端（localhost:5173 / 127.0.0.1:5173）
# - Tailscale 私网访问（手机经 100.x.y.z:5173 或 *.ts.net:5173 打开前端）
#   Tailscale CGNAT 段为 100.64.0.0/10
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_origin_regex=(
        r"^http://(100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
        r"|[a-z0-9-]+\.ts\.net):\d+$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 表结构由 Alembic 迁移管理：
#   首次/升级：alembic upgrade head（backend 目录下）
#   开发期没有跑迁移时后端也能启动，但会缺表 → 记得先 upgrade

app.include_router(events.router)
app.include_router(event_types.router)
app.include_router(app_rules.router)
app.include_router(day_notes.router)
app.include_router(thoughts.router)
app.include_router(states.router)
app.include_router(timeline.router)
app.include_router(experiments.router)
app.include_router(summary.router)
app.include_router(reviews.router)
app.include_router(problems.router)
app.include_router(findings.router)
app.include_router(profile.router)
app.include_router(memory.router)
app.include_router(analysis.router)
app.include_router(devices.router)
app.include_router(ingest.router)
app.include_router(agent.router)
app.include_router(auth.router)


@app.get("/health", tags=["server"])
def health():
    return {"status": "ok", "app": settings.app_name}


# 前端构建产物同源托管：前端与 API 同源 → 手机只需一个地址，且完全不涉及 CORS。
# 挂在所有 include_router 之后：/health、/docs 等显式路由优先于 mount。
# is_dir() 守卫 → 没 build 过后端照样能起（开发期走 vite dev server）。
# 前端用 HashRouter，路由全在 #/ 之后 → 不需要 SPA 兜底重写。
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
