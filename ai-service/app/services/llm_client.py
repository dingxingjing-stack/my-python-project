"""通用 LLM 客户端 — 工厂模式 + 硅基流动/OpenRouter 双路路由 + 指数退避重试。

路由策略：
  siliconflow → OpenAI 兼容客户端，使用 SILICONFLOW_API_KEY
  openrouter  → OpenAI 兼容客户端，使用 OPENROUTER_API_KEY

所有 public 方法带 @retry（指数退避 2s→4s→8s）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Optional

from loguru import logger
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import httpx

from app.config import get_settings
from app.models.schemas import LyricsRequest


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class LLMError(Exception):
    ...

class LLMNotConfiguredError(LLMError):
    ...

class LLMResponseError(LLMError):
    ...


# ---------------------------------------------------------------------------
# 默认歌词 system prompt
# ---------------------------------------------------------------------------

_LYRICS_SYSTEM_PROMPT = """You are a professional songwriter.
Write emotionally engaging, singable lyrics that fit the requested
musical style. Always return BOTH:
1) Plain lyrics text (every line on its own row)
2) LRC formatted lyrics with rolling timestamps

Reply in this exact format, no markdown:

LYRICS:
<plain lyrics here, one line per row>

LRC:
[mm:ss.xx] first line
[mm:ss.xx] second line
..."""


# ---------------------------------------------------------------------------
# 退避重试
# ---------------------------------------------------------------------------

_RETRY_ARGS = dict(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=2, min=2, max=8),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)

_PROVIDER_MAP: list[tuple[str, str, str, str]] = []


def _build_provider_map() -> list[tuple[str, str, str, str]]:
    """懒构建 provider 映射表，确保每次都读最新环境变量。"""
    s = get_settings()
    providers = []
    if s.siliconflow_api_key:
        providers.append(("siliconflow", s.siliconflow_api_key, s.siliconflow_base_url, s.siliconflow_text_model))
    if s.openrouter_api_key:
        providers.append(("openrouter", s.openrouter_api_key, s.openrouter_base_url, s.openrouter_text_fallback))
    return providers


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

class LLMClient:
    """统一 LLM 接口 — 硅基流动优先，OpenRouter 备用。"""

    def __init__(self) -> None:
        self._clients: dict[str, AsyncOpenAI] = {}
        self._model_map: dict[str, str] = {}

        for name, key, base, model in _build_provider_map():
            if not key or not model:
                continue
            self._clients[name] = AsyncOpenAI(
                base_url=base.rstrip("/") + "/v1",
                api_key=key,
                timeout=httpx.Timeout(60, connect=15),
            )
            self._model_map[name] = model
            logger.info("LLMClient: {} → base={} model={}", name, base, model)

        if not self._clients:
            logger.warning("LLMClient 无可用 provider (SILICONFLOW_API_KEY 或 OPENROUTER_API_KEY 均未配置)")

    # ------------------------------------------------------------------
    # 通用对话
    # ------------------------------------------------------------------

    @retry(**_RETRY_ARGS)
    async def chat(
        self,
        messages: list[dict[str, str]],
        provider: str = "siliconflow",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        response_format: Optional[dict[str, str]] = None,
    ) -> str:
        client, model = self._resolve(provider)
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format:
            kwargs["response_format"] = response_format
        logger.debug("LLM chat | provider={} model={}", provider, model)
        resp = await asyncio.wait_for(
            client.chat.completions.create(**kwargs),
            timeout=45,
        )
        return resp.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # 流式对话
    # ------------------------------------------------------------------

    @retry(**_RETRY_ARGS)
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        provider: str = "siliconflow",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        client, model = self._resolve(provider)
        logger.debug("LLM stream | provider={} model={}", provider, model)
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ),
            timeout=45,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    # ------------------------------------------------------------------
    # 结构化输出
    # ------------------------------------------------------------------

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        provider: str = "siliconflow",
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        text = await self.chat(
            messages=messages,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("LLM 返回非 JSON:\n{}", text)
            raise LLMResponseError(f"模型返回了非 JSON 内容：{exc}") from exc

    # ------------------------------------------------------------------
    # 简版补全
    # ------------------------------------------------------------------

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        provider: str = "siliconflow",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return await self.chat(messages, provider, temperature, max_tokens)

    # ------------------------------------------------------------------
    # 歌词生成（向后兼容）
    # ------------------------------------------------------------------

    async def generate(self, req: LyricsRequest) -> tuple[str, str, str]:
        user = (
            f"主题/方向：{req.prompt}\n"
            f"风格：{req.style}\n"
            f"语言：{req.language}\n"
            f"结构：{req.structure}\n"
        )

        provider = req.provider
        if provider not in self._clients:
            fallback = self.available_providers()
            if not fallback:
                raise LLMNotConfiguredError("没有任何 LLM 提供商已配置")
            provider = fallback[0]
            logger.info(
                "provider '{}' 未配置，fallback 到 '{}'",
                req.provider, provider,
            )

        text = await self.complete(
            system_prompt=_LYRICS_SYSTEM_PROMPT,
            user_prompt=user,
            provider=provider,
        )
        lyrics, lrc = self._parse(text)
        return lyrics, lrc, provider

    # ------------------------------------------------------------------
    # 工具辅助
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        import re
        cjk = len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", text))
        words = len(re.findall(r"[a-zA-Z0-9_]+", text))
        other = len(text) - cjk - sum(len(w) for w in re.findall(r"[a-zA-Z0-9_]+", text))
        return int(cjk * 1.5 + words * 1.3 + other * 0.25)

    def available_providers(self) -> list[str]:
        return list(self._clients.keys())

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _resolve(self, provider: str) -> tuple[AsyncOpenAI, str]:
        client = self._clients.get(provider)
        if client is None:
            avail = self.available_providers()
            raise LLMNotConfiguredError(
                f"provider='{provider}' 未配置。"
                f" 已配置: {avail or '无'}"
            )
        return client, self._model_map[provider]

    @staticmethod
    def _parse(reply: str) -> tuple[str, str]:
        lines = reply.strip().splitlines()
        lyrics_buf, lrc_buf = [], []
        mode = None
        for line in lines:
            stripped = line.strip()
            if stripped.upper() == "LYRICS:":
                mode = "lyrics"
                continue
            if stripped.upper() == "LRC:":
                mode = "lrc"
                continue
            if not stripped:
                continue
            if mode == "lyrics":
                lyrics_buf.append(stripped)
            elif mode == "lrc":
                lrc_buf.append(stripped)
        if not lyrics_buf and not lrc_buf:
            return reply.strip(), ""
        return "\n".join(lyrics_buf), "\n".join(lrc_buf)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

def get_llm_client() -> LLMClient:
    return LLMClient()

get_lyrics_client = get_llm_client
