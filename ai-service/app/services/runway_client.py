"""Runway Gen-4.5 video generation client.

Reference: https://dev.runwayml.com/
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.models.schemas import GenerateMVRequest


class RunwayClient:
    def __init__(self) -> None:
        s = get_settings()
        self._key = s.runway_api_key
        self._base = s.runway_api_base.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=10),
            headers={"X-Runway-API-Key": self._key} if self._key else None,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self._key)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def text_to_video(
        self, prompt: str, duration: int = 5
    ) -> Dict[str, Any]:
        """Generate a single short video segment. Returns task dict."""
        body = {"promptText": prompt, "duration": duration, "model": "gen4-turbo"}
        return await self._submit(body)

    async def image_to_video(
        self,
        start_image: str,
        prompt: str,
        duration: int = 5,
    ) -> Dict[str, Any]:
        body = {
            "promptImage": start_image,
            "promptText": prompt,
            "duration": duration,
            "model": "gen4-turbo",
        }
        return await self._submit(body)

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        resp = await self._client.get(f"{self._base}/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

    async def wait_for_task(
        self, task_id: str, timeout: int = 600, interval: int = 5
    ) -> Dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            detail = await self.get_task(task_id)
            status = detail.get("status")
            logger.debug("Runway task={} status={}", task_id, status)
            if status in ("SUCCEEDED", "COMPLETED"):
                return detail
            if status in ("FAILED", "CANCELED"):
                raise RuntimeError(f"Runway task {task_id} -> {status}")
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("Runway generation timed out")
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _submit(self, body: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._client.post(f"{self._base}/text_to_video", json=body)
        resp.raise_for_status()
        return resp.json()


_client: Optional[RunwayClient] = None


def get_runway_client() -> RunwayClient:
    global _client
    if _client is None:
        _client = RunwayClient()
    return _client
