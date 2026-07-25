"""声音克隆 / SoVITS 路由 — 上传样本训练音色 + 文本转人声演唱。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from app.database import get_db
from app.services.sovits_engine import get_sovits_engine

router = APIRouter(prefix="/ai", tags=["ai-voice"])


@router.post("/voice/generate")
async def generate_vocal(
    text: str = Form(...),
    voice: str = Form("default"),
    speed: float = Form(1.0),
    language: str = Form("zh"),
):
    """文本转人声干声。"""
    engine = get_sovits_engine()
    try:
        audio_url = await engine.generate_vocal(
            text=text, voice=voice, speed=speed, language=language,
        )
        return {"success": True, "data": {"audio_url": audio_url}}
    except Exception as exc:
        raise HTTPException(500, f"Vocal generation failed: {exc}")


@router.post("/voice/train")
async def train_voice(
    name: str = Form(...),
    audio: UploadFile = File(...),
    transcript: str = Form(""),
):
    """上传音频样本训练新音色。"""
    engine = get_sovits_engine()
    from app.services.local_storage import get_local_storage
    storage = get_local_storage()
    audio_bytes = await audio.read()
    path = storage.save_audio(audio_bytes, ext="wav")

    try:
        result = await engine.train_voice(name=name, sample_audio_path=path, transcript=transcript)
        return {"success": True, "data": result}
    except Exception as exc:
        raise HTTPException(500, f"Voice training failed: {exc}")


@router.get("/voice/list")
async def list_voices():
    """获取可用音色列表。"""
    engine = get_sovits_engine()
    try:
        voices = await engine.list_voices()
        return {"success": True, "data": voices}
    except Exception as exc:
        raise HTTPException(500, f"Failed to list voices: {exc}")
