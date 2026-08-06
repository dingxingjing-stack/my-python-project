"""MV 生成调度器 — 本地开源模型优先，外部 API 兜底。

图片通道：
  Layer 1  Modal FLUX.1-schnell（FP8 量化，T4 本地，Apache-2.0 免费）
  Layer 2  SiliconFlow SDXL（Modal GPU 配额耗尽 / 本地 Flux 失败时兜底）

音频通道：
  Layer 1  Modal Kokoro-82M TTS（T4 本地，Apache-2.0 免费，朗读歌词）
  Layer 2  SoundHelix 免费背景音乐（Kokoro 失败时兜底）

并发控制：
  _flux_sem     最大并发 2（对齐 Modal @modal.concurrent(max_inputs=2)）
  _sf_sem       最大并发 2（SiliconFlow 生图）
  _kokoro_sem   最大并发 2（Kokoro TTS）

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

# 通道并发上限（对齐 Modal T4 max_inputs=2）
_MAX_FLUX_CONCURRENCY = 2
_MAX_SF_CONCURRENCY = 2
_MAX_KOKORO_CONCURRENCY = 2

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
    """MV 两层降级调度器：本地开源模型（Flux/Kokoro）优先，外部 API 兜底。"""

    def __init__(self) -> None:
        self._flux_sem = asyncio.Semaphore(_MAX_FLUX_CONCURRENCY)
        self._sf_sem = asyncio.Semaphore(_MAX_SF_CONCURRENCY)
        self._kokoro_sem = asyncio.Semaphore(_MAX_KOKORO_CONCURRENCY)
        self.flux_health = ChannelHealth("flux")
        self.sf_health = ChannelHealth("siliconflow")
        self.kokoro_health = ChannelHealth("kokoro")
        self.gpu_quota_exhausted = False
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15))

    # ------------------------------------------------------------------
    # 场景图片：Flux 本地 → SiliconFlow SDXL 兜底
    # ------------------------------------------------------------------

    async def generate_scene_image(
        self, scene: Dict[str, Any], style: str, storage,
        scene_idx: int = 0,
    ) -> Tuple[Optional[str], str]:
        """生成单个场景的静态图片（MV 图片序列素材）。

        返回 (image_url, channel)；channel ∈ {flux, siliconflow, none}。
        image_url 为本地 /uploads/... 路径；none 表示两层都失败，交给上层跳过分镜。
        """
        prompt = scene.get("image_prompt", "") or scene.get("description", "")
        if not prompt:
            return None, "none"

        # Layer 1: Modal FLUX.1-schnell 本地（免费，Apache-2.0）
        if self.flux_health.available:
            try:
                async with self._flux_sem:
                    url = await self._try_flux(prompt, style, storage, scene_idx)
                if url:
                    self.flux_health.record_success()
                    return url, "flux"
            except Exception as exc:
                if _is_gpu_quota_error(exc):
                    self.gpu_quota_exhausted = True
                    logger.warning("[MV] scene{} Flux GPU 配额耗尽，降级 SiliconFlow", scene_idx)
                else:
                    logger.warning("[MV] scene{} Flux 失败: {}", scene_idx, exc)
            self.flux_health.record_failure()

        # Layer 2: SiliconFlow SDXL 兜底（GPU 配额耗尽 / Flux 失败时）
        if self.sf_health.available:
            try:
                async with self._sf_sem:
                    url = await self._try_siliconflow(prompt, style, storage, scene_idx)
                if url:
                    self.sf_health.record_success()
                    return url, "siliconflow"
            except Exception as exc:
                logger.warning("[MV] scene{} SiliconFlow 失败: {}", scene_idx, exc)
            self.sf_health.record_failure()

        return None, "none"

    # ------------------------------------------------------------------
    # 音乐：Kokoro 本地 TTS → SoundHelix 免费 MP3
    # ------------------------------------------------------------------

    async def generate_music(
        self, lyrics: str, title: str, style: str, storage,
    ) -> Tuple[Optional[str], str]:
        """生成 MV 音频。返回 (audio_url, channel)；channel ∈ {kokoro, soundhelix, none}。

        首选 Kokoro-82M 朗读歌词（本地 TTS），失败时降级 SoundHelix 免费背景音乐。
        """
        text = _clean_lyrics_for_tts(lyrics) or f"{title}, {style} instrumental"

        # Layer 1: Modal Kokoro-82M TTS（本地，Apache-2.0）
        if self.kokoro_health.available:
            try:
                async with self._kokoro_sem:
                    from app.services.modal_gpu_client import kokoro_tts
                    wav_bytes = await kokoro_tts(text=text[:600])
                if wav_bytes:
                    self.kokoro_health.record_success()
                    url = storage.save_audio(wav_bytes, ext="wav")
                    logger.info("[MV] 音频经 Kokoro TTS 生成: {}", url)
                    return url, "kokoro"
            except Exception as exc:
                logger.warning("[MV] Kokoro TTS 失败: {}", exc)
            self.kokoro_health.record_failure()

        # Layer 2: SoundHelix 免费背景音乐
        try:
            resp = await self._http.get(_SOUNDHELIX_MP3_URL)
            if resp.status_code == 200 and resp.content:
                url = storage.save_audio(resp.content, ext="mp3")
                logger.info("[MV] 音频经 SoundHelix 兜底: {}", url)
                return url, "soundhelix"
        except Exception as exc:
            logger.warning("[MV] SoundHelix 下载失败: {}", exc)

        return None, "none"

    # ------------------------------------------------------------------
    # 内部：各通道实现
    # ------------------------------------------------------------------

    async def _try_flux(
        self, prompt: str, style: str, storage, scene_idx: int,
    ) -> Optional[str]:
        from app.services.modal_gpu_client import flux_image_generate
        jpg_bytes = await flux_image_generate(
            prompt=f"{style}, {prompt}",
            width=1024, height=576,
            seed=int(time.time()) % (2**31),
        )
        if not jpg_bytes:
            return None
        url = storage.save_cover(jpg_bytes, ext="jpg")
        logger.info("[MV] scene{} Flux 图片完成: {}", scene_idx, url)
        return url

    async def _try_siliconflow(
        self, prompt: str, style: str, storage, scene_idx: int,
    ) -> Optional[str]:
        from app.services.ai_scheduler import get_scheduler
        sched = get_scheduler()
        result = await sched.generate_image_sdxl(
            prompt=f"{style}, {prompt}",
            width=1024, height=576,
            num_images=1,
        )
        urls = (result.data or {}).get("image_urls") or []
        if not urls:
            return None
        logger.info("[MV] scene{} SiliconFlow SDXL 图片完成: {}", scene_idx, urls[0])
        return urls[0]


def _is_gpu_quota_error(exc: Exception) -> bool:
    """判断是否为 Modal GPU 配额耗尽类异常（免费额度用尽 / 并发占满）。"""
    from app.services.modal_gpu_client import GPUQuotaError
    if isinstance(exc, GPUQuotaError):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "quota" in msg or "quota" in name or "exhausted" in msg or "over concurrency" in msg


def _clean_lyrics_for_tts(lyrics: str) -> str:
    """去除 LRC 时间戳 / 章节标记，仅保留可朗读的歌词文本。"""
    import re
    lines = []
    for line in (lyrics or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("["):
            line = re.sub(r"^\[[^\]]*\]\s*", "", line).strip()
        if re.match(r"^(Title|LRC|Verse|Chorus|Bridge|Intro|Outro)\s*\d*:", line, flags=re.IGNORECASE):
            continue
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


_scheduler: Optional[MVScheduler] = None


def get_mv_scheduler() -> MVScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MVScheduler()
    return _scheduler
