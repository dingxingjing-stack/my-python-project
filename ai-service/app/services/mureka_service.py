"""Mureka AI 音乐生成服务 — 多引擎降级链入口。"""
from __future__ import annotations

import os
import uuid
from typing import Optional

import httpx

from app.config import get_settings


class QuotaExceededError(Exception):
    """Mureka 配额耗尽/余额不足。"""


class MurekaSongRequest:
    def __init__(self, lyrics: str, style: str = "pop", duration: Optional[int] = None):
        self.lyrics = lyrics
        self.style = style
        self.duration = duration


class MurekaResult:
    def __init__(self, success: bool, audio_url: str = "", task_id: str = ""):
        self.success = success
        self.audio_url = audio_url
        self.task_id = task_id


class MurekaService:
    """Mureka API 封装。

    环境变量:
        MUREKA_API_BASE — API 地址（默认 https://api.mureka.ai/v1）
        MUREKA_API_KEY  — API Key
    """

    def __init__(self):
        self.api_base = os.getenv("MUREKA_API_BASE", "https://api.mureka.ai/v1")
        self.api_key = os.getenv("MUREKA_API_KEY", "")
        self._configured = bool(self.api_key)

    async def generate_song(self, req: MurekaSongRequest) -> MurekaResult:
        if not self._configured:
            print("[Mureka] 未配置 API Key，抛出 QuotaExceededError 触发降级")
            raise QuotaExceededError("Mureka API key not configured")

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                payload = {
                    "lyrics": req.lyrics,
                    "style": req.style,
                    "duration": req.duration or 30,
                }
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                resp = await client.post(
                    f"{self.api_base}/songs/generate",
                    json=payload,
                    headers=headers,
                )

            if resp.status_code == 429:
                raise QuotaExceededError("Mureka quota exceeded")

            if resp.status_code != 200:
                print(f"[Mureka] API error {resp.status_code}: {resp.text}")
                return MurekaResult(success=False)

            data = resp.json()
            audio_url = (data.get("data") or data).get("audio_url") or ""
            task_id = (data.get("data") or data).get("task_id") or str(uuid.uuid4())[:8]
            return MurekaResult(success=bool(audio_url), audio_url=audio_url, task_id=task_id)

        except QuotaExceededError:
            raise
        except Exception as e:
            print(f"[Mureka] 请求异常: {e}")
            return MurekaResult(success=False)


mureka_service = MurekaService()
