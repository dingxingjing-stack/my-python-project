"""
AI 统一调度中间件 — SiliconFlow + OpenRouter 双服务商，带降级链。

降级策略：
  TEXT  → 硅基 Qwen2.5-7B-Instruct → OpenRouter nemotron-3-nano-30b-a3b:free
  CODE  → 硅基 Qwen2.5-Coder-7B-Instruct → OpenRouter cohere/north-mini-code:free
  LONG  → OpenRouter nemotron-3-ultra:free  （仅 OR，无降级）
  CODE_ALT → OpenRouter cohere/north-mini-code:free（仅 OR，无降级）
  VISION   → OpenRouter nemotron-3-nano-omni:free（仅 OR，无降级）

服务商独立限额：
  - 每次调用前检查对应服务商日配额（daily_siliconflow_calls / daily_openrouter_calls）
  - 调用成功后记录 provider 用量
  - 超出配额后抛出 429
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings
from app.database import get_db


class AITaskType(str, Enum):
    TEXT = "text"
    CODE = "code"
    LONG = "long"
    CODE_ALT = "code_alt"
    VISION = "vision"


@dataclass
class AIResult:
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    model_name: str = ""
    provider: str = ""
    fallback_used: bool = False
    tokens_used: int = 0
    elapsed_ms: int = 0


class QuotaExceededError(Exception):
    pass


class AllProvidersFailedError(Exception):
    pass


class AIScheduler:
    """
    统一 AI 调度中间件 — 自动匹配模型、限流、重试、降级、扣费、日志。

    每个 TaskType 对应一组 (primary_provider, primary_model, fallback_provider, fallback_model)。
    dispatch() 优先调用 primary；失败后自动降级到 fallback（若配置）。
    每次调用前检查对应服务商的日配额，超出后抛出 429。
    """

    def __init__(self) -> None:
        s = get_settings()
        self._sf_key = s.siliconflow_api_key
        self._sf_base = s.siliconflow_base_url
        self._or_key = s.openrouter_api_key
        self._or_base = s.openrouter_base_url

        # 任务 → 路由映射，含降级
        self._task_map: dict[AITaskType, dict] = {
            AITaskType.TEXT: {
                "primary": ("siliconflow", s.siliconflow_text_model),
                "fallback": ("openrouter", s.openrouter_text_fallback),
                "credit_action": "text",
            },
            AITaskType.CODE: {
                "primary": ("siliconflow", s.siliconflow_code_model),
                "fallback": ("openrouter", s.openrouter_code_model),
                "credit_action": "code",
            },
            AITaskType.LONG: {
                "primary": ("openrouter", s.openrouter_long_model),
                "fallback": None,
                "credit_action": "long",
            },
            AITaskType.CODE_ALT: {
                "primary": ("openrouter", s.openrouter_code_model),
                "fallback": None,
                "credit_action": "code_alt",
            },
            AITaskType.VISION: {
                "primary": ("openrouter", s.openrouter_vision_model),
                "fallback": None,
                "credit_action": "vision",
            },
        }

        # 限流: 每个 provider 最多 5 并发
        self._semaphores: dict[str, asyncio.Semaphore] = {
            "siliconflow": asyncio.Semaphore(5),
            "openrouter": asyncio.Semaphore(5),
        }

    # ------------------------------------------------------------------
    # 核心调度方法
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        task_type: AITaskType,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        credit_action: Optional[str] = None,
        user_id: int = 1,
        job_id: Optional[str] = None,
        request_id: Optional[str] = None,
        disable_fallback: bool = False,
    ) -> AIResult:
        """
        统一调度入口 — 自动降级、幂等检查、状态机、限额检查、双 Provider 限流。

        参数:
          disable_fallback: True 时只尝试 primary，不降级。
        """
        from app.services.task_state_machine import (
            create_task, transition, update_task_output, TaskStatus,
        )
        from app.services.usage_tracker import (
            check_daily_limits, record_usage, generate_task_id,
            check_provider_daily_limits, record_provider_usage,
        )
        from app.services.idempotency import check_idempotency, register_idempotency

        route = self._task_map.get(task_type, self._task_map[AITaskType.TEXT])
        action = credit_action or route.get("credit_action", "")

        result = AIResult()
        t0 = time.monotonic()

        # 幂等检查
        if request_id:
            cached = await check_idempotency(request_id, user_id)
            if cached:
                logger.info("[Dispatch] Idempotent hit: request_id={}", request_id)
                return AIResult(text=cached.get("text", ""), model_name=route["primary"][1], provider=route["primary"][0])

        # 用户每日总限额
        await check_daily_limits(user_id, action)

        # 生成任务 ID
        tid = job_id or generate_task_id()
        await create_task(
            user_id=user_id, task_type=action or task_type.value, task_id=tid,
            request_id=request_id,
            input_data=json.dumps(messages, ensure_ascii=False)[:5000],
            model_name=route["primary"][1], provider=route["primary"][0],
        )
        await transition(tid, TaskStatus.PROCESSING)

        # 主 + 降级调用链
        text, model_name, provider, fallback_used = await self._call_chain(
            route=route,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            disable_fallback=disable_fallback,
        )

        result.text = text
        result.model_name = model_name
        result.provider = provider
        result.fallback_used = fallback_used
        result.elapsed_ms = int((time.monotonic() - t0) * 1000)

        # 写入结果
        await update_task_output(tid, text[:5000])
        await transition(tid, TaskStatus.COMPLETED)
        await record_usage(user_id, action, 0)

        # 记录该 provider 的调用次数
        await record_provider_usage(provider)

        if request_id:
            await register_idempotency(request_id, user_id, tid, {"text": text[:2000], "task_id": tid})

        await self._log_job(
            job_id=tid, task_type=task_type.value, model=model_name, provider=provider,
            input_data=json.dumps(messages, ensure_ascii=False)[:5000],
            output_data=text[:5000], credits_used=0,
            elapsed_ms=result.elapsed_ms, status="completed",
        )

        return result

    async def _call_chain(
        self,
        route: dict,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        disable_fallback: bool = False,
    ) -> tuple[str, str, str, bool]:
        """
        按降级链依次尝试: primary → fallback。
        返回 (text, model_name, provider, fallback_used)。
        """
        from app.services.usage_tracker import check_provider_daily_limits

        attempts = [("primary", route["primary"])]
        if not disable_fallback and route.get("fallback"):
            attempts.append(("fallback", route["fallback"]))

        last_exception: Optional[Exception] = None

        for stage, (provider, model) in attempts:
            # 检查该服务商当日配额
            try:
                await check_provider_daily_limits(provider)
            except HTTPException as exc:
                logger.warning("[{}] {} 配额不足: {}", stage, provider, exc.detail)
                last_exception = exc
                continue

            try:
                sem = self._semaphores.get(provider, asyncio.Semaphore(5))
                async with sem:
                    text = await self._call_with_retry(provider, model, messages, temperature, max_tokens)
                logger.info("[{}] {} 调用成功, model={}", stage, provider, model)
                fallback_used = stage == "fallback"
                return text, model, provider, fallback_used
            except Exception as exc:
                logger.warning("[{}] {} 调用失败: {} -> {}", stage, provider, type(exc).__name__, exc)
                last_exception = exc
                continue

        # 全部失败
        err_msg = f"所有引擎均不可用: {last_exception}" if last_exception else "无可用引擎"
        logger.error("[_call_chain] {}", err_msg)
        raise AllProvidersFailedError(err_msg)

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    async def generate_lyrics(
        self,
        prompt: str,
        style: str = "pop",
        language: str = "zh",
        mood: str = "",
        vocal: str = "auto",
        user_id: int = 1,
    ) -> AIResult:
        lang_map = {
            "zh": "Chinese (简体中文)",
            "en": "English",
            "ja": "Japanese (日本語)",
            "ko": "Korean (한국어)",
            "es": "Spanish (Español)",
        }
        lang_name = lang_map.get(language, language or "English")
        system = f"""You are a professional songwriter. Write emotionally engaging, singable lyrics.
