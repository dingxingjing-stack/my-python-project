"""一键 AI 创作路由 — V2.0 纯本地方案（无 Suno）。

全链路：
1. Nemotron 生成完整歌词 + 曲风 + BPM + 配器描述
2. 本地 SoVITS 引擎生成人声干声
3. 大模型生成配器 JSON → 本地 FFmpeg 合成伴奏
4. 硅基 SDXL 生成封面图
5. Nemotron 生成 MV 分镜 → 硅基 SDXL 生图 → FFmpeg 合成视频
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import get_db
from app.services.ai_scheduler import get_scheduler
from app.services.sovits_engine import get_sovits_engine
from app.services.local_storage import get_local_storage

router = APIRouter(prefix="/ai", tags=["ai-create"])


class OneClickCreateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000, description="一句话描述你想要的歌曲")
    style: Optional[str] = Field(default="pop", description="音乐风格")
    language: str = Field(default="zh", description="歌词语言")
    mood: str = Field(default="", description="情绪")
    vocal: str = Field(default="auto", description="人声类型")
    generate_mv: bool = Field(default=True, description="是否同时生成 MV")
    mv_style: str = Field(default="Cinematic", description="MV 视觉风格")
    voice: str = Field(default="default", description="SoVITS 音色名称")


from typing import Optional


@router.post("/one-click-create")
async def one_click_create(req: OneClickCreateRequest):
    """一句话生成完整作品：歌词 + 人声 + 伴奏 + 封面 + MV。"""
    user_id = 1
    scheduler = get_scheduler()
    sovits = get_sovits_engine()
    job_id = str(uuid.uuid4())[:8]

    db = await get_db()
    await db.execute(
        "INSERT INTO ai_creations (job_id, user_id, prompt_text, title, style_tags, language, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, user_id, req.prompt, req.prompt[:30], req.style or "pop", req.language, "processing"),
    )
    await db.commit()
    creation_id = (await (await db.execute("SELECT last_insert_rowid()")).fetchone())[0]

    await db.execute(
        "INSERT INTO generation_jobs (job_id, user_id, task_type, status) VALUES (?, ?, ?, ?)",
        (job_id, user_id, "one_click", "processing"),
    )
    await db.commit()

    result = {
        "creation_id": creation_id,
        "job_id": job_id,
        "lyrics": None,
        "lrc": None,
        "audio_url": None,
        "cover_url": None,
        "video_url": None,
        "title": req.prompt[:30],
        "music_production": None,
    }

    try:
        # ── 阶段 1：Nemotron 生成歌词 + 曲风 + BPM + 配器 ──
        try:
            lyrics_result = await scheduler.generate_lyrics(
                prompt=req.prompt,
                style=req.style or "pop",
                language=req.language,
                mood=req.mood,
                vocal=req.vocal,
                user_id=user_id,
            )
            result["lyrics"] = lyrics_result.text
            result["lrc"] = _extract_lrc(lyrics_result.text)
            result["title"] = _extract_title(lyrics_result.text) or req.prompt[:30]

            lyrics_id = None
            await db.execute(
                "INSERT INTO lyrics (creation_id, user_id, version, title, lyrics_text, lrc_text, prompt_text, style_tags, language, model_name) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
                (creation_id, user_id, result["title"], lyrics_result.text, result["lrc"], req.prompt, req.style or "pop", req.language, lyrics_result.model_name),
            )
            await db.commit()
            lyrics_id = (await (await db.execute("SELECT last_insert_rowid()")).fetchone())[0]
        except Exception as exc:
            lyrics_id = None

        # ── 阶段 2：Nemotron 生成配器参数 ──
        accompaniment = None
        try:
            if result["lyrics"]:
                accompaniment = await scheduler.generate_accompaniment(
                    lyrics=result["lyrics"],
                    style=req.style or "pop",
                    user_id=user_id,
                )
                result["music_production"] = accompaniment.text
        except Exception:
            pass

        # ── 阶段 3：SoVITS 生成人声干声 ──
        try:
            if result["lyrics"]:
                vocal_text = _extract_lyrics_only(result["lyrics"]) or result["lyrics"]
                audio_url = await sovits.generate_vocal(
                    text=vocal_text[:1000],
                    voice=req.voice,
                    language=req.language,
                )
                result["audio_url"] = audio_url

                await db.execute(
                    "INSERT INTO songs (creation_id, lyrics_id, user_id, version, title, audio_url, style_tags, model_name, generation_prompt) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
                    (creation_id, lyrics_id, user_id, result["title"], result["audio_url"], req.style or "pop", "sovits-local", vocal_text[:200]),
                )
                await db.commit()
        except Exception as exc:
            pass

        # ── 阶段 4：硅基 SDXL 生成封面 ──
        try:
            if result["lyrics"]:
                cover_prompt_result = await scheduler.generate_cover_prompt(
                    lyrics=result["lyrics"],
                    title=result["title"],
                    style=req.mv_style,
                    user_id=user_id,
                )
                image_result = await scheduler.generate_image_sdxl(
                    prompt=cover_prompt_result.text,
                    num_images=1,
                    user_id=user_id,
                )
                if image_result.data.get("image_urls"):
                    result["cover_url"] = image_result.data["image_urls"][0]

                await db.execute(
                    "INSERT INTO images (creation_id, user_id, image_url, image_type, style, prompt_text, model_name) VALUES (?, ?, ?, 'cover', ?, ?, ?)",
                    (creation_id, user_id, result["cover_url"] or "", req.mv_style, cover_prompt_result.text, image_result.model_name),
                )
                await db.commit()
        except Exception:
            pass

        # ── 阶段 5：MV 生成（硅基 SDXL 生图 + Runway + FFmpeg） ──
        if req.generate_mv and result["lyrics"]:
            try:
                from app.routes.ai.mv import _generate_mv_from_lyrics
                video_url = await _generate_mv_from_lyrics(
                    lyrics=result["lyrics"],
                    title=result["title"],
                    mv_style=req.mv_style,
                    user_id=user_id,
                    creation_id=creation_id,
                    scheduler=scheduler,
                )
                result["video_url"] = video_url

                await db.commit()
            except Exception:
                pass

        # 更新状态
        await db.execute(
            "UPDATE ai_creations SET status='completed', title=?, audio_url=?, cover_url=?, video_url=?, lyrics=? WHERE id=?",
            (result["title"], result["audio_url"], result["cover_url"], result["video_url"], result["lyrics"], creation_id),
        )
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
        await db.execute(
            "UPDATE ai_creations SET status='failed' WHERE id=?",
            (creation_id,),
        )
        await db.commit()
        raise HTTPException(500, f"One-click creation failed: {exc}")


def _extract_title(text: str) -> str:
    import re
    match = re.search(r"Title:\s*(.+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_lrc(text: str) -> str:
    import re
    match = re.search(r"LRC:\s*([\s\S]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_lyrics_only(text: str) -> str:
    """从 AI 返回中提取纯歌词（去除 Title/LRC 等标记）。"""
    import re
    # 去掉 LRC 部分
    text = re.sub(r"LRC:\s*[\s\S]+", "", text)
    # 去掉 Title 行
    text = re.sub(r"Title:.*", "", text)
    # 去掉结构标记
    text = re.sub(r"(Verse|Chorus|Bridge|Intro|Outro)\s*\d*:", "", text)
    return text.strip()
