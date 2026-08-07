"""AI lyrics generation routes — V1.0对接 ai_scheduler。"""
from __future__ import annotations

import asyncio
import json
import os
import re
from fastapi import APIRouter, HTTPException, Request

from app.database import get_db
from app.services.ai_scheduler import get_scheduler
from app.services.feature_flags import require_feature

router = APIRouter(prefix="/ai", tags=["ai-lyrics"])

MOCK_FALLBACK_ENABLED = os.getenv("MOCK_FALLBACK", "true").lower() in ("1", "true", "yes")
# 歌词生成总超时（外部 LLM 或本地网关）+ 语法解析兜底恢复
LYRICS_TIMEOUT_SECS = int(os.getenv("LYRICS_TIMEOUT_SECS", "30"))


@router.post("/lyrics")
@require_feature("ai_lyrics")
async def generate_lyrics(request: Request):
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
        result = await asyncio.wait_for(
            scheduler.generate_lyrics(
                prompt=prompt,
                style=style,
                language=language,
                mood=mood,
                vocal=vocal,
                user_id=user_id,
            ),
            timeout=LYRICS_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        print(f"[lyrics] AI generation timed out after {LYRICS_TIMEOUT_SECS}s")
        if not MOCK_FALLBACK_ENABLED:
            raise HTTPException(504, "AI generation timed out")
        result = None
    except Exception as exc:
        print(f"[lyrics] AI generation failed: {exc}")
        if not MOCK_FALLBACK_ENABLED:
            raise HTTPException(500, f"AI generation failed: {exc}")
        result = None

    if result is None:
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

    parsed = _parse_lyrics(result.text)

    db = await get_db()
    cur = await db.execute(
        """
        INSERT INTO lyrics (creation_id, user_id, version, title, lyrics_text, lrc_text, prompt_text, style_tags, language, model_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            creation_id,
            user_id,
            1,
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
    db = await get_db()
    cur = await db.execute("SELECT * FROM lyrics WHERE id = ?", (lyrics_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Lyrics not found")
    return {"success": True, "data": dict(row)}


@router.get("/lyrics/versions/{creation_id}")
async def list_lyrics_versions(creation_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM lyrics WHERE creation_id = ? ORDER BY version DESC",
        (creation_id,),
    )
    rows = await cur.fetchall()
    return {"success": True, "data": [dict(r) for r in rows]}


def _parse_lyrics(text: str) -> dict:
    # 剥离模型可能输出的任务解释/英文元评论（若未严格遵守 system prompt）。
    # 找到一个实际歌词起始点（Title / 任意语言歌词首行）之前的部分一律丢弃。
    stripped = text.strip()
    start = re.search(r"(?m)^(Title:|Verse\s*\d+|Chorus|Bridge|LRC:)", stripped)
    if start:
        stripped = stripped[start.start():]
    # 去掉 LRC 之后的任何尾注
    lrc_end = re.search(r"(?m)^(LRC:)", stripped)
    if lrc_end:
        stripped = stripped[:lrc_end.start()].rstrip() + "\nLRC:\n"

    result = {"title": "", "lyrics": stripped, "lrc": ""}
    title_match = re.search(r"Title:\s*(.+)", stripped, re.IGNORECASE)
    if title_match:
        result["title"] = title_match.group(1).strip()
    lrc_match = re.search(r"LRC:\s*([\s\S]+)", stripped, re.IGNORECASE)
    if lrc_match:
        result["lrc"] = lrc_match.group(1).strip()
    return result


_MOCK_LYRICS = {
    "ja": """Verse 1:
星が窓辺に降り注ぐ
夜は水のように優しく
心の中の歌が
風に乗って遠くへ

Chorus:
夢を追う人は決して疲れない
嵐を越えて優雅に
僕たちはみんな旅の途中
心の光へ走り続ける

LRC:
[00:00.00]星が窓辺に降り注ぐ
[00:05.00]夜は水のように優しく
[00:10.00]心の中の歌が
[00:15.00]風に乗って遠くへ""",

    "ko": """Verse 1:
별빛이 창가에 내려와
밤은 물처럼 부드럽게
마음속의 그 노래가
바람을 타고 멀리 멀리

Chorus:
꿈을 찾는 우리는 멈추지 않아
폭풍을 가로질러
우린 모두 여정 중이야
마음속의 빛을 향해 달려가

LRC:
[00:00.00]별빛이 창가에 내려와
[00:05.00]밤은 물처럼 부드럽게
[00:10.00]마음속의 그 노래가
[00:15.00]바람을 타고 멀리 멀리""",

    "es": """Verse 1:
La luz de las estrellas sobre la ventana
La noche es suave como el agua
La cancion de mi corazon
Flota lejos con el viento

Chorus:
Los sonadores nunca se cansan
Atravesando la tormenta con gracia
Todos estamos en el camino
Corriendo hacia la luz interior

LRC:
[00:00.00]La luz de las estrellas
[00:05.00]La noche es suave como el agua
[00:10.00]La cancion de mi corazon
[00:15.00]Flota lejos con el viento""",

    "zh": """Verse 1:
星光落在窗前
夜色温柔如水
心中那首歌谣
随风轻轻飘远

Chorus:
追梦的人永不疲倦
穿越风雨也从容
我们都在路上
奔向心中的光

LRC:
[00:00.00]星光落在窗前
[00:05.00]月色温柔如水
[00:10.00]心中那首歌谣
[00:15.00]随风轻轻飘远""",

    "en": """Verse 1:
Starlight falls upon the garden
Night is gentle as the water
The song inside my heart
Drifts away with the wind

Chorus:
Dreamers never tire
Walking through the storm with grace
We are all on the road
Running toward the light within

LRC:
[00:00.00]Starlight falls upon the garden
[00:05.00]Night is gentle as the water
[00:10.00]The song inside my soul
[00:15.00]Drifts away with the wind""",
}


def _build_mock_lyrics(prompt: str, style: str, language: str) -> str:
    lang = (language or "en").lower()
    base = prompt[:20] if prompt else "Untitled"
    if lang not in _MOCK_LYRICS:
        lang = "en"
    return "Title: {}\n\n{}".format(base, _MOCK_LYRICS[lang])