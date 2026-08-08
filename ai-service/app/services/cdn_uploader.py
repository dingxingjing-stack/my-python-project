"""CDN 上传封装 — 自动选择 R2 / 本地存储。"""
from __future__ import annotations

import asyncio
import pathlib
from typing import Optional

from app.services.r2_uploader import get_uploader
from app.services.local_storage import get_local_storage


class CDNUploader:
    """上传文件到 CDN（优先 R2，未配置时回退本地存储）。"""

    def __init__(self):
        self._r2 = None
        self._local = get_local_storage()
        try:
            from app.services.r2_uploader import get_uploader
            self._r2 = get_uploader()
        except Exception as e:
            print(f"[CDNUploader] R2 不可用（走本地存储）: {e}")

    async def upload_audio(self, file_path: str) -> Optional[str]:
        """上传音频文件，返回可公开访问的 URL。"""
        path = pathlib.Path(file_path)
        data = path.read_bytes()
        ext = path.suffix.lstrip(".") or "mp3"

        # 优先 R2
        if self._r2 is not None:
            try:
                url = self._r2.upload_audio(data, ext=ext)
                if url:
                    return url
            except Exception:
                pass

        # 回退本地存储
        try:
            url = self._local.save_audio(data, ext=ext)
            return url
        except Exception as e:
            print(f"[CDNUploader] 本地存储失败: {e}")
            return None

    async def upload_cover(self, file_path: str) -> Optional[str]:
        path = pathlib.Path(file_path)
        data = path.read_bytes()
        ext = path.suffix.lstrip(".") or "jpg"
        try:
            url = self._r2.upload_cover(data, ext=ext)
            if url:
                return url
        except Exception:
            pass
        try:
            return self._local.save_cover(data, ext=ext)
        except Exception:
            return None

    async def upload_audio_batch(self, paths: list[str]) -> list[str | None]:
        """并发上传多个音频文件，最大并发 4。"""
        sem = asyncio.Semaphore(4)

        async def _one(p):
            async with sem:
                return await self.upload_audio(p)

        return await asyncio.gather(*[_one(p) for p in paths])

    async def upload_cover_batch(self, paths: list[str]) -> list[str | None]:
        """并发上传多个封面，最大并发 4。"""
        sem = asyncio.Semaphore(4)

        async def _one(p):
            async with sem:
                return await self.upload_cover(p)

        return await asyncio.gather(*[_one(p) for p in paths])
