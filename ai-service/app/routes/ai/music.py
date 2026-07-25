"""AI 音频生成路由 — V2.0 纯本地方案（无 Suno）。

Nemotron 生成配器描述 → SoVITS 生成人声 → 返回音频 URL。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from app.database import get_db
from app.services.ai_scheduler import get_scheduler
from app.services.sovits_engine import get_sovits_engine

router = APIRouter(prefix="/ai", tags=["ai-music"])


@router.post("/music/generate")
async def generate_music(req: dict, request: Request):
    """歌曲音频生成 — Nemotron 配器 + SoVITS 人声，扣 5 Credits。"""
    user_id = req.get("user_id", 1)
    lyrics_id = req.get("lyrics_id")
    lyrics_text = req.get("lyrics", "")
    style = req.get("style", "pop")
    title = req.get("title", "")
    creation_id = req.get("creation_id")
    voice = req.get("voice", "default")

    if not lyrics_text:
        raise HTTPException(400, "Missing lyrics")

    scheduler = get_scheduler()
    sovits = get_sovits_engine()
    job_id = str(uuid.uuid4())[:8]

    db = await get_db()
    await db.execute(
        "INSERT INTO generation_jobs (job_id, user_id, task_type, status) VALUES (?, ?, ?, ?)",
        (job_id, user_id, "music", "processing"),
    )
    await db.commit()

    try:
        # Step 1: Nemotron 生成配器描述
        accompaniment = None
        try:
            accompaniment = await scheduler.generate_accompaniment(
                lyrics=lyrics_text, style=style, user_id=user_id,
            )
        except Exception:
            pass

        # Step 2: SoVITS 生成人声干声
        vocal_text = _extract_lyrics_only(lyrics_text) or lyrics_text
        audio_url = await sovits.generate_vocal(
            text=vocal_text[:1000],
            voice=voice,
            language=req.get("language", "zh"),
        )

        # Step 3: 存入 songs 表
        song_id = None
        if creation_id:
            cur = await db.execute(
                "INSERT INTO songs (creation_id, lyrics_id, user_id, version, title, audio_url, style_tags, model_name, generation_prompt) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (creation_id, lyrics_id or 0, user_id, title or "Untitled", audio_url, style, accompaniment.model_name if accompaniment else "sovits-local", (accompaniment.text or "")[:500] if accompaniment else ""),
            )
            song_id = cur.lastrowid

        await db.execute(
            "UPDATE generation_jobs SET status='completed', model_name=?, elapsed_ms=? WHERE job_id=?",
            (accompaniment.model_name if accompaniment else "sovits-local", 0, job_id),
        )
        await db.commit()

        return {
            "success": True,
            "data": {
                "job_id": job_id,
                "song_id": song_id,
                "audio_url": audio_url,
                "title": title or "Untitled",
                "model": accompaniment.model_name if accompaniment else "sovits-local",
                "music_production": accompaniment.text if accompaniment else None,
            },
        }

    except Exception as exc:
        await db.execute(
            "UPDATE generation_jobs SET status='failed', error_message=? WHERE job_id=?",
            (str(exc)[:500], job_id),
        )
        await db.commit()
        raise HTTPException(500, f"Music generation failed: {exc}")


@router.get("/music/{song_id}")
async def get_song(song_id: int):
    """获取单首歌曲记录。"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM songs WHERE id = ?", (song_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Song not found")
    return {"success": True, "data": dict(row)}


@router.get("/music/list/{creation_id}")
async def list_songs(creation_id: int):
    """获取某作品的所有歌曲版本。"""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM songs WHERE creation_id = ? ORDER BY version",
        (creation_id,),
    )
    rows = await cur.fetchall()
    return {"success": True, "data": [dict(r) for r in rows]}


def _extract_lyrics_only(text: str) -> str:
    import re
    text = re.sub(r"LRC:\s*[\s\S]+", "", text)
    text = re.sub(r"Title:.*", "", text)
    text = re.sub(r"(Verse|Chorus|Bridge|Intro|Outro)\s*\d*:", "", text)
    return text.strip()
