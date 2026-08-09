"""AI 文本转语音路由 — 对接本地 Kokoro-82M TTS（Modal GPU，无外部 key）。

产出 WAV 保存到本地存储，返回 /uploads/audio/*.wav 可下载地址。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from app.services.local_storage import get_local_storage

router = APIRouter(prefix="/ai", tags=["ai-tts"])


@router.post("/tts")
async def text_to_speech(request: Request):
    """文本转语音，返回本地 WAV URL。"""
    req = await request.json()
    text = (req.get("text") or "").strip()
    voice = req.get("voice", "")
    speed = float(req.get("speed", 1.0))

    if not text:
        raise HTTPException(400, "Missing text")
    if len(text) > 2000:
        raise HTTPException(400, "text too long (max 2000)")

    from app.services.modal_gpu_client import kokoro_tts

    try:
        wav_bytes = await kokoro_tts(text=text[:600], voice=voice, speed=speed)
    except Exception as exc:
        print(f"[tts] Kokoro TTS 失败: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(500, f"TTS generation failed: {exc}")

    if not wav_bytes:
        print("[tts] Kokoro TTS 返回空（引擎不可用）", flush=True)
        raise HTTPException(503, "TTS engine unavailable")

    storage = get_local_storage()
    audio_url = storage.save_audio(wav_bytes, ext="wav")
    return {
        "success": True,
        "data": {
            "audio_url": audio_url,
            "format": "wav",
            "size": len(wav_bytes),
        },
    }


@router.get("/tts/voices")
async def list_voices():
    """可用 TTS 音色列表（Kokoro 内置）。"""
    voices = [
        {"name": "zf_xiaobei", "language": "zh"},
        {"name": "af_heart", "language": "en"},
    ]
    return {"success": True, "data": voices}