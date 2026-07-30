"""AI lyrics generation routes — V1.0 对接 ai_scheduler。"""
from __future__ import annotations

import json
import os
import re
from fastapi import APIRouter, HTTPException, Request

from app.database import get_db
from app.services.ai_scheduler import get_scheduler
from app.services.feature_flags import require_feature

router = APIRouter(prefix="/ai", tags=["ai-lyrics"])

MOCK_FALLBACK_ENABLED = os.getenv("MOCK_FALLBACK", "true").lower() in ("1", "true", "yes")


@router.post("/lyrics")
@require_feature("ai_lyrics")
async def generate_lyrics(request: Request):
    """歌词生成 — 对接硅基 Qwen2.5-7B-Instruct，自动扣 1 Credits。"""
    req = await request.json()
    user_id = req.get("user_id", 1)
    prompt = req.get("prompt", "")
    style = req.get("style", "pop")
    language = req.get("language", "zh")
    mood = req.get("mood", "")
    vocal = req.get("vocal", "auto")
    creation_id = req.get("creation_id")

    if not prompt:
        raise HTTPException(400, "Missing prompt")

    scheduler = get_scheduler()

    try:
        result = await scheduler.generate_lyrics(
            prompt=prompt,
            style=style,
            language=language,
            mood=mood,
            vocal=vocal,
            user_id=user_id,
        )
    except Exception as exc:
        print(f"[lyrics] AI generation failed: {exc}")
        if not MOCK_FALLBACK_ENABLED:
            raise HTTPException(500, f"AI generation failed: {exc}")
        # Mock 兜底 — 返回模板歌词让前端流程能跑通
        mock_text = _build_mock_lyrics(prompt, style, language)
        db = await get_db()
        cur = await db.execute(
            """
            INSERT INTO lyrics (creation_id, user_id, version, title, lyrics_text, lrc_text, prompt_text, style_tags, language, model_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (creation_id, user_id, 1, f"[Mock] {prompt[:20]}",
             mock_text, "", prompt, style, language, "mock"),
        )
        await db.commit()
        return {
            "success": True,
            "data": {
                "lyrics_id": cur.lastrowid,
                "version": 1,
                "title": f"[Mock] {prompt[:20]}",
                "lyrics": mock_text,
                "lrc": "",
                "model": "mock",
                "provider": "mock",
                "elapsed_ms": 0,
            },
        }

    # 解析 AI 返回的歌词结构
    parsed = _parse_lyrics(result.text)

    # 存入 lyrics 表（支持多版本）
    db = await get_db()
    cur = await db.execute(
        """
        INSERT INTO lyrics (creation_id, user_id, version, title, lyrics_text, lrc_text, prompt_text, style_tags, language, model_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            creation_id,
            user_id,
            1,  # version
            parsed.get("title", ""),
            parsed.get("lyrics", result.text),
            parsed.get("lrc", ""),
            prompt,
            style,
            language,
            result.model_name,
        ),
    )
    await db.commit()
    lyrics_id = cur.lastrowid

    # 查询版本号
    ver_row = await (await db.execute(
        "SELECT COUNT(*) FROM lyrics WHERE creation_id = ? AND user_id = ?",
        (creation_id, user_id),
    )).fetchone()
    version_num = ver_row[0] if ver_row else 1

    return {
        "success": True,
        "data": {
            "lyrics_id": lyrics_id,
            "version": version_num,
            "title": parsed.get("title", ""),
            "lyrics": parsed.get("lyrics", result.text),
            "lrc": parsed.get("lrc", ""),
            "model": result.model_name,
            "provider": result.provider,
            "elapsed_ms": result.elapsed_ms,
        },
    }


@router.get("/lyrics/{lyrics_id}")
async def get_lyrics(lyrics_id: int):
    """获取单条歌词记录。"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM lyrics WHERE id = ?", (lyrics_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Lyrics not found")
    return {"success": True, "data": dict(row)}


@router.get("/lyrics/versions/{creation_id}")
async def list_lyrics_versions(creation_id: int):
    """获取某作品的所有歌词版本。"""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM lyrics WHERE creation_id = ? ORDER BY version DESC",
        (creation_id,),
    )
    rows = await cur.fetchall()
    return {"success": True, "data": [dict(r) for r in rows]}


def _parse_lyrics(text: str) -> dict:
    """解析 AI 返回的歌词结构。"""
    result = {"title": "", "lyrics": text, "lrc": ""}

    # 提取标题
    title_match = re.search(r"Title:\s*(.+)", text, re.IGNORECASE)
    if title_match:
        result["title"] = title_match.group(1).strip()

    # 提取 LRC
    lrc_match = re.search(r"LRC:\s*([\s\S]+)", text, re.IGNORECASE)
    if lrc_match:
        result["lrc"] = lrc_match.group(1).strip()

    return result


def _build_mock_lyrics(prompt: str, style: str, language: str) -> str:
    """生成 Mock 歌词（AI 不可用时兜底）。"""
    is_zh = language.startswith("zh")
    title = prompt[:20] if prompt else "Untitled"
    if is_zh:
        return f"""Title: {title}

Verse 1:
星光落在窗前
夜色温柔如水
心中那首歌谣
随风轻轻飘远

Chorus:
追梦的人永不疲倦
穿越风雨也从容
我们都在路上
奔向心中的光

Verse 2:
回忆变成诗句
写下每个瞬间
时间不会停歇
但爱永远年轻

Bridge:
走过四季变迁
依然相信明天
心中的火焰
从未熄灭过

Chorus:
追梦的人永不疲倦
穿越风雨也从容
我们都在路上
奔向心中的光

LRC:
[00:00.00]星光落在窗前
[00:05.00]夜色温柔如水
[00:10.00]心中那首歌谣
[00:15.00]随风轻轻飘远
[00:20.00]追梦的人永不疲倦
[00:25.00]穿越风雨也从容"""
    else:
        return f"""Title: {title}

Verse 1:
Starlight falls upon the window
Night is gentle as the water
The song inside my heart
Drifts away with the wind

Chorus:
Dreamers never tire
Walking through the storm with grace
We are all on the road
Running to the light within

LRC:
[00:00.00]Starlight falls upon the window
[00:05.00]Night is gentle as the water
[00:10.00]The song inside my heart
[00:15.00]Drifts away with the wind"""