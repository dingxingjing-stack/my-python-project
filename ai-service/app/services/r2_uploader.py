"""Cloudflare R2 (S3-compatible) uploader for AI outputs."""
from __future__ import annotations

import io
import uuid
from datetime import datetime
from typing import Optional

import boto3
from botocore.client import BaseClient
from loguru import logger

from app.config import get_settings


class R2Uploader:
    """Upload AI generation results to R2.

    R2 is fully S3-compatible, so we use boto3 with a custom endpoint.
    """

    def __init__(self) -> None:
        s = get_settings()
        if not s.r2_endpoint:
            logger.warning("R2 endpoint not configured; uploads will be disabled")
            self._client: Optional[BaseClient] = None
            return

        self._public_base = s.r2_public_base_url.rstrip("/")
        self._client = boto3.client(
            service_name="s3",
            endpoint_url=s.r2_endpoint,
            aws_access_key_id=s.r2_access_key_id,
            aws_secret_access_key=s.r2_secret_access_key,
            region_name="auto",
        )
        self._bucket_audio = s.r2_bucket_audio
        self._bucket_covers = s.r2_bucket_covers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_audio(
        self, data: bytes, ext: str = "mp3", prefix: str = "ai-generated"
    ) -> str:
        """Upload audio bytes; return public URL."""
        key = self._build_key(prefix, ext)
        self._put(self._bucket_audio, key, data, content_type=f"audio/{ext}")
        return self._public_url(self._bucket_audio, key)

    def upload_cover(self, data: bytes, ext: str = "jpg") -> str:
        key = self._build_key("ai-covers", ext)
        self._put(self._bucket_covers, key, data, content_type=f"image/{ext}")
        return self._public_url(self._bucket_covers, key)

    def upload_video(self, data: bytes, ext: str = "mp4") -> str:
        key = self._build_key("ai-mv", ext)
        self._put(self._bucket_audio, key, data, content_type=f"video/{ext}")
        return self._public_url(self._bucket_audio, key)

    def upload_fileobj(
        self,
        data: bytes,
        bucket: str,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        self._put(bucket, key, data, content_type=content_type)
        return self._public_url(bucket, key)

    def presign(self, key: str, expires: int = 3600, bucket: str | None = None) -> str:
        """Generate a presigned GET URL for an existing object."""
        if self._client is None:
            raise RuntimeError("R2 uploader is not configured")
        b = bucket or self._bucket_audio
        url = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": b, "Key": key},
            ExpiresIn=expires,
        )
        return url

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _put(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> None:
        if self._client is None:
            raise RuntimeError("R2 uploader is not configured")
        self._client.upload_fileobj(
            io.BytesIO(data),
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        logger.info("Uploaded s3://{}/{} ({} bytes)", bucket, key, len(data))

    def _public_url(self, bucket: str, key: str) -> str:
        return f"{self._public_base}/{key}"

    @staticmethod
    def _build_key(prefix: str, ext: str) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d")
        uid = uuid.uuid4().hex
        return f"{prefix}/{ts}/{uid}.{ext}"


_uploader: Optional[R2Uploader] = None


def get_uploader() -> R2Uploader:
    global _uploader
    if _uploader is None:
        _uploader = R2Uploader()
    return _uploader
