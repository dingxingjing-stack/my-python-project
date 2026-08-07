"""OpenAI 兼容 ↔ OpenCode 原生 API 翻译网关。

背景:
    `opencode serve` 原生只暴露 OpenCode 自身 API（/session、/session/:id/message、/global/health），
    并不提供 OpenAI 兼容的 /v1/chat/completions。而业务侧 _call_local_gateway
    (app/services/ai_scheduler.py) 按 OpenAI 协议向 127.0.0.1:4096/v1/chat/completions 发请求。

本模块是一个轻量翻译代理，监听 127.0.0.1:4096，把 OpenAI 格式的 chat.completions 请求
翻译成 OpenCode 原生会话调用，并把 OpenCode 的文本回包拼回 OpenAI 响应，从而让业务层
无感知地使用容器内/本机的 OpenCode 本地网关。

       OpenAI 客户端 (FastAPI 业务进程)
              │  POST /v1/chat/completions    (OpenAI 协议)
              ▼
       ┌── 本网关 (127.0.0.1:4096) ──────────────────────────┐
       │  1. POST /session            → 创建会话,得到 session_id │
       │  2. POST /session/{id}/message → 发消息, 取回答        │
       └──────────────────────────────────────────────────────┘
              │  OpenCode 原生 API
              ▼
       opencode serve (127.0.0.1:4098)

环境变量:
    OPENCODE_BACKEND_URL   opencode serve 的原生地址，默认 http://127.0.0.1:4098
    GATEWAY_PORT           本网关监听端口，默认 4096
    GATEWAY_HOST           监听地址，默认 127.0.0.1
    OPENCODE_MODEL         默认透传模型，默认 opencode/free
"""
from __future__ import annotations

import os
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

BACKEND_URL = os.getenv("OPENCODE_BACKEND_URL", "http://127.0.0.1:4098").rstrip("/")
GATEWAY_MODEL = os.getenv("OPENCODE_MODEL", "opencode/free")


class ChatCompletionRequest(BaseModel):
    model: str = GATEWAY_MODEL
    messages: List[dict] = []
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


def _split_messages(messages: List[dict]):
    """把 OpenAI 消息拆成 (system_texts, user_parts, agent)。"""
    system_texts: List[str] = []
    user_parts: List[dict] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_texts.append(str(content))
        elif role == "user":
            user_parts.append({"type": "text", "text": str(content)})
        elif role == "assistant":
            # 历史助手回复只作参考，直接以文本形式回填（OpenCode 会话按对话轮次交换）
            user_parts.append({"type": "text", "text": f"[assistant]: {str(content)}"})
    return "\n".join(system_texts).strip(), user_parts


def build_app() -> FastAPI:
    app = FastAPI(title="opencode-openai-gateway", version="1.0.0")

    @app.get("/v1/models")
    async def models():
        return {
            "object": "list",
            "data": [{"id": GATEWAY_MODEL, "object": "model", "owned_by": "opencode-gateway"}],
        }

    @app.get("/health")
    async def health():
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{BACKEND_URL}/global/health")
                ok = r.status_code == 200
                return {"status": "ok" if ok else "deg", "backend": BACKEND_URL, "gateway_model": GATEWAY_MODEL}
        except Exception:
            return {"status": "degraded", "backend": BACKEND_URL}

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest):
        if not req.messages:
            raise HTTPException(400, "messages required")
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
            # 1) 建会话
            try:
                sresp = await client.post(
                    f"{BACKEND_URL}/session",
                    json={"title": "gateway"},
                )
                sresp.raise_for_status()
                session_id = sresp.json()["id"]
            except Exception as exc:
                raise HTTPException(502, f"opencode create session failed: {type(exc).__name__}: {exc}") from exc

            # 2) 发消息
            system, user_parts = _split_messages(req.messages)
            body: dict = {
                "model": req.model or GATEWAY_MODEL,
                "parts": user_parts,
            }
            if system:
                body["system"] = system
            try:
                mresp = await client.post(
                    f"{BACKEND_URL}/session/{session_id}/message",
                    json=body,
                )
                mresp.raise_for_status()
                data = mresp.json()
            except Exception as exc:
                raise HTTPException(502, f"opencode message failed: {type(exc).__name__}: {exc}") from exc

        content = _extract_reply(data)
        if not content:
            raise HTTPException(502, "opencode returned empty text")

        return {
            "id": f"chatcmpl-{session_id}",
            "object": "chat.completion",
            "created": int(__import__("time").time()),
            "model": req.model or GATEWAY_MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    return app


def _extract_reply(data: dict) -> str:
    """从 OpenCode message 响应中拼出文本回复。

    OpenCode 返回形如 {"info": {...}, "parts": [{...}]} 或直接为消息对象。
    文本 part 为 {"type": "text", "text": "..."}；reasoning/step 等非文本 part 忽略。
    """
    chunks: List[str] = []
    _saw_text = False
    if isinstance(data, dict):
        for key in ("parts", "steps"):
            parts = data.get(key)
            if isinstance(parts, list):
                for p in parts:
                    if not isinstance(p, dict):
                        if isinstance(p, str):
                            chunks.append(p)
                        continue
                    ptype = p.get("type")
                    text = p.get("text")
                    if ptype in ("text", "system") and text:
                        chunks.append(str(text))
                        _saw_text = True
                    elif isinstance(text, list):  # 某些实现把 text 作为数组
                        for t in text:
                            if isinstance(t, dict) and t.get("type") == "text":
                                chunks.append(str(t.get("text", "")))
                                _saw_text = True
    if not _saw_text:
        # 兜底：整个对象字符串化，避免返回空导致上层判定失败
        import json
        return json.dumps(data, ensure_ascii=False) if not chunks else "\n".join(chunks)
    return "\n".join(chunks)


_runner = None


def run() -> None:
    """启动网关（供 subprocess 调用）。"""
    import os
    import uvicorn

    port = int(os.getenv("GATEWAY_PORT", "4096"))
    host = os.getenv("GATEWAY_HOST", "127.0.0.1")
    uvicorn.run(build_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()