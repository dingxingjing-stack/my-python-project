"""Runway Gen-4 video generation client.

Reference: https://docs.dev.runwayml.com/
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
            timeout=httpx.Timeout(120, connect=10),
            headers={
                "Authorization": f"Bearer {self._key}",
                "X-Runway-Version": "2024-11-06",
            } if self._key else None,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self._key) and not self._key.startswith("your-")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def text_to_video(
        self, prompt: str, duration: int = 5
    ) -> Dict[str, Any]:
        """Generate a single short video segment. Returns task dict."""
        body = {
            "model": "gen4_turbo",
            "promptText": prompt,
            "duration": duration,
            "ratio": "1280:720",
        }
        return await self._submit(body)

    async def image_to_video(
        self,
        start_image: str,
        prompt: str,
        duration: int = 5,
    ) -> Dict[str, Any]:
        body = {
            "model": "gen4_turbo",
            "promptImage": start_image,
            "promptText": prompt,
            "duration": duration,
            "ratio": "1280:720",
        }
        return await self._submit(body, endpoint="image_to_video")

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        resp = await self._client.get(f"{self._base}/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

    async def wait_for_task(
        self, task_id: str, timeout: int = 600, interval: int = 5
    ) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            detail = await self.get_task(task_id)
            status = detail.get("status")
            logger.debug("Runway task={} status={}", task_id, status)
            if status in ("SUCCEEDED", "COMPLETED"):
                return detail
            if status in ("FAILED", "CANCELED"):
                raise RuntimeError(f"Runway task {task_id} -> {status}")
            if loop.time() > deadline:
                raise TimeoutError("Runway generation timed out")
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _submit(self, body: Dict[str, Any], endpoint: str = "text_to_video") -> Dict[str, Any]:
        resp = await self._client.post(f"{self._base}/{endpoint}", json=body)
        if resp.status_code != 200:
            logger.error("Runway {} error {}: {}", endpoint, resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp.json()


_client: Optional[RunwayClient] = None


def get_runway_client() -> RunwayClient:
    global _client
    if _client is None:
        _client = RunwayClient()
    return _client
