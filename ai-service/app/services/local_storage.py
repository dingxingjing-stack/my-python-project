"""本地文件存储服务 — 当 R2 未配置时自动回退到本地存储。

生成的音频/视频/封面保存到 ai-service/data/uploads/ 目录，
通过 FastAPI StaticFiles 以 /uploads/ 路径提供访问。
"""
from __future__ import annotations

import os
import pathlib
import uuid
from datetime import datetime
from typing import Optional

from loguru import logger


class LocalStorage:
    """将 AI 生成的文件保存到本地磁盘。"""

    def __init__(self, base_dir: str | None = None) -> None:
        if base_dir is None:
            root = pathlib.Path(__file__).resolve().parent.parent  # ai-service/
            base_dir = str(root / "data" / "uploads")
        self._base = pathlib.Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        # 创建子目录
        for sub in ("audio", "covers", "videos"):
            (self._base / sub).mkdir(exist_ok=True)
        logger.info("LocalStorage 初始化: {}", self._base)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_audio(self, data: bytes, ext: str = "mp3") -> str:
        """保存音频字节，返回可访问的 URL 路径。"""
        return self._save("audio", data, ext)

    def save_cover(self, data: bytes, ext: str = "jpg") -> str:
        """保存封面图片字节，返回可访问的 URL 路径。"""
        return self._save("covers", data, ext)

    def save_video(self, data: bytes, ext: str = "mp4") -> str:
        """保存视频字节，返回可访问的 URL 路径。"""
        return self._save("videos", data, ext)

    def save_from_path(self, category: str, src_path: str) -> str:
        """将已存在的文件移动到存储目录。"""
        import shutil
        ext = pathlib.Path(src_path).suffix.lstrip(".") or "bin"
        dest = self._build_path(category, ext)
        shutil.copy2(src_path, str(dest))
        return self._to_url(category, dest.name)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _save(self, category: str, data: bytes, ext: str) -> str:
        dest = self._build_path(category, ext)
        dest.write_bytes(data)
        logger.info("保存文件 {}/{} ({} bytes)", category, dest.name, len(data))
        return self._to_url(category, dest.name)

    def _build_path(self, category: str, ext: str) -> pathlib.Path:
        ts = datetime.utcnow().strftime("%Y%m%d")
        uid = uuid.uuid4().hex[:12]
        filename = f"{ts}_{uid}.{ext}"
        return self._base / category / filename

    @staticmethod
    def _to_url(category: str, filename: str) -> str:
        return f"/uploads/{category}/{filename}"


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_storage: Optional[LocalStorage] = None


def get_local_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage
