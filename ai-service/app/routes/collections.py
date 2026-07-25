"""作品管理 API 路由 — V1.0。

提供：
- 作品 CRUD (ai_creations)
- 保存创作 (从向导)
- 公开作品广场 (explore)
- Like 系统
- 播放次数
- Library (含草稿)
"""
from __future__ import annotations

import json
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_db

router = APIRouter(prefix="/api/v1", tags=["collections"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class SaveCreationRequest(BaseModel):
    """从创作向导保存作品。"""
    prompt_text: str = Field(..., min_length=1)
    title: str = "Untitled"
    style_tags: Optional[str] = None
    language: str = "zh"
    lyrics: Optional[str] = None
    lrc: Optional[str] = None
    audio_url: Optional[str] = None
    cover_url: Optional[str] = None
    video_url: Optional[str] = None
    duration_ms: Optional[int] = None
    is_public: bool = True
    status: str = "completed"  # draft | completed


class UpdateCreationRequest(BaseModel):
    """更新作品。"""
    title: Optional[str] = None
    style_tags: Optional[str] = None
    is_public: Optional[bool] = None
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# 保存创作（从向导）
# ---------------------------------------------------------------------------


@router.post("/creations/save")
async def save_creation(req: SaveCreationRequest):
    """保存从创作向导完成的作品。"""
    db = await get_db()
    # 生成 share_code
    share_code = secrets.token_urlsafe(8)

    cur = await db.execute(
        """
        INSERT INTO ai_creations
            (job_id, user_id, prompt_text, title, style_tags, language,
             lyrics, lrc, audio_url, cover_url, video_url,
             duration_ms, model_name, is_public, status, share_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            None, 1, req.prompt_text, req.title, req.style_tags, req.language,
            req.lyrics, req.lrc, req.audio_url, req.cover_url, req.video_url,
            req.duration_ms, "suno-v5", 1 if req.is_public else 0,
            req.status, share_code,
        ),
    )
    await db.commit()
    creation_id = cur.lastrowid

    return {"success": True, "id": creation_id, "share_code": share_code}


# ---------------------------------------------------------------------------
# 作品 CRUD
# ---------------------------------------------------------------------------


@router.get("/works")
async def list_works(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
):
    """获取用户的所有作品（含草稿）。"""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, job_id, prompt_text, title, style_tags, language,
               lyrics, lrc, audio_url, cover_url, video_url,
               duration_ms, model_name, plays_count, likes_count,
               is_public, status, share_code, created_at
        FROM ai_creations
        WHERE user_id = 1
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = await cur.fetchall()
    items = [dict(r) for r in rows]

    cnt = await db.execute("SELECT COUNT(*) FROM ai_creations WHERE user_id = 1")
    total = (await cnt.fetchone())[0]

    return {
        "success": True,
        "items": items,
        "pagination": {"total": total, "limit": limit, "offset": offset},
    }


@router.get("/works/{work_id}")
async def get_work_detail(work_id: int):
    """获取单个作品详情。"""
    db = await get_db()
    cur = await db.execute(
        """
        SELECT id, job_id, prompt_text, title, style_tags, language,
               lyrics, lrc, audio_url, cover_url, video_url,
               duration_ms, model_name, plays_count, likes_count,
               is_public, status, share_code, created_at
        FROM ai_creations WHERE id = ?
        """,
        (work_id,),
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, detail="作品不存在")
    return {"success": True, "data": dict(row)}


@router.put("/works/{work_id}")
async def update_work(work_id: int, req: UpdateCreationRequest):
    """更新作品信息。"""
    db = await get_db()
    updates = []
    params = []
    if req.title is not None:
        updates.append("title = ?")
        params.append(req.title)
    if req.style_tags is not None:
        updates.append("style_tags = ?")
        params.append(req.style_tags)
    if req.is_public is not None:
        updates.append("is_public = ?")
        params.append(1 if req.is_public else 0)
    if req.status is not None:
        updates.append("status = ?")
        params.append(req.status)

    if not updates:
        return {"success": True}

    params.append(work_id)
    await db.execute(f"UPDATE ai_creations SET {', '.join(updates)} WHERE id = ?", params)
    await db.commit()
    return {"success": True}


@router.delete("/works/{work_id}")
async def delete_work(work_id: int):
    """删除一个作品。"""
    db = await get_db()
    await db.execute("DELETE FROM ai_creations WHERE id = ?", (work_id,))
    await db.commit()
    return {"success": True, "message": "已删除"}


# ---------------------------------------------------------------------------
# 公开作品广场
# ---------------------------------------------------------------------------


@router.get("/explore")
async def explore_works(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
    sort: str = Query(default="created_at", pattern="^(created_at|plays_count|likes_count)$"),
    q: str = Query(default=None, description="搜索关键词"),
    tab: str = Query(default="new", pattern="^(trending|new|videos)$"),
):
    """公开作品广场。"""
    db = await get_db()

    where_clauses = ["is_public = 1", "status = 'completed'"]
    params: list = []

    if tab == "videos":
        where_clauses.append("video_url IS NOT NULL AND video_url != ''")

    if q:
        where_clauses.append("(title LIKE ? OR prompt_text LIKE ? OR style_tags LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

    where = " AND ".join(where_clauses)

    cur = await db.execute(
        f"""
        SELECT id, prompt_text, title, style_tags, language,
               lyrics, audio_url, cover_url, video_url,
               duration_ms, plays_count, likes_count, created_at
        FROM ai_creations
        WHERE {where}
        ORDER BY {sort} DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    )
    rows = await cur.fetchall()
    items = [dict(r) for r in rows]

    cnt = await db.execute(f"SELECT COUNT(*) FROM ai_creations WHERE {where}", params)
    total = (await cnt.fetchone())[0]

    return {
        "success": True,
        "items": items,
        "pagination": {"total": total, "limit": limit, "offset": offset},
    }


# ---------------------------------------------------------------------------
# Library（含草稿）
# ---------------------------------------------------------------------------


@router.get("/library")
async def library_works(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
    tab: str = Query(default="all", pattern="^(all|songs|videos|drafts)$"),
):
    """用户作品库（含草稿分类）。"""
    db = await get_db()

    where_clauses = ["user_id = 1"]
    params: list = []

    if tab == "songs":
        where_clauses.append("audio_url IS NOT NULL AND audio_url != '' AND status = 'completed'")
    elif tab == "videos":
        where_clauses.append("video_url IS NOT NULL AND video_url != ''")
    elif tab == "drafts":
        where_clauses.append("status = 'draft'")

    where = " AND ".join(where_clauses)

    cur = await db.execute(
        f"""
        SELECT id, prompt_text, title, style_tags, language,
               audio_url, cover_url, video_url,
               duration_ms, plays_count, likes_count, status, created_at
        FROM ai_creations
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    )
    rows = await cur.fetchall()
    items = [dict(r) for r in rows]

    cnt = await db.execute(f"SELECT COUNT(*) FROM ai_creations WHERE {where}", params)
    total = (await cnt.fetchone())[0]

    return {
        "success": True,
        "items": items,
        "pagination": {"total": total, "limit": limit, "offset": offset},
    }


# ---------------------------------------------------------------------------
# Like 系统
# ---------------------------------------------------------------------------


@router.post("/works/{work_id}/like")
async def toggle_like(work_id: int):
    """Like / Unlike 切换。返回当前状态。"""
    db = await get_db()
    # 检查是否已 like
    cur = await db.execute(
        "SELECT 1 FROM likes WHERE user_id = 1 AND creation_id = ?",
        (work_id,),
    )
    existing = await cur.fetchone()

    if existing:
        # Unlike
        await db.execute("DELETE FROM likes WHERE user_id = 1 AND creation_id = ?", (work_id,))
        await db.execute("UPDATE ai_creations SET likes_count = MAX(0, likes_count - 1) WHERE id = ?", (work_id,))
        await db.commit()
        return {"liked": False}
    else:
        # Like
        await db.execute("INSERT INTO likes (user_id, creation_id) VALUES (1, ?)", (work_id,))
        await db.execute("UPDATE ai_creations SET likes_count = likes_count + 1 WHERE id = ?", (work_id,))
        await db.commit()
        return {"liked": True}


@router.get("/works/{work_id}/like")
async def check_like(work_id: int):
    """检查是否已 like。"""
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM likes WHERE user_id = 1 AND creation_id = ?",
        (work_id,),
    )
    row = await cur.fetchone()
    return {"liked": row is not None}


# ---------------------------------------------------------------------------
# 播放次数
# ---------------------------------------------------------------------------


@router.post("/works/{work_id}/play")
async def record_play(work_id: int):
    """记录一次播放。"""
    db = await get_db()
    await db.execute(
        "UPDATE ai_creations SET plays_count = plays_count + 1 WHERE id = ?",
        (work_id,),
    )
    await db.commit()
    return {"success": True}
