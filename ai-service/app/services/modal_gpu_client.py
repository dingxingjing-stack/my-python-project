"""Modal GPU 函数客户端 — 从 web 容器调用 CogVideoX / MusicGen 生成函数。

通过 modal.Function.from_name 按名称调用同一 App 内的 GPU 函数（需 GPU 已部署）。
Modal SDK 在容器运行时自动注入；本地无 modal / GPU 未部署时优雅降级返回 None。
"""
from __future__ import annotations

import asyncio
from typing import Optional

_APP_NAME = "avireon-ai-music"
_lookup_cache = {}


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
