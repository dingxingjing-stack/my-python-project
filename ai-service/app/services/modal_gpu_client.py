"""Modal GPU 函数客户端 — 从 web 容器调用 CogVideoX / MusicGen / Flux / Kokoro 生成函数。

通过 modal.Function.from_name 按名称调用同一 App 内的 GPU 函数（需 GPU 已部署）。
Modal SDK 在容器运行时自动注入；本地无 modal / GPU 未部署时优雅降级返回 None。
"""
from __future__ import annotations

import asyncio
from typing import Optional

_APP_NAME = "avireon-ai-music"
_lookup_cache = {}


class GPUQuotaError(RuntimeError):
    """Modal GPU 配额耗尽（免费额度用尽 / 并发占满），调用方应降级到 SiliconFlow 等备用通道。"""


def _is_quota_exception(exc: Exception) -> bool:
    """判断 Modal 调用异常是否为 GPU 配额耗尽 / 并发超限（免费额度或限流）。"""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return (
        "quota" in msg
        or "quota" in name
        or "exhausted" in msg
        or "too many concurrent" in msg
        or "429" in msg
        or "over concurrency" in msg
        or "concurrent input" in msg
    )


def _get_function(name: str):
    if name not in _lookup_cache:
        import modal
        _lookup_cache[name] = modal.Function.from_name(_APP_NAME, name)
    return _lookup_cache[name]


async def cogvideo_generate(
    prompt: str,
    num_frames: int = 49,
    steps: int = 30,
    timeout: float = 1500.0,
) -> Optional[bytes]:
    """调用 Modal CogVideoX 生成视频，返回 mp4 字节；失败返回 None。"""
    try:
        fn = _get_function("cogvideo_generate")
        return await asyncio.wait_for(
            fn.remote.aio(prompt=prompt, num_frames=num_frames, steps=steps),
            timeout,
        )
    except Exception as exc:
        print(f"[modal-gpu] cogvideo_generate 失败: {type(exc).__name__}: {exc}", flush=True)
        return None


async def musicgen_generate(
    prompt: str,
    max_new_tokens: int = 512,
    timeout: float = 900.0,
) -> Optional[bytes]:
    """调用 Modal MusicGen-small 生成音乐，返回 wav 字节；失败返回 None。"""
    try:
        fn = _get_function("musicgen_generate")
        return await asyncio.wait_for(
            fn.remote.aio(prompt=prompt, max_new_tokens=max_new_tokens),
            timeout,
        )
    except Exception as exc:
        print(f"[modal-gpu] musicgen_generate 失败: {type(exc).__name__}: {exc}", flush=True)
        return None


async def flux_image_generate(
    prompt: str,
    width: int = 1024,
    height: int = 576,
    seed: int = 0,
    timeout: float = 900.0,
) -> Optional[bytes]:
    """调用 Modal FLUX.1-schnell 本地文生图，返回 jpg 字节；失败返回 None。"""
    try:
        fn = _get_function("flux_image_generate")
        return await asyncio.wait_for(
            fn.remote.aio(prompt=prompt, width=width, height=height, seed=seed),
            timeout,
        )
    except Exception as exc:
        if _is_quota_exception(exc):
            raise GPUQuotaError(str(exc)[:200]) from exc
        print(f"[modal-gpu] flux_image_generate 失败: {type(exc).__name__}: {exc}", flush=True)
        return None


async def kokoro_tts(
    text: str,
    voice: str = "",
    speed: float = 1.0,
    timeout: float = 600.0,
) -> Optional[bytes]:
    """调用 Modal Kokoro-82M 本地 TTS，返回 wav 字节；失败返回 None。"""
    try:
        fn = _get_function("kokoro_tts")
        return await asyncio.wait_for(
            fn.remote.aio(text=text, voice=voice, speed=speed),
            timeout,
        )
    except Exception as exc:
        if _is_quota_exception(exc):
            raise GPUQuotaError(str(exc)[:200]) from exc
        print(f"[modal-gpu] kokoro_tts 失败: {type(exc).__name__}: {exc}", flush=True)
        return None


async def heartmula_generate(
    lyrics: str,
    tags: str = "",
    language: str = "pt",
    duration: int = 60,
    timeout: float = 2700.0,
) -> Optional[dict]:
    """调用 Modal HeartMuLa 3B 本地生成音乐，返回 dict{mp3字节+元数据}；失败返回 None。"""
    try:
        fn = _get_function("heartmula_generate")
        return await asyncio.wait_for(
            fn.remote.aio(lyrics=lyrics, tags=tags, language=language, duration=duration),
            timeout,
        )
    except Exception as exc:
        if _is_quota_exception(exc):
            raise GPUQuotaError(str(exc)[:200]) from exc
        print(f"[modal-gpu] heartmula_generate 失败: {type(exc).__name__}: {exc}", flush=True)
        return None
