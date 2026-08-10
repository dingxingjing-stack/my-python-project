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
# 歌词生成总超时（外部 LLM 或本地网关）。默认 60s，覆盖 Stage 6 实测慢调用（21–38s），可用 LYRICS_TIMEOUT_SECS 覆盖。
LYRICS_TIMEOUT_SECS = int(os.getenv("LYRICS_TIMEOUT_SECS", "60"))


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
    except HTTPException as exc:
        # 配额/限流类错误（429）必须真实透传，不得被 Mock fallback 吞掉
        raise
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
                "is_mock": True,
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


def _build_mock_lyrics(prompt: str, style: str, language: str) -> str:
    """Mock 兜底歌词：统一从 Language Registry 读模板，不再按语言写特例。"""
    from app.services.language_registry import get as get_lang_cap

    cap = get_lang_cap(language)
    mock = cap.mock_template
    if not mock:
        # 该语言无预置模板 → 回退默认英文，仍走 Registry，避免 if pt/zh/es 特例
        from app.services.language_registry import get as _reg_get
        mock = _reg_get("en").mock_template
    base = prompt[:20] if prompt else "Untitled"
    return "Title: {}\n\n{}".format(base, mock)