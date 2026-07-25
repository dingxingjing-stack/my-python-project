"""Suno API client wrapper.

Suno currently has no official public API. This wrapper talks to a
community-maintained OpenAI-compatible endpoint (e.g. `suno-api` on
HuggingFace), which mirrors Suno's internal protocol. Swap the base
URL any time the upstream project changes.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.models.schemas import GenerateMusicRequest, GeneratedTrack


class SunoClient:
    def __init__(self) -> None:
        s = get_settings()
        if not s.suno_api_base or not s.suno_api_key:
            logger.warning("Suno API not configured")
            self._client: Optional[AsyncOpenAI] = None
            return
        self._client = AsyncOpenAI(
            base_url=s.suno_api_base,
            api_key=s.suno_api_key,
        )
        self._default_model = "suno-v5"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(self, req: GenerateMusicRequest) -> List[GeneratedTrack]:
        """Submit a Suno generation request and poll until done."""
        if self._client is None:
            raise RuntimeError("Suno API is not configured")

        # The community API exposes a custom `audio.speech` endpoint
        # extended with Suno-specific fields. We use the raw client to
        # keep the implementation flexible across upstream versions.
        payload: Dict[str, Any] = {
            "model": req.model or self._default_model,
            "prompt": req.prompt,
            "instrumental": req.instrumental,
        }
        if req.style:
            payload["style"] = req.style
        if req.title:
            payload["title"] = req.title
        if req.lyrics:
            payload["lyrics"] = req.lyrics

        logger.info("Suno submit: model={} prompt={!r}", payload["model"], req.prompt)

        response = await self._submit(payload)
        # Poll until the Suno tasks finish. Typical latency: 30-60s.
        completed = await self._poll(response["task_ids"])

        tracks: List[GeneratedTrack] = []
        for item in completed:
            tracks.append(
                GeneratedTrack(
                    audio_url=item.get("audio_url", ""),
                    cover_url=item.get("cover_url"),
                    lyrics=item.get("lyrics"),
                    title=item.get("title", req.title or "Untitled"),
                    duration_ms=item.get("duration_ms"),
                )
            )
        return tracks

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Use httpx via OpenAI client's underlying request helper.
        # Different community forks accept different routes; we default
        # to POST {base}/v1/audio/speech which is the canonical path
        # used by the `suno-api` reverse-engineered project.
        resp = await self._client.post(
            "/audio/speech",
            body=payload,
        )
        data = resp.json()
        if "task_ids" not in data:
            raise RuntimeError(f"Suno unexpected response: {data}")
        return data

    async def _poll(
        self, task_ids: List[str], timeout: int = 300, interval: int = 5
    ) -> List[Dict[str, Any]]:
        """Poll Suno for completion of every task id."""
        deadline = asyncio.get_event_loop().time() + timeout
        results: Dict[str, Dict[str, Any]] = {}

        while len(results) < len(task_ids):
            for tid in task_ids:
                if tid in results:
                    continue
                detail = await self._fetch_task(tid)
                status = detail.get("status")
                if status == "completed":
                    results[tid] = detail
                elif status == "error":
                    raise RuntimeError(f"Suno task {tid} error: {detail}")
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("Suno generation timed out")
            await asyncio.sleep(interval)

        return list(results.values())

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
    async def _fetch_task(self, task_id: str) -> Dict[str, Any]:
        resp = await self._client.get(f"/tasks/{task_id}")
        return resp.json()


_client: Optional[SunoClient] = None


def get_suno_client() -> SunoClient:
    global _client
    if _client is None:
        _client = SunoClient()
    return _client
