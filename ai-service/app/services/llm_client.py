"""通用 LLM 客户端 — 工厂模式 + 多源路由 + 指数退避重试。

路由策略（二选一）：

1. RELAY 模式（推荐）：
   配置 relay_api_base + relay_api_key 后，ALL providers
   共用同一中转地址，仅 model 名不同。

2. 直连模式（relay 为空时自动启用）：
   每个 provider 独立 base_url + api_key，互不共享。

所有 public 方法带 @retry（指数退避 2s→4s→8s）。
"""
from __future__ import annotations

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
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=8),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


class LLMClient:
    """统一 LLM 接口 — relay 优先，直连备用。"""

    def __init__(self) -> None:
        s = get_settings()
        self._clients: dict[str, AsyncOpenAI] = {}
        self._model_map: dict[str, str] = {}

        # ── RELAY 模式：所有 provider 共用中转 ──
        if s.relay_api_key:
            logger.info("RELAY 模式：所有 provider 共用 {}", s.relay_api_base)
            for name, model in [
                ("deepseek", s.deepseek_model),
                ("nvidia",   s.nvidia_model),
                ("glm",      s.glm_model),
                ("openai",   s.openai_model),
            ]:
                if not model:
                    continue
                self._clients[name] = AsyncOpenAI(
                    base_url=s.relay_api_base,
                    api_key=s.relay_api_key,
                )
                self._model_map[name] = model
                logger.info("  {} → model={}", name, model)
            if not self._clients:
                logger.warning("RELAY 已配置但所有 model 名为空")

        # ── 直连模式：各 provider 独立 ──
        else:
            logger.info("直连模式：各 provider 独立配置")
            for name, key, base, model in [
                ("deepseek", s.deepseek_api_key, s.deepseek_base_url, s.deepseek_model),
                ("nvidia",   s.nvidia_api_key,   s.nvidia_base_url,   s.nvidia_model),
                ("glm",      s.glm_api_key,      s.glm_base_url,      s.glm_model),
                ("openai",   s.openai_api_key,   s.openai_base_url,   s.openai_model),
            ]:
                if not key or not model:
                    continue
                self._clients[name] = AsyncOpenAI(base_url=base, api_key=key)
                self._model_map[name] = model
                logger.info("  {} → base={} model={}", name, base, model)

    # ------------------------------------------------------------------
    # 通用对话
    # ------------------------------------------------------------------

    @retry(**_RETRY_ARGS)
    async def chat(
        self,
        messages: list[dict[str, str]],
        provider: str = "deepseek",
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
        resp = await client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # 流式对话
    # ------------------------------------------------------------------

    @retry(**_RETRY_ARGS)
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        provider: str = "deepseek",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        client, model = self._resolve(provider)
        logger.debug("LLM stream | provider={} model={}", provider, model)
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
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
        provider: str = "deepseek",
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
        provider: str = "deepseek",
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
