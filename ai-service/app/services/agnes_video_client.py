"""Agnes Video V2.0 客户端 — 免费 AI 视频生成（替代 Runway 做 MV 动态镜头）。

API:
  POST https://apihub.agnes-ai.com/v1/videos         创建视频任务
  GET  https://apihub.agnes-ai.com/agnesapi?video_id=<ID>  查询任务结果
当前价格: $0 / 秒（免费）。

参考: https://agnes-ai.com/doc/agnes-video-v20
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional

import httpx
from loguru import logger


class AgnesVideoClient:
    def __init__(self) -> None:
        self._key = os.getenv("AGNES_API_KEY", "")
        self._base = "https://apihub.agnes-ai.com"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=15),
            headers={"Authorization": f"Bearer {self._key}"} if self._key else None,
        )
        # 免费层限速：1 个视频任务 / 60 秒。用时间戳队列强制节流。
        self._rate_min_interval = 60.0
        self._last_create_at: float = 0.0
        self._rate_lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(self._key) and not self._key.startswith("your-")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def text_to_video(self, prompt: str, duration: int = 5) -> Dict[str, Any]:
        """纯文本生成视频（无需图片）。返回任务 dict。"""
        return await self._submit({
            "model": "agnes-video-v2.0",
            "prompt": prompt,
            "num_frames": self._frames_for(duration),
            "frame_rate": 24,
        })

    async def image_to_video(
        self, start_image: str, prompt: str, duration: int = 5
    ) -> Dict[str, Any]:
        """图片转动态视频。返回任务 dict。"""
        return await self._submit({
            "model": "agnes-video-v2.0",
            "prompt": prompt,
            "image": start_image,
            "num_frames": self._frames_for(duration),
            "frame_rate": 24,
        })

    async def get_task(self, video_id: str) -> Dict[str, Any]:
        resp = await self._client.get(
            f"{self._base}/agnesapi", params={"video_id": video_id}
        )
        resp.raise_for_status()
        return resp.json()

    async def wait_for_task(
        self, video_id: str, timeout: int = 600, interval: int = 10
    ) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            detail = await self.get_task(video_id)
            status = detail.get("status")
            logger.debug("Agnes task={} status={} progress={}", video_id, status, detail.get("progress"))
            if status == "completed":
                return detail
            if status == "failed":
                raise RuntimeError(f"Agnes task {video_id} -> failed: {detail.get('error')}")
            if loop.time() > deadline:
                raise TimeoutError("Agnes video generation timed out")
            await asyncio.sleep(interval)

    @staticmethod
    def extract_video_url(detail: Dict[str, Any]) -> str:
        """从任务结果提取最终视频 URL。"""
        metadata = detail.get("metadata") or {}
        url = metadata.get("url", "")
        if url:
            return url
        return detail.get("url", "")

    # ------------------------------------------------------------------

    @staticmethod
    def _frames_for(duration: int) -> int:
        """按目标秒数映射到 num_frames（8n+1 规则，frame_rate=24）。"""
        return {3: 81, 5: 121, 10: 241}.get(min(int(duration), 10), 121)

    async def _submit(self, body: Dict[str, Any]) -> Dict[str, Any]:
        # 免费层限速：确保两次任务创建间隔 >= 60s（含 429 重试退避）
        async with self._rate_lock:
            now = time.monotonic()
            wait = self._rate_min_interval - (now - self._last_create_at)
            if wait > 0:
                logger.info("Agnes 限速节流，等待 {:.1f}s 后再创建任务", wait)
                await asyncio.sleep(wait)

            for attempt in range(3):
                resp = await self._client.post(f"{self._base}/v1/videos", json=body)
                if resp.status_code in (200, 201):
                    self._last_create_at = time.monotonic()
                    return resp.json()
                if resp.status_code == 429:
                    retry_after = self._rate_min_interval
                    logger.warning("Agnes 429 限速，等待 {:.0f}s 重试 (attempt {})", retry_after, attempt + 1)
                    await asyncio.sleep(retry_after)
                    continue
                logger.error("Agnes create error {}: {}", resp.status_code, resp.text[:500])
                resp.raise_for_status()
        raise RuntimeError("Agnes video create failed after retries")


_client: Optional[AgnesVideoClient] = None


def get_agnes_video_client() -> AgnesVideoClient:
    global _client
    if _client is None:
        _client = AgnesVideoClient()
    return _client