Lyrics language: {lang_name}. All lyrics must be in this language.
Always return in this format:

Title: <song title>

Verse 1:
<lyrics>

Chorus:
<lyrics>

Verse 2:
<lyrics>

Bridge:
<lyrics>

Chorus:
<lyrics>

Then also provide LRC format:

LRC:
[00:00.00] first lyric line
[00:05.00] second lyric line
..."""
        user_msg = (
            f"Topic: {prompt}\n"
            f"Genre: {style}\n"
            f"Language: {language}\n"
        )
        if mood:
            user_msg += f"Mood: {mood}\n"
        if vocal and vocal != "auto":
            user_msg += f"Vocal: {vocal}\n"

        return await self.dispatch(
            AITaskType.TEXT,
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.8,
            max_tokens=3000,
            credit_action="lyrics",
            user_id=user_id,
        )

    async def generate_music_prompt(
        self,
        lyrics: str,
        style: str = "pop",
        title: str = "",
        user_id: int = 1,
    ) -> AIResult:
        system = "You are a music production assistant. Given lyrics and style, generate a detailed prompt for AI music generation (Suno-compatible). Output ONLY the prompt text, max 200 words."
        user_msg = f"Title: {title}\nStyle: {style}\n\nLyrics:\n{lyrics}"

        return await self.dispatch(
            AITaskType.CODE,
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.5,
            max_tokens=500,
            credit_action=None,
            user_id=user_id,
        )

    async def generate_cover_prompt(
        self,
        lyrics: str,
        title: str,
        style: str = "Cinematic",
        user_id: int = 1,
    ) -> AIResult:
        system = "You are a visual artist. Given a song title and lyrics, generate a detailed image generation prompt for creating album cover art. Style: {style}. Output ONLY the image prompt, max 100 words."
        user_msg = f"Song: {title}\nStyle: {style}\n\nLyrics excerpt:\n{lyrics[:500]}"

        return await self.dispatch(
            AITaskType.VISION,
            [{"role": "system", "content": system.format(style=style)}, {"role": "user", "content": user_msg}],
            temperature=0.7,
            max_tokens=300,
            credit_action=None,
            user_id=user_id,
        )

    async def generate_mv_storyboard(
        self,
        lyrics: str,
        title: str,
        mv_style: str = "Cinematic",
        num_scenes: int = 4,
        user_id: int = 1,
    ) -> AIResult:
        system = f"""You are a music video director. Given song lyrics, create a complete MV storyboard.
