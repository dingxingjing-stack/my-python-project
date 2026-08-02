"""FastAPI application entry point — V2.0 纯本地方案。"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.config import get_settings
from app.database import close_db, init_db


# ---------------------------------------------------------------------------
# Uvicorn 日志过滤 — 屏蔽 /health 巡检 & HEAD 扫描噪声
# ---------------------------------------------------------------------------


class HealthAndScanFilter(logging.Filter):
    """屏蔽 /health GET 与外部 HEAD 扫描的访问日志，仅保留关键业务报错。"""

    _QUIET_PATHS = ("/health", "/favicon.ico")
    _QUIET_METHODS = ("HEAD",)

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage() if record.args else record.getMessage()
        # 屏蔽 /health 巡检
        for path in self._QUIET_PATHS:
            if path in msg:
                return False
        # 屏蔽 HEAD 扫描
        for method in self._QUIET_METHODS:
            if f'"{method} ' in msg or f'"{method}/' in msg:
                return False
        return True


def _configure_uvicorn_logging() -> None:
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.addFilter(HealthAndScanFilter())


_configure_uvicorn_logging()


# ---------------------------------------------------------------------------
# Lifespan event handler
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    # P0 启动自检 — 必填密钥缺失直接抛出异常阻止启动
    from app.startup_check import run_startup_checks
    run_startup_checks(exit_on_failure=True)

    await init_db()

    from app.services.recovery import run_startup_recovery, periodic_recovery_loop
    await run_startup_recovery()

    # 初始化内置模板
    from app.routes.templates import init_builtin_templates
    try:
        await init_builtin_templates()
    except Exception:
        pass

    recovery_task = asyncio.create_task(periodic_recovery_loop())

    yield

    recovery_task.cancel()
    try:
        await recovery_task
    except asyncio.CancelledError:
        pass
    await close_db()


# ---------------------------------------------------------------------------
# JWT 鉴权中间件
# ---------------------------------------------------------------------------


async def auth_middleware(request: Request, call_next):
    """JWT 鉴权 — 校验 Authorization header，注入 user_id。"""
    public_paths = {
        "/", "/health", "/create", "/explore", "/templates",
        "/auth/login", "/auth/register",
    }
    path = request.url.path

    # 公开路径跳过鉴权
    if path in public_paths or path.startswith("/static/") or path.startswith("/uploads/"):
        response = await call_next(request)
        return response

    # JWT 校验：仅对 /api/ 路径生效
    if path.startswith("/api/"):
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            from app.routes.auth import decode_token
            payload = decode_token(token)
            if payload:
                request.state.user_id = int(payload.get("sub", 1))
                response = await call_next(request)
                return response

        # 无有效 Token 时允许访问（默认 user_id=1 向后兼容）
        request.state.user_id = 1
        response = await call_next(request)
        return response

    request.state.user_id = 1
    response = await call_next(request)
    return response


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Avireon Music - AI Creation Platform",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载 static 静态资源目录（与 ai-service/ 同级）
    app.mount(
        "/static",
        StaticFiles(directory="static"),
        name="static",
    )

    # 注册中间件
    app.middleware("http")(auth_middleware)

    # i18n 中间件 — 注入语言环境
    from app.i18n import detect_locale
    @app.middleware("http")
    async def i18n_middleware(request: Request, call_next):
        request.state.locale = detect_locale(request)
        response = await call_next(request)
        return response

    _register_routers(app)

    return app


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def _register_routers(app: FastAPI) -> None:
    """Register all API route groups and page routes."""

    # ── Auth / 用户 ──
    from app.routes.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/v1")

    # ── 作品管理 ──
    from app.routes.collections import router as collections_router
    app.include_router(collections_router)

    # ── FTS5 全文检索 ──
    from app.routes.search import router as search_router
    app.include_router(search_router)

    # ── AI: 歌词生成 ──
    from app.routes.ai.lyrics import router as ai_lyrics_router
    app.include_router(ai_lyrics_router, prefix="/api/v1")

    # ── AI: 音乐生成（SoVITS 本地方案） ──
    from app.routes.ai.music import router as ai_music_router
    app.include_router(ai_music_router, prefix="/api/v1")

    # ── AI: MV 视频生成 ──
    from app.routes.ai.mv import router as ai_mv_router
    app.include_router(ai_mv_router, prefix="/api/v1")

    # ── AI: 封面图生成（硅基 SDXL） ──
    from app.routes.ai.cover import router as ai_cover_router
    app.include_router(ai_cover_router, prefix="/api/v1")

    # ── AI: 声音克隆 / SoVITS ──
    from app.routes.ai.voice import router as ai_voice_router
    app.include_router(ai_voice_router, prefix="/api/v1")

    # ── AI: 一键创作 ──
    from app.routes.ai.create import router as ai_create_router
    app.include_router(ai_create_router, prefix="/api/v1")

    # ── AI: Remix 二创 ──
    from app.routes.ai.remix import router as ai_remix_router
    app.include_router(ai_remix_router, prefix="/api/v1")

    # ── Template / Remix ──
    from app.routes.templates import router as templates_router
    app.include_router(templates_router)

    # ── Credits 系统 ──
    from app.routes.credits import router as credits_router
    app.include_router(credits_router)

    # ── Admin 后台 ──
    from app.routes.admin import router as admin_router
    app.include_router(admin_router)

    # ── Page routes ──
    from app.routes.pages import router as pages_router
    app.include_router(pages_router)

    # ── i18n 语言切换 API ──
    from app.i18n import router as i18n_router
    app.include_router(i18n_router)

    # ── 本地上传文件静态服务 ──
    import pathlib
    uploads_dir = pathlib.Path(__file__).resolve().parent.parent / "data" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/uploads",
        StaticFiles(directory=str(uploads_dir), html=False),
        name="uploads",
    )

    # ── Health check（GET + HEAD 兼容外部探活） ──
    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "build": "2026-07-31-v4"}

    @app.head("/health", include_in_schema=False)
    async def health_head():
        return JSONResponse(content=None, headers={"Content-Length": "15"})

    # ── 功能开关状态查询（前端可用） ──
    @app.get("/api/v1/features", include_in_schema=False)
    async def features_endpoint():
        from app.services.feature_flags import features_summary
        return features_summary()

    # ── 根路径 — 读取 static/home.html 渲染企业官网首页 ──
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index_page():
        with open("static/home.html", "r", encoding="utf-8") as f:
            return f.read()

    @app.head("/", include_in_schema=False)
    async def root_head():
        return JSONResponse(content=None)

    # ── 登录后深色创作控制台 ──
    @app.get("/console", response_class=HTMLResponse, include_in_schema=False)
    async def console_page():
        with open("static/console.html", "r", encoding="utf-8") as f:
            return f.read()

    # ── 登录/注册页面 ──
    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page():
        with open("static/login.html", "r", encoding="utf-8") as f:
            return f.read()

    @app.get("/register", response_class=HTMLResponse, include_in_schema=False)
    async def register_page():
        with open("static/register.html", "r", encoding="utf-8") as f:
            return f.read()


# Singleton
_app: FastAPI | None = None


def get_app() -> FastAPI:
    global _app
    if _app is None:
        _app = create_app()
    return _app


app = get_app()