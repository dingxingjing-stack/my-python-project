"""AI cover image generation routes — V1.0 对接 OpenRouter 图文模型。"""
from __future__ import annotations

import json
import uuid
from fastapi import APIRouter, HTTPException, Request

from app.database import get_db
from app.services.ai_scheduler import get_scheduler
from app.services.feature_flags import require_feature

router = APIRouter(prefix="/ai", tags=["ai-cover"])


@router.post("/cover/generate")
@require_feature("ai_cover")
async def generate_cover(request: Request):
    """封面图生成 — 调用 OpenRouter 视觉模型生成绘图提示词，扣 2 Credits。"""
    req = await request.json()
    user_id = req.get("user_id", 1)
    lyrics_id = req.get("lyrics_id")
    lyrics = req.get("lyrics", "")
    title = req.get("title", "Untitled")
    style = req.get("style", "Cinematic")
    aspect_ratio = req.get("aspect_ratio", "1:1")
    creation_id = req.get("creation_id")

    if not lyrics:
        raise HTTPException(400, "Missing lyrics")

    scheduler = get_scheduler()
    job_id = str(uuid.uuid4())[:8]

    db = await get_db()
    await db.execute(
        "INSERT INTO generation_jobs (job_id, user_id, task_type, status) VALUES (?, ?, ?, ?)",
        (job_id, user_id, "cover", "processing"),
    )
    await db.commit()

    try:
        # Step 1: 生成绘图提示词
        prompt_result = await scheduler.generate_cover_prompt(
            lyrics=lyrics, title=title, style=style, user_id=user_id,
        )
        image_prompt = prompt_result.text

        # Step 2: 存入 images 表（实际图片 URL 需要图像生成服务，当前只存提示词）
        cur = await db.execute(
            """
            INSERT INTO images (creation_id, user_id, image_url, image_type, aspect_ratio, style, prompt_text, model_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                creation_id, user_id, "",  # image_url 待图像模型生成
                "cover", aspect_ratio, style,
                image_prompt, prompt_result.model_name,
            ),
        )
        await db.execute(
            "UPDATE generation_jobs SET status='completed', model_name=?, input_prompt=?, ai_response=? WHERE job_id=?",
            (prompt_result.model_name, f"lyrics:{lyrics[:200]}", image_prompt[:5000], job_id),
        )
        await db.commit()

        return {
            "success": True,
            "data": {
                "image_id": cur.lastrowid,
                "job_id": job_id,
                "prompt": image_prompt,
                "model": prompt_result.model_name,
                "provider": prompt_result.provider,
                "elapsed_ms": prompt_result.elapsed_ms,
                "note": "Image prompt generated. Actual image generation requires integration with image service (DALL-E, Stable Diffusion, etc.)",
            },
        }

    except Exception as exc:
        await db.execute(
            "UPDATE generation_jobs SET status='failed', error_message=? WHERE job_id=?",
            (str(exc)[:500], job_id),
        )
        await db.commit()
        raise HTTPException(500, f"Cover generation failed: {exc}")


@router.get("/cover/{image_id}")
async def get_cover(image_id: int):
    """获取单个封面记录。"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM images WHERE id = ?", (image_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Image not found")
    return {"success": True, "data": dict(row)}


@router.get("/cover/list/{creation_id}")
async def list_covers(creation_id: int):
    """获取某作品的所有封面。"""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM images WHERE creation_id = ? ORDER BY created_at DESC",
        (creation_id,),
    )
    rows = await cur.fetchall()
    return {"success": True, "data": [dict(r) for r in rows]}