Output exactly {num_scenes} scenes. For each scene provide:
1. Scene number
2. Visual description (what we see)
3. Camera movement
4. Mood/lighting
5. Image generation prompt (for AI image generation, detailed, cinematic)

Format as JSON array:
[
  {{
    "scene": 1,
    "description": "...",
    "camera": "...",
    "mood": "...",
    "image_prompt": "..."
  }},
  ...
]

MV Style: {mv_style}"""

        user_msg = f"Song: {title}\n\nLyrics:\n{lyrics}"

        return await self.dispatch(
            AITaskType.CODE,
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.8,
            max_tokens=800,
            credit_action="mv",
            user_id=user_id,
        )

    async def generate_scene_image(
        self,
        image_prompt: str,
        style: str = "Cinematic",
        user_id: int = 1,
    ) -> AIResult:
        system = "You are an AI image prompt enhancer. Take the given image prompt and enhance it for high-quality image generation. Keep it under 150 words. Output ONLY the enhanced prompt."
        user_msg = f"Style: {style}\n\nOriginal prompt: {image_prompt}"

        return await self.dispatch(
            AITaskType.VISION,
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.6,
            max_tokens=300,
            credit_action=None,
            user_id=user_id,
        )

    async def generate_share_text(
        self,
        title: str,
        style: str,
        lyrics_snippet: str,
        user_id: int = 1,
    ) -> AIResult:
        system = "You are a social media copywriter. Write a short, engaging share text for an AI-generated song. Max 50 words. Include a call to action."
        user_msg = f"Song: {title}\nStyle: {style}\nLyrics: {lyrics_snippet[:200]}"

        return await self.dispatch(
            AITaskType.TEXT,
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.9,
            max_tokens=150,
            credit_action="share_text",
            user_id=user_id,
        )

    async def generate_accompaniment(
        self,
        lyrics: str,
        style: str = "pop",
        bpm: int = 120,
        instruments: str = "",
        user_id: int = 1,
    ) -> AIResult:
        system = """You are a music producer. Given lyrics and style, output a standardized music production spec.
