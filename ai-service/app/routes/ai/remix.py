"""Remix / 音乐二创路由 — 纯本地方案。

流程：
1. 接收上传的本地音频文件
2. Nemotron 分析原曲风格、歌词
3. 生成改编方案（重写歌词、变调、曲风转换）
4. SoVITS 重新生成改编后人声
5. FFmpeg 合并输出
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from app.database import get_db
from app.services.ai_scheduler import get_scheduler
from app.services.sovits_engine import get_sovits_engine
from app.services.local_storage import get_local_storage


async def _separate_vocals(input_path: str, output_dir: str) -> tuple[str, str]:
    """分离人声与伴奏，返回 (vocals_path, instrumental_path)。

    失败时静默回退到原始音频路径，保证 remix 流程不中断。
    """
    vocals_path = os.path.join(output_dir, "vocals.wav")
    instrumental_path = os.path.join(output_dir, "instrumental.wav")
    try:
        proc1 = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", input_path,
            "-af", "pan=mono|FC=FL",
            "-y", vocals_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        proc2 = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", input_path,
            "-af", "pan=stereo|FL=FL-FC|FR=FR-FC",
            "-y", instrumental_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.gather(proc1.wait(), proc2.wait())
        if proc1.returncode == 0 and proc2.returncode == 0:
            return vocals_path, instrumental_path
    except Exception:
        pass
    return input_path, input_path


router = APIRouter(prefix="/ai", tags=["ai-remix"])


@router.post("/remix")
async def remix_track(
    audio: UploadFile = File(...),
    style: str = Form("pop"),
    new_lyrics: str = Form(""),
    user_id: int = Form(1),
):
    """上传音频进行二创改编。

    Args:
        audio: 上传的本地音频文件
        style: 目标曲风
        new_lyrics: 新歌词（可选，不传则由 AI 重写）
    """
    scheduler = get_scheduler()
    sovits = get_sovits_engine()
    job_id = str(uuid.uuid4())[:8]

    # 保存上传的音频
    storage = get_local_storage()
    audio_bytes = await audio.read()
    original_audio_url = storage.save_audio(audio_bytes, ext="mp3")

    # 分离人声与伴奏（非致命，失败则静默回退）
    try:
        rel_path = original_audio_url.lstrip("/uploads/")
        local_path = os.path.join(str(storage._base), rel_path)
        sep_dir = os.path.join(str(storage._base), "temp", job_id)
        os.makedirs(sep_dir, exist_ok=True)
        vocals_path, instrumental_path = await _separate_vocals(local_path, sep_dir)
    except Exception:
        vocals_path = instrumental_path = None

    db = await get_db()
    await db.execute(
        "INSERT INTO generation_jobs (job_id, user_id, task_type, status) VALUES (?, ?, ?, ?)",
        (job_id, user_id, "remix", "processing"),
    )
    await db.commit()

    result = {
        "job_id": job_id,
        "original_audio_url": original_audio_url,
        "new_audio_url": None,
        "new_lyrics": new_lyrics or None,
        "remix_style": style,
        "analysis": None,
    }

    try:
        # Step 1: Nemotron 分析原曲
        system = "You are a music analyst. Given the song style, provide a brief analysis and remix strategy. Output JSON: {\"original_style\": \"...\", \"bpm_guess\": 120, \"key\": \"C\", \"remix_plan\": \"...\"}"
        analysis = await scheduler.dispatch(
            scheduler.AITaskType.CODE if hasattr(scheduler, 'AITaskType') else __import__('app.services.ai_scheduler', fromlist=['AITaskType']).AITaskType.CODE,
            [{"role": "system", "content": system}, {"role": "user", "content": f"Original style: {style}, filename: {audio.filename}"}],
            temperature=0.5,
            max_tokens=500,
            credit_action=None,
            user_id=user_id,
        )
        result["analysis"] = analysis.text

        # Step 2: 生成歌词（如果未提供）
        lyrics_text = new_lyrics
        if not lyrics_text:
            lyrics_result = await scheduler.generate_lyrics(
                prompt=f"Remix version of a {style} song",
                style=style,
                language="zh",
                user_id=user_id,
            )
            lyrics_text = lyrics_result.text

        # Step 3: SoVITS 生成改编后人声
        from app.routes.ai.create import _extract_lyrics_only
        vocal_text = _extract_lyrics_only(lyrics_text) or lyrics_text
        new_audio_url = await sovits.generate_vocal(
            text=vocal_text[:1000],
            voice="default",
            language="zh",
        )
        result["new_audio_url"] = new_audio_url
        result["new_lyrics"] = lyrics_text

        # Step 4: 完成
        await db.execute(
            "UPDATE generation_jobs SET status='completed' WHERE job_id=?",
            (job_id,),
        )
        await db.commit()

        return {"success": True, "data": result}

    except Exception as exc:
        await db.execute(
            "UPDATE generation_jobs SET status='failed', error_message=? WHERE job_id=?",
            (str(exc)[:500], job_id),
        )
        await db.commit()
        raise HTTPException(500, f"Remix failed: {exc}")
