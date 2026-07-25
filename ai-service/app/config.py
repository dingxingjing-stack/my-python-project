from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_host: str = "0.0.0.0"
    service_port: int = 8000

    internal_api_token: str = Field(default="change-me")
    allowed_origins: str = "http://localhost:8000"

    # ── OpenRouter ──
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_long_model: str = "nvidia/nemotron-3-ultra:free"
    openrouter_code_model: str = "cohere/command-r-code:free"
    openrouter_vision_model: str = "nvidia/nemotron-3-nano-omni:free"

    # ── 硅基流动 SiliconFlow ──
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_text_model: str = "Qwen/Qwen2.5-7B-Instruct"
    siliconflow_code_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"

    # ── Runway MV 生成 ──
    runway_api_key: str = ""
    runway_api_base: str = "https://api.runwayml.com/v1"

    # ── 本地 SoVITS 声音克隆引擎 ──
    sovits_enabled: bool = False
    sovits_api_base: str = "http://127.0.0.1:9880"
    sovits_default_language: str = "zh"

    # ── 本地存储（默认回退） ──
    local_uploads_dir: str = "data/uploads"
    local_base_url: str = "http://localhost:8000"

    # ── 免费额度管控 ──
    daily_free_credits: int = 10
    daily_max_ai_calls: int = 10
    daily_global_max_calls: int = 200
    daily_mv_slots: int = 3
    task_timeout_minutes: int = 30

    @field_validator("allowed_origins")
    @classmethod
    def _strip_origins(cls, v: str) -> str:
        return ",".join([o.strip() for o in v.split(",") if o.strip()])

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


def get_settings() -> Settings:
    return Settings()