Use this exact JSON format:
{
  "title": "Song Title",
  "bpm": 120,
  "key": "C major",
  "genre": "pop",
  "instruments": ["piano", "drums", "bass", "synth"],
  "structure": "verse-chorus-verse-chorus-bridge-chorus",
  "mood": "uplifting",
  "production_notes": "Start with soft piano intro, build with drums at chorus"
}
Output ONLY valid JSON, no markdown."""
        user_msg = f"Title: {''}\nGenre: {style}\nBPM: {bpm}\nInstruments: {instruments or 'auto'}\n\nLyrics:\n{lyrics[:1000]}"

        return await self.dispatch(
            AITaskType.CODE,
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.5,
            max_tokens=800,
            credit_action="music",
            user_id=user_id,
        )

    async def generate_image_sdxl(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        num_images: int = 1,
        user_id: int = 1,
    ) -> AIResult:
        if not self._sf_key:
            raise RuntimeError("SiliconFlow API key 未配置")

        from app.services.local_storage import get_local_storage
        import uuid
        import base64

        model = "stabilityai/stable-diffusion-xl-base-1.0"
        t0 = time.monotonic()
        result = AIResult(model_name=model, provider="siliconflow")

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self._sf_base}/v1/image/generations",
                headers={
                    "Authorization": f"Bearer {self._sf_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "prompt": prompt,
                    "negative_prompt": negative_prompt or None,
                    "image_size": f"{width}x{height}",
                    "batch_size": num_images,
                    "seed": int(uuid.uuid4().int % (2**31)),
                },
            )
            resp.raise_for_status()
            data = resp.json()

        result.elapsed_ms = int((time.monotonic() - t0) * 1000)
        result.text = f"Generated {num_images} image(s) via SDXL"

        storage = get_local_storage()
        image_urls = []
        for img_data in data.get("data", []):
            if "url" in img_data:
                async with httpx.AsyncClient() as http:
                    img_resp = await http.get(img_data["url"], timeout=60)
                    img_resp.raise_for_status()
                    url = storage.save_cover(img_resp.content, ext="jpg")
                    image_urls.append(url)
            elif "b64_json" in img_data:
                img_bytes = base64.b64decode(img_data["b64_json"])
                url = storage.save_cover(img_bytes, ext="jpg")
                image_urls.append(url)

        result.data = {"image_urls": image_urls, "prompt": prompt}
        return result

    # ------------------------------------------------------------------
    # 内部：API 调用（带重试）
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def _call_with_retry(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        if provider == "siliconflow":
            return await self._call_siliconflow(model, messages, temperature, max_tokens)
        elif provider == "openrouter":
            return await self._call_openrouter(model, messages, temperature, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    async def _call_siliconflow(
        self, model: str, messages: list[dict], temperature: float, max_tokens: int,
    ) -> str:
        if not self._sf_key:
            raise RuntimeError("SiliconFlow API key 未配置 (SILICONFLOW_API_KEY)")

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self._sf_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._sf_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""

    async def _call_openrouter(
        self, model: str, messages: list[dict], temperature: float, max_tokens: int,
    ) -> str:
        if not self._or_key:
            raise RuntimeError("OpenRouter API key 未配置 (OPENROUTER_API_KEY)")

        print(f"[openrouter] model={model} max_tokens={max_tokens} temp={temperature}")
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{self._or_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._or_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:8000",
                    "X-Title": "Avireon",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            if resp.status_code != 200:
                print(f"[openrouter ERROR] status={resp.status_code} model={model} body={resp.text[:500]}")
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""

    # ------------------------------------------------------------------
    # 内部：任务日志
    # ------------------------------------------------------------------

    async def _log_job(
        self,
        job_id: Optional[str],
        task_type: str,
        model: str,
        provider: str,
        input_data: str,
        output_data: str,
        credits_used: int,
        elapsed_ms: int,
        status: str,
        error: str = "",
    ) -> None:
        try:
            db = await get_db()
            await db.execute(
                """
                INSERT INTO generation_jobs
                    (job_id, user_id, task_type, model_name, provider,
                     input_prompt, ai_response, credits_used,
                     elapsed_ms, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id or "", 1, task_type, model, provider,
                    input_data, output_data, credits_used,
                    elapsed_ms, status, error,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Failed to log generation_job: {}", exc)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_scheduler: AIScheduler | None = None


def get_scheduler() -> AIScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AIScheduler()
    return _scheduler
