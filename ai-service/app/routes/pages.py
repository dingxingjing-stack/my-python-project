"""HTML page routes (Jinja2 SSR).

页面：
- /          首页（极简创作入口）
- /create    四步创作向导
- /explore   作品广场
- /library   我的作品库
- /templates 模板库
- /creation/:id  作品详情页
- /settings  用户设置
- /s/:code   分享落地页
- /admin     管理后台
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from jinja2 import Environment

from app.i18n import i18n_context

router = APIRouter(tags=["pages"])

class I18nTemplates(Jinja2Templates):
    """自动注入 _() 翻译函数的 Jinja2Templates。"""
    def TemplateResponse(self, request, name, context=None, status_code=200):
        context = context or {}
        context.update(i18n_context(request))
        return super().TemplateResponse(request, name, context, status_code)

_templates = I18nTemplates(directory="app/templates")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request):
    """首页 — 极简创作入口。"""
    return _templates.TemplateResponse(request, "pages/home.html")


@router.get("/create", response_class=HTMLResponse, include_in_schema=False)
async def create(request: Request):
    """四步创作向导。"""
    return _templates.TemplateResponse(request, "pages/create.html")


@router.get("/explore", response_class=HTMLResponse, include_in_schema=False)
async def explore(request: Request):
    """作品广场。"""
    return _templates.TemplateResponse(request, "pages/explore.html")


@router.get("/library", response_class=HTMLResponse, include_in_schema=False)
async def library(request: Request):
    """我的作品库（含草稿）。"""
    return _templates.TemplateResponse(request, "pages/library.html")


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
async def settings(request: Request):
    """用户设置页。"""
    return _templates.TemplateResponse(request, "pages/settings.html")


@router.get("/creation/{creation_id:int}", response_class=HTMLResponse, include_in_schema=False)
async def creation_detail(request: Request, creation_id: int):
    """作品详情页。"""
    from app.database import get_db
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, job_id, prompt_text, title, style_tags, language,
               lyrics, lrc, audio_url, cover_url, video_url,
               duration_ms, model_name, plays_count, is_public, created_at
        FROM ai_creations WHERE id = ?
        """,
        (creation_id,),
    )
    row = await cur.fetchone()
    if not row:
        return _templates.TemplateResponse(request, "pages/home.html")
    creation = dict(row)
    return _templates.TemplateResponse(request, "pages/creation.html", {"creation": creation})


@router.get("/s/{code}", response_class=HTMLResponse, include_in_schema=False)
async def share_short_link(request: Request, code: str):
    """分享短链落地页 — 展示作品 + 播放器 + 一键 Remix。"""
    from app.database import get_db
    db = await get_db()
    cur = await db.execute(
        """SELECT c.id, c.user_id, c.prompt_text, c.title, c.style_tags, c.language,
                  c.lyrics, c.lrc, c.audio_url, c.cover_url, c.video_url,
                  c.duration_ms, c.plays_count, c.created_at,
                  u.display_name as creator_name
           FROM ai_creations c LEFT JOIN users u ON c.user_id = u.id
           WHERE c.share_code = ?""",
        (code,),
    )
    row = await cur.fetchone()
    if not row:
        return _templates.TemplateResponse(request, "pages/home.html")
    creation = dict(row)
    # 首次点击奖励创作者
    cur = await db.execute("SELECT click_count FROM shares WHERE share_code = ?", (code,))
    share_row = await cur.fetchone()
    was_zero = share_row and share_row["click_count"] == 0
    await db.execute("UPDATE shares SET click_count = click_count + 1 WHERE share_code = ?", (code,))
    await db.commit()
    if was_zero and creation.get("user_id"):
        try:
            from app.services.usage_tracker import add_bonus_generation
            await add_bonus_generation(creation["user_id"])
        except Exception:
            pass
    return _templates.TemplateResponse(request, "pages/share-event.html", {"creation": creation, "share_code": code, "was_zero": was_zero})


@router.get("/templates", response_class=HTMLResponse, include_in_schema=False)
async def templates_page(request: Request):
    """创作模板库页面。"""
    return _templates.TemplateResponse(request, "pages/templates.html")


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page(request: Request):
    """Admin 管理后台页面。"""
    return _templates.TemplateResponse(request, "pages/admin.html")
