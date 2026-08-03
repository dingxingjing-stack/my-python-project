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
    # 主用模型（2026 年 8 月最新免费 ID）
    openrouter_long_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_code_model: str = "cohere/north-mini-code:free"
    openrouter_vision_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    openrouter_text_fallback: str = "nvidia/nemotron-3-nano-30b-a3b:free"

    # ── 硅基流动 SiliconFlow ──
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_text_model: str = "Qwen/Qwen2.5-7B-Instruct"
    siliconflow_code_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"

    # ── 服务商优先级 ──
    # PRIMARY_PROVIDER: TEXT/CODE 任务的主服务商。'siliconflow'(默认,本地) 或 'openrouter'。
    # 线上若 siliconflow key 无效/挂起，设 openrouter 直接走 OpenRouter 避免 45s 超时降级。
    primary_provider: str = "siliconflow"

    # ── Runway MV 生成 ──
    runway_api_key: str = ""
    runway_api_base: str = "https://api.dev.runwayml.com/v1"

    # ── 灰度管控 ──
    # FEATURE_STAGE 1/2/3，1=公测基础版默认关闭重型功能，3=全量放开
    feature_stage: int = 1

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

    # ── 分级限流：重型接口每日调用上限 ──
    daily_heavy_feature_calls: int = 3

    # ── 各服务商每日调用上限 ──
    daily_siliconflow_calls: int = 100
    daily_openrouter_calls: int = 200

    # ── OpenRouter 模型池（2026 年 8 月最新免费模型，8 组去重） ──
    # 来源: openrouter.ai/models?max-price=0
    # 注意: 每组内的模型不与其他组重复
    or_pool_long: str = (
        "nvidia/nemotron-3-ultra-550b-a55b:free,"
        "nvidia/nemotron-3-super-120b-a12b:free"
    )
    or_pool_chat: str = (
        "nvidia/nemotron-3-nano-30b-a3b:free,"
        "nvidia/nemotron-nano-9b-v2:free,"
        "google/gemma-4-26b-a4b-it:free,"
        "google/gemma-4-31b-it:free,"
        "inclusionai/ling-3.0-flash:free"
    )
    or_pool_code: str = "cohere/north-mini-code:free"
    or_pool_multimodal: str = (
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free,"
        "nvidia/nemotron-nano-12b-v2-vl:free"
    )
    or_pool_embedding: str = "nvidia/nemotron-3-embed-1b:free"
    or_pool_safety: str = "nvidia/nemotron-3.5-content-safety:free"
    or_pool_rerank: str = "nvidia/llama-nemotron-rerank-vl-1b-v2:free"
    or_pool_other: str = (
        "openai/gpt-oss-20b:free,"
        "poolside/laguna-s-2.1:free,"
        "poolside/laguna-xs-2.1:free"
    )

    @field_validator("allowed_origins")
    @classmethod
    def _strip_origins(cls, v: str) -> str:
        return ",".join([o.strip() for o in v.split(",") if o.strip()])

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    # ── 结构化模型池 ──
    @property
    def openrouter_model_pool(self) -> dict[str, list[str]]:
        return {
            "long": [m.strip() for m in self.or_pool_long.split(",") if m.strip()],
            "chat": [m.strip() for m in self.or_pool_chat.split(",") if m.strip()],
            "code": [m.strip() for m in self.or_pool_code.split(",") if m.strip()],
            "multimodal": [m.strip() for m in self.or_pool_multimodal.split(",") if m.strip()],
            "embedding": [m.strip() for m in self.or_pool_embedding.split(",") if m.strip()],
            "safety": [m.strip() for m in self.or_pool_safety.split(",") if m.strip()],
            "rerank": [m.strip() for m in self.or_pool_rerank.split(",") if m.strip()],
            "other": [m.strip() for m in self.or_pool_other.split(",") if m.strip()],
        }


def get_settings() -> Settings:
    return Settings()