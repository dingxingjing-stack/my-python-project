"""Pydantic request / response schemas for all AI endpoints."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.PENDING


class TaskStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = 0
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Music generation (Suno)
# ---------------------------------------------------------------------------


class GenerateMusicRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    style: Optional[str] = Field(
        default=None, description="Genre hint: pop, rock, electronic, etc."
    )
    title: Optional[str] = None
    lyrics: Optional[str] = Field(
        default=None,
        description="Custom lyrics; if omitted, Suno auto-generates.",
    )
    instrumental: bool = False
    model: str = "v5.5"


class GeneratedTrack(BaseModel):
    audio_url: str
    cover_url: Optional[str] = None
    lyrics: Optional[str] = None
    title: str
    duration_ms: Optional[int] = None


class GenerateMusicResult(BaseModel):
    tracks: List[GeneratedTrack]


# ---------------------------------------------------------------------------
# Lyrics generation (DeepSeek / OpenAI)
# ---------------------------------------------------------------------------


class LyricsRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    style: str = "pop"
    language: str = "zh"
    structure: str = "verse-chorus-verse-chorus-bridge-chorus"
    provider: str = Field(
        default="deepseek",
        description="deepseek | openai | nvidia | glm",
    )


class LyricsResult(BaseModel):
    lyrics: str
    lrc: str
    provider: str


# ---------------------------------------------------------------------------
# Voice cloning (GPT-SoVITS)
# ---------------------------------------------------------------------------


class CloneVoiceRequest(BaseModel):
    sample_url: str = Field(..., description="R2 URL of 5-60s voice sample")
    text: str = Field(..., min_length=1, max_length=2000)
    language: str = Field(
        default="zh", description="zh | en | ja | ko | yue"
    )
    speed: float = 1.0
    top_k: int = 15
    top_p: float = 0.9
    temperature: float = 0.9


class CloneVoiceResult(BaseModel):
    audio_url: str
    duration_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# MV generation (Runway)
# ---------------------------------------------------------------------------


class GenerateMVRequest(BaseModel):
    audio_url: str = Field(..., description="R2 URL of the track audio")
    prompt: str = Field(..., min_length=1, max_length=1000)
    style: Optional[str] = None
    resolution: str = "1080p"
    segment_seconds: int = Field(default=10, ge=3, le=15)


class GenerateMVResult(BaseModel):
    video_url: str
    segments: int
