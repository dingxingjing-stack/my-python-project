"""GPT-SoVITS 本地推理引擎封装 — 零第三方 API，纯本地音频合成。

接口：
  - POST /v1/tts         文本 → 人声干声 WAV
  - POST /v1/voice/train 上传音频样本训练音色
  - GET  /v1/voice/list  获取可用音色列表
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from app.config import get_settings
from app.services.local_storage import get_local_storage


class SoVITSEngine:
    """GPT-SoVITS API 客户端封装。

    引擎地址由 SOVITS_API_BASE 指定（默认 http://127.0.0.1:9880）。
    引擎未启动时返回模拟音频（占位），不影响全流程跑通。
    """

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.sovits_api_base.rstrip("/")
        self._enabled = s.sovits_enabled
        self._default_lang = s.sovits_default_language
        self._client = httpx.AsyncClient(timeout=300)

    @property
    def is_ready(self) -> bool:
        """检查 SoVITS 引擎是否可达。"""
        if not self._enabled:
            return False
        try:
            import asyncio
            resp = asyncio.run(self._client.get(f"{self._base}/v1/health"))
            return resp.status_code == 200
        except Exception:
            return False

    async def generate_vocal(
        self,
        text: str,
        voice: str = "default",
        speed: float = 1.0,
        language: str = "zh",
    ) -> str:
        """文本转人声干声，返回本地音频 URL。

        Args:
            text: 歌词文本
            voice: 音色名称（默认 'default'）
            speed: 语速
            language: 语言代码

        Returns:
            本地存储的音频 URL 路径
        """
        if not self._enabled:
            return self._make_placeholder("vocal")

        try:
            resp = await self._client.post(
                f"{self._base}/v1/tts",
                json={
                    "text": text,
                    "voice": voice,
                    "speed": speed,
                    "language": language,
                },
            )
            resp.raise_for_status()
            audio_bytes = resp.content
            storage = get_local_storage()
            return storage.save_audio(audio_bytes, ext="wav")
        except Exception as exc:
            logger.warning("SoVITS TTS failed (using placeholder): {}", exc)
            return self._make_placeholder("vocal")

    async def train_voice(
        self,
        name: str,
        sample_audio_path: str,
        transcript: str = "",
    ) -> dict:
        """上传音频样本训练新音色。

        Args:
            name: 音色名称
            sample_audio_path: 样本音频本地路径
            transcript: 音频文本转录（可选）

        Returns:
            训练任务信息
        """
        if not self._enabled:
            return {"voice": name, "status": "simulated", "message": "SoVITS 未启用，模拟训练成功"}

        try:
            files = {"audio": open(sample_audio_path, "rb")}
            data = {"name": name}
            if transcript:
                data["transcript"] = transcript
            resp = await self._client.post(f"{self._base}/v1/voice/train", files=files, data=data)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("SoVITS train failed: {}", exc)
            return {"voice": name, "status": "simulated", "error": str(exc)}

    async def list_voices(self) -> list[dict]:
        """获取可用音色列表。"""
        if not self._enabled:
            return [{"name": "default", "language": "zh", "status": "simulated"}]

        try:
            resp = await self._client.get(f"{self._base}/v1/voice/list")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("SoVITS list_voices failed: {}", exc)
            return [{"name": "default", "language": "zh", "status": "simulated"}]

    def _make_placeholder(self, category: str) -> str:
        """SoVITS 不可用时生成占位文件。"""
        import struct
        import wave

        storage = get_local_storage()
        base = Path(storage._base) / "audio"
        base.mkdir(parents=True, exist_ok=True)

        uid = uuid.uuid4().hex[:12]
        path = base / f"placeholder_{uid}.wav"

        # 生成 3 秒静音 WAV
        sample_rate = 24000
        duration = 3
        num_samples = sample_rate * duration
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))

        return f"/uploads/audio/placeholder_{uid}.wav"


_engine: Optional[SoVITSEngine] = None


def get_sovits_engine() -> SoVITSEngine:
    global _engine
    if _engine is None:
        _engine = SoVITSEngine()
    return _engine
