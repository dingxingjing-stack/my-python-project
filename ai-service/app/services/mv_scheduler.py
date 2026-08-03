"""MV 生成三层降级异步调度器。

通道优先级：
  Layer 1  Agnes AI 视频（免费，独立信号量限流 ≤3） + Modal MusicGen-small 音乐
  Layer 2  Modal CogVideoX 视频 + MusicGen 音乐（Agnes 限流/报错时接管全部任务）
  Layer 3  FFmpeg 幻灯片视频 + SoundHelix 免费背景音乐（Modal 算力用尽/异常兜底）

并发控制：
  _agnes_sem  最大并发 3（独立隔离，429 限流重试见 agnes_video_client）
  _modal_sem  全局并发上限 8（CogVideoX + MusicGen 共用）

通道健康：
  每个通道跟踪连续失败次数，达到阈值后熔断冷却（一段时间内跳过该通道），
  自动切换到下一层通道；成功一次即清零失败计数。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from loguru import logger

from app.services.local_storage import get_local_storage

# Agnes 视频并发上限（独立隔离）
_MAX_AGNES_CONCURRENCY = 3
# Modal GPU 全局并发上限
_MAX_MODAL_CONCURRENCY = 8

# 通道熔断参数
_CHANNEL_MAX_CONSECUTIVE_FAILURES = 2
_CHANNEL_COOLDOWN_SECS = 300.0

_SOUNDHELIX_MP3_URL = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"


class ChannelHealth:
    """通道健康跟踪：连续失败 N 次后熔断冷却，期间跳过该通道。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.consecutive_failures = 0
        self.disabled_until = 0.0

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.disabled_until = 0.0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= _CHANNEL_MAX_CONSECUTIVE_FAILURES:
            self.disabled_until = time.monotonic() + _CHANNEL_COOLDOWN_SECS
            logger.warning(
                "[MV] 通道 {} 连续失败 {} 次，熔断 {}s，后续任务自动切换通道",
                self.name, self.consecutive_failures, _CHANNEL_COOLDOWN_SECS,
            )

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.disabled_until


class MVScheduler:
    """MV 三层降级调度器。"""

    def __init__(self) -> None:
        self._agnes_sem = asyncio.Semaphore(_MAX_AGNES_CONCURRENCY)
        self._modal_sem = asyncio.Semaphore(_MAX_MODAL_CONCURRENCY)
        self.agnes_health = ChannelHealth("agnes")
        self.modal_health = ChannelHealth("modal")
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15))

    # ------------------------------------------------------------------
    # 场景视频：Agnes → CogVideoX → 返回 None（由上层走幻灯片兜底）
    # ------------------------------------------------------------------

    async def generate_scene_video(
        self, scene: Dict[str, Any], style: str, storage,
        scene_idx: int = 0,
    ) -> Tuple[Optional[str], str]:
        """生成单个场景的动态视频片段。

        返回 (video_url, channel)；channel ∈ {agnes, modal, none}。
        video_url 为本地 /uploads/... 路径；none 表示两层都失败，交给 FFmpeg 幻灯片。
        """
        prompt = scene.get("image_prompt", "") or scene.get("description", "")
        if not prompt:
            return None, "none"

        # Layer 1: Agnes 免费视频
        if self.agnes_health.available:
            try:
                async with self._agnes_sem:
                    url = await self._try_agnes(prompt, style, storage, scene_idx)
                if url:
                    self.agnes_health.record_success()
                    return url, "agnes"
            except Exception as exc:
                logger.warning("[MV] scene{} Agnes 失败: {}", scene_idx, exc)
            self.agnes_health.record_failure()

        # Layer 2: Modal CogVideoX
        if self.modal_health.available:
            try:
                async with self._modal_sem:
                    url = await self._try_modal_video(prompt, style, storage, scene_idx)
                if url:
                    self.modal_health.record_success()
                    return url, "modal"
            except Exception as exc:
                logger.warning("[MV] scene{} Modal CogVideoX 失败: {}", scene_idx, exc)
            self.modal_health.record_failure()

        return None, "none"

    # ------------------------------------------------------------------
    # 音乐：MusicGen → SoundHelix 免费 MP3
    # ------------------------------------------------------------------

    async def generate_music(
        self, title: str, style: str, storage,
    ) -> Tuple[Optional[str], str]:
        """生成 MV 背景音乐。返回 (audio_url, channel)；channel ∈ {musicgen, soundhelix, none}。"""
        music_prompt = f"{style} instrumental background music for {title}, cinematic"

        # Layer 1: Modal MusicGen-small
        if self.modal_health.available:
            try:
                async with self._modal_sem:
                    from app.services.modal_gpu_client import musicgen_generate
                    wav_bytes = await musicgen_generate(prompt=music_prompt, max_new_tokens=512)
                if wav_bytes:
                    self.modal_health.record_success()
                    url = storage.save_audio(wav_bytes, ext="wav")
                    logger.info("[MV] 音乐经 MusicGen 生成: {}", url)
                    return url, "musicgen"
            except Exception as exc:
                logger.warning("[MV] MusicGen 失败: {}", exc)
            self.modal_health.record_failure()

        # Layer 2: SoundHelix 免费背景音乐
        try:
            resp = await self._http.get(_SOUNDHELIX_MP3_URL)
            if resp.status_code == 200 and resp.content:
                url = storage.save_audio(resp.content, ext="mp3")
                logger.info("[MV] 音乐经 SoundHelix 兜底: {}", url)
                return url, "soundhelix"
        except Exception as exc:
            logger.warning("[MV] SoundHelix 下载失败: {}", exc)

        return None, "none"

    # ------------------------------------------------------------------
    # 内部：各通道实现
    # ------------------------------------------------------------------

    async def _try_agnes(
        self, prompt: str, style: str, storage, scene_idx: int,
    ) -> Optional[str]:
        from app.services.agnes_video_client import get_agnes_video_client
        agnes = get_agnes_video_client()
        if not agnes.is_configured:
            return None
        task = await agnes.text_to_video(prompt=f"{style}: {prompt}", duration=5)
        video_id = task.get("video_id") or task.get("id", "")
        if not video_id:
            return None
        result = await agnes.wait_for_task(video_id, timeout=300)
        video_url = agnes.extract_video_url(result)
        if not video_url:
            return None
        resp = await self._http.get(video_url)
        if resp.status_code == 200 and resp.content:
            url = storage.save_video(resp.content, ext="mp4")
            logger.info("[MV] scene{} Agnes 视频完成: {}", scene_idx, url)
            return url
        return None

    async def _try_modal_video(
        self, prompt: str, style: str, storage, scene_idx: int,
    ) -> Optional[str]:
        from app.services.modal_gpu_client import cogvideo_generate
        mp4_bytes = await cogvideo_generate(
            prompt=f"{style}, {prompt}", num_frames=49, steps=30, timeout=1500.0,
        )
        if not mp4_bytes:
            return None
        url = storage.save_video(mp4_bytes, ext="mp4")
        logger.info("[MV] scene{} Modal CogVideoX 视频完成: {}", scene_idx, url)
        return url


_scheduler: Optional[MVScheduler] = None


def get_mv_scheduler() -> MVScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MVScheduler()
    return _scheduler
