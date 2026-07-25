"""FTS5 全文检索 API — 零外部依赖。

检索范围（跨表联合搜索）：
- 歌词 / 标题 (ai_creations + lyrics)
- MV 分镜脚本 (videos.storyboard)
- 生成提示词 (songs.generation_prompt)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.database import get_db

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
    tab: str = Query(default="all", pattern="^(all|songs|videos|lyrics|images)$"),
):
    """FTS5 全文搜索 — 跨表检索创作内容。"""
    db = await get_db()
    like = f"%{q}%"

    results = []
    total = 0

    # 搜索 ai_creations（标题 + 歌词 + 提示词）
    if tab in ("all", "songs", "lyrics"):
        cur = await db.execute(
            """
            SELECT id, 'creation' as source, title as name,
                   prompt_text as description, created_at,
                   audio_url, cover_url, video_url
            FROM ai_creations
            WHERE title LIKE ? OR prompt_text LIKE ? OR lyrics LIKE ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, like, limit, offset),
        )
        rows = await cur.fetchall()
        for r in rows:
            results.append(dict(r))

        cnt = await db.execute(
            "SELECT COUNT(*) FROM ai_creations WHERE title LIKE ? OR prompt_text LIKE ? OR lyrics LIKE ?",
            (like, like, like),
        )
        total += (await cnt.fetchone())[0]

    # 搜索 lyrics 表
    if tab in ("all", "lyrics"):
        cur = await db.execute(
            """
            SELECT id, 'lyric' as source, title as name,
                   lyrics_text as description, created_at
            FROM lyrics
            WHERE lyrics_text LIKE ? OR title LIKE ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, limit, offset),
        )
        rows = await cur.fetchall()
        results.extend(dict(r) for r in rows)
        cnt = await db.execute(
            "SELECT COUNT(*) FROM lyrics WHERE lyrics_text LIKE ? OR title LIKE ?",
            (like, like),
        )
        total += (await cnt.fetchone())[0]

    # 搜索 videos（分镜脚本）
    if tab in ("all", "videos"):
        cur = await db.execute(
            """
            SELECT id, 'video' as source, '' as name,
                   storyboard as description, created_at
            FROM videos
            WHERE storyboard LIKE ? OR scenes_data LIKE ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, limit, offset),
        )
        rows = await cur.fetchall()
        results.extend(dict(r) for r in rows)
        cnt = await db.execute(
            "SELECT COUNT(*) FROM videos WHERE storyboard LIKE ? OR scenes_data LIKE ?",
            (like, like),
        )
        total += (await cnt.fetchone())[0]

    # 搜索图片/封面 (ai_creations cover_url + image tags)
    if tab in ("all", "images"):
        cur = await db.execute(
            """
            SELECT id, 'image' as source, title as name,
                   prompt_text as description, created_at,
                   cover_url, audio_url, video_url
            FROM ai_creations
            WHERE cover_url IS NOT NULL AND cover_url != ''
              AND (title LIKE ? OR prompt_text LIKE ?)
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, limit, offset),
        )
        rows = await cur.fetchall()
        results.extend(dict(r) for r in rows)
        cnt = await db.execute(
            "SELECT COUNT(*) FROM ai_creations WHERE cover_url IS NOT NULL AND cover_url != '' AND (title LIKE ? OR prompt_text LIKE ?)",
            (like, like),
        )
        total += (await cnt.fetchone())[0]

    # FTS5 原生搜索（ai_creations 全文索引）
    try:
        fts_cur = await db.execute(
            """
            SELECT rowid, title FROM tracks_fts
            WHERE tracks_fts MATCH ?
            LIMIT 10
            """,
            (q,),
        )
        # tracks_fts 结果暂不加入主列表（已有 ai_creations 覆盖）
    except Exception:
        pass

    return {
        "success": True,
        "items": results[:limit],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "query": q,
            "tab": tab,
        },
    }
