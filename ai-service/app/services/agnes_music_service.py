"""Agnes 音乐提示词优化服务 — 使用 LLM 优化创作提示词并生成歌词。

降级链：Agnes API → SiliconFlow/OpenRouter LLM → 原样返回。
"""
from __future__ import annotations

import os
from typing import Optional

from app.services.llm_client import LLMClient


class AgnesSongRequest:
    def __init__(self, prompt: str, style: str = "pop", duration: int = 180, type: str = "song"):
        self.prompt = prompt
        self.style = style
        self.duration = duration
        self.type = type


class AgnesResult:
    def __init__(
        self,
        success: bool,
        optimized_prompt: str = "",
        generated_lyrics: str = "",
        error: str = "",
    ):
        self.success = success
        self.optimized_prompt = optimized_prompt
        self.generated_lyrics = generated_lyrics
        self.error = error


class AgnesMusicService:
    """Agnes / Gemini 风格提示词优化。

    优先调用 Agnes API（AGNES_API_BASE + AGNES_API_KEY），
    未配置时自动降级到本地 LLM（llm_client.py）。
    """

    def __init__(self):
        self.API_KEY = os.getenv("AGNES_API_KEY", "")
        self.api_base = os.getenv("AGNES_API_BASE", "https://api.agnes.ai/v1")
        self._llm = LLMClient()

    async def generate_song(self, req: AgnesSongRequest) -> AgnesResult:
        # 尝试 Agnes API
        if self.API_KEY:
            try:
                return await self._call_agnes_api(req)
            except Exception as e:
                print(f"[Agnes] API 调用失败，降级到 LLM: {e}")

        # 降级到本地 LLM
        return await self._call_llm_fallback(req)

    async def _call_agnes_api(self, req: AgnesSongRequest) -> AgnesResult:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.api_base}/lyrics/generate",
                json={
                    "prompt": req.prompt,
                    "style": req.style,
                    "duration": req.duration,
                    "type": req.type,
                },
                headers={
                    "Authorization": f"Bearer {self.API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Agnes API error: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        return AgnesResult(
            success=True,
            optimized_prompt=data.get("optimized_prompt", req.prompt),
            generated_lyrics=data.get("lyrics", ""),
        )

    async def _call_llm_fallback(self, req: AgnesSongRequest) -> AgnesResult:
        system_prompt = (
            "You are a professional music producer and lyricist. "
            "Given a user's creative prompt, produce:\n"
            "1. An optimized music generation prompt (2-3 sentences describing instrumentation, mood, tempo)\n"
            "2. A set of lyrics following standard song structure (verse, chorus, bridge)\n"
            "Format: OPTIMIZED_PROMPT: ...\n---\nLYRICS: ..."
        )
        user_prompt = f"Style: {req.style}\nPrompt: {req.prompt}\nDuration: {req.duration}s"
        try:
            if self._llm.available_providers():
                result = await self._llm.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=1024,
                )
            else:
                return AgnesResult(success=False, error="No LLM provider", optimized_prompt=req.prompt)
            text = (result or "").strip()
            optimized = req.prompt
            lyrics = ""

            if "OPTIMIZED_PROMPT:" in text:
                parts = text.split("OPTIMIZED_PROMPT:")
                if len(parts) > 1:
                    rest = parts[1].strip()
                    if "---" in rest:
                        optimized = rest.split("---")[0].strip()
                        lyrics_part = rest.split("---", 1)[1]
                        if "LYRICS:" in lyrics_part:
                            lyrics = lyrics_part.split("LYRICS:", 1)[1].strip()
                    else:
                        optimized = rest

            return AgnesResult(
                success=True,
                optimized_prompt=optimized,
                generated_lyrics=lyrics,
            )
        except Exception as e:
            print(f"[Agnes] LLM 降级失败: {e}")
            return AgnesResult(success=False, error=str(e), optimized_prompt=req.prompt)


agnes_service = AgnesMusicService()
