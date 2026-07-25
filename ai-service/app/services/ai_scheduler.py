"""AI 统一调度中间件 — 硅基流动 + OpenRouter 双免费模型对接。

任务-模型自动匹配规则：
  text      → 硅基 Qwen/Qwen2.5-7B-Instruct       (歌词/文案/描述)
  code      → 硅基 Qwen/Qwen2.5-Coder-7B-Instruct  (分镜批量/逻辑编排)
  long      → OpenRouter nemotron-3-ultra:free      (超长上下文/深度推理)
  code_alt  → OpenRouter cohere/command-r-code:free (代码调试/提示词优化)
  vision    → OpenRouter nemotron-3-nano-omni:free  (图文识别/风格提示词)

三层架构：
  前端业务层 → ai_scheduler(本模块) → 第三方 API
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


# ---------------------------------------------------------------------------
# 任务类型枚举
# ---------------------------------------------------------------------------

class AITaskType(str, Enum):
    TEXT = "text"          # 歌词、文案、描述
    CODE = "code"          # 分镜批量生成、逻辑编排
    LONG = "long"          # 超长上下文、深度推理
    CODE_ALT = "code_alt"  # 代码调试、提示词优化
    VISION = "vision"      # 图文多模态


# ---------------------------------------------------------------------------
# 任务结果
# ---------------------------------------------------------------------------

@dataclass
class AIResult:
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    model_name: str = ""
    provider: str = ""
    tokens_used: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------

class AIScheduler:
    """统一 AI 调度中间件 — 自动匹配模型、限流、重试、扣费、日志。"""

    def __init__(self) -> None:
        s = get_settings()
        self._sf_key = s.siliconflow_api_key
        self._sf_base = s.siliconflow_base_url
        self._or_key = s.openrouter_api_key
        self._or_base = s.openrouter_base_url

        # 模型映射
        self._models = {
            AITaskType.TEXT: (s.siliconflow_text_model, "siliconflow"),
            AITaskType.CODE: (s.siliconflow_code_model, "siliconflow"),
            AITaskType.LONG: (s.openrouter_long_model, "openrouter"),
            AITaskType.CODE_ALT: (s.openrouter_code_model, "openrouter"),
            AITaskType.VISION: (s.openrouter_vision_model, "openrouter"),
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
    ) -> AIResult:
        """统一调度入口 — 幂等检查 → 状态机 → 限额检查 → API调用 → 记录用量。"""
        from app.services.task_state_machine import (
            create_task, transition, update_task_output, TaskStatus,
        )
        from app.services.usage_tracker import (
            check_daily_limits, record_usage, generate_task_id,
        )
        from app.services.idempotency import check_idempotency, register_idempotency

        model_name, provider = self._models.get(task_type, self._models[AITaskType.TEXT])
        result = AIResult(model_name=model_name, provider=provider)
        t0 = time.monotonic()

        if request_id:
            cached = await check_idempotency(request_id, user_id)
            if cached:
                logger.info("[Dispatch] Idempotent hit: request_id={}", request_id)
                return AIResult(text=cached.get("text", ""), model_name=model_name, provider=provider)

        action = credit_action or ""
        await check_daily_limits(user_id, action)

        tid = job_id or generate_task_id()
        await create_task(
            user_id=user_id, task_type=action or task_type.value, task_id=tid,
            request_id=request_id,
            input_data=json.dumps(messages, ensure_ascii=False)[:5000],
            model_name=model_name, provider=provider,
        )

        await transition(tid, TaskStatus.PROCESSING)

        try:
            sem = self._semaphores.get(provider, asyncio.Semaphore(5))
            async with sem:
                text = await self._call_with_retry(provider, model_name, messages, temperature, max_tokens)

            result.text = text
            result.elapsed_ms = int((time.monotonic() - t0) * 1000)

            await update_task_output(tid, text[:5000])
            await transition(tid, TaskStatus.COMPLETED)
            await record_usage(user_id, action, 0)

            if request_id:
                await register_idempotency(request_id, user_id, tid, {"text": text[:2000], "task_id": tid})

            await self._log_job(
                job_id=tid, task_type=task_type.value, model=model_name, provider=provider,
                input_data=json.dumps(messages, ensure_ascii=False)[:5000],
                output_data=text[:5000], credits_used=0,
                elapsed_ms=result.elapsed_ms, status="completed",
            )

        except HTTPException:
            await transition(tid, TaskStatus.FAILED, error="HTTP error")
            raise
        except Exception as exc:
            result.elapsed_ms = int((time.monotonic() - t0) * 1000)
            await transition(tid, TaskStatus.FAILED, error=str(exc)[:500])
            await self._log_job(
                job_id=tid, task_type=task_type.value, model=model_name, provider=provider,
                input_data=json.dumps(messages, ensure_ascii=False)[:5000],
                output_data="", credits_used=0, elapsed_ms=result.elapsed_ms,
                status="failed", error=str(exc)[:500],
            )
            raise

        return result

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
        """歌词生成 — 使用硅基 Qwen2.5-7B-Instruct。"""
        system = """You are a professional songwriter. Write emotionally engaging, singable lyrics.
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
[00:00.00] first line
[00:05.00] second line
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
        """用代码模型解析歌词，生成音频生成专用提示词。"""
        system = "You are a music production assistant. Given lyrics and style, generate a detailed prompt for AI music generation (Suno-compatible). Output ONLY the prompt text, max 200 words."
        user_msg = f"Title: {title}\nStyle: {style}\n\nLyrics:\n{lyrics}"

        return await self.dispatch(
            AITaskType.CODE,
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.5,
            max_tokens=500,
            credit_action=None,  # 不单独扣费
            user_id=user_id,
        )

    async def generate_cover_prompt(
        self,
        lyrics: str,
        title: str,
        style: str = "Cinematic",
        user_id: int = 1,
    ) -> AIResult:
        """用视觉模型生成封面绘图提示词。"""
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
        """MV 全流程第一步：生成完整故事剧本 + 分镜。"""
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
            max_tokens=4000,
            credit_action="mv",
            user_id=user_id,
        )

    async def generate_scene_image(
        self,
        image_prompt: str,
        style: str = "Cinematic",
        user_id: int = 1,
    ) -> AIResult:
        """为单个分镜生成图片描述（实际图片生成需要图像模型）。"""
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
        """生成分享卡片文案。"""
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
        """用 Nemotron 生成配器描述 + 伴奏参数，供本地开源工具合成。"""
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
        """使用硅基流动 SDXL 模型生成图片。
        
        返回 AIResult，data 中包含 image_urls 列表。
        """
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

        # 保存图片到本地存储
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
        """写入 generation_jobs 日志表。"""
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
