"""
AI 音乐生成路由

降级链：Mureka -> HF (Hugging Face MusicGen) -> Mock
通过环境变量 HF_FALLBACK (默认 true) 控制是否启用 HF 兜底。
MOCK_FALLBACK (默认 true) 控制最终是否返回示例音频。
"""

import os
import uuid
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.services.mureka_service import mureka_service, MurekaSongRequest, QuotaExceededError
from app.services.agnes_music_service import agnes_service, AgnesSongRequest

router = APIRouter(prefix="/ai", tags=["ai-music"])

HF_FALLBACK_ENABLED = os.getenv("HF_FALLBACK", "true").lower() in ("1", "true", "yes")
MOCK_FALLBACK_ENABLED = os.getenv("MOCK_FALLBACK", "true").lower() in ("1", "true", "yes")

_http_client: Optional[httpx.AsyncClient] = None
_cdn_uploader: Optional["CDNUploader"] = None


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=120.0)
    return _http_client


async def _get_cdn_uploader():
    global _cdn_uploader
    if _cdn_uploader is None:
        from app.services.cdn_uploader import CDNUploader
        _cdn_uploader = CDNUploader()
    return _cdn_uploader


async def _try_hf_fallback(prompt: str, duration: Optional[int]) -> Optional[str]:
    if not HF_FALLBACK_ENABLED:
        print("[HF 兜底] HF_FALLBACK 未启用，跳过")
        return None

    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if not hf_token:
        print("[HF 兜底] 未配置 HF_TOKEN / HUGGINGFACE_TOKEN，跳过")
        return None

    tmp_path = None
    try:
        api_url = "https://api-inference.huggingface.co/models/facebook/musicgen-large"
        headers = {"Authorization": f"Bearer {hf_token}"}
        payload = {"inputs": prompt}
        if duration:
            payload["parameters"] = {"max_new_tokens": int(duration * 50)}

        client = await _get_http_client()
        response = await client.post(api_url, headers=headers, json=payload)

        if response.status_code != 200:
            truncated = response.text[:200]
            print(f"[HF 兜底] API 错误 {response.status_code}: {truncated}")
            return None

        audio_data = response.content
        if not audio_data:
            print("[HF 兜底] API 返回空数据")
            return None

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_data)
            tmp.flush()
            tmp_path = tmp.name

        uploader = await _get_cdn_uploader()
        cdn_url = await uploader.upload_audio(tmp_path)
        if cdn_url:
            return cdn_url
        print("[HF 兜底] CDN 上传失败")
        return None

    except Exception as e:
        print(f"[HF 兜底] 异常: {type(e).__name__}: {e}")
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


class GenerateRequest(BaseModel):
    prompt: str
    style: str = "pop"
    duration: Optional[int] = None
    type: str = "song"


class GenerateResponse(BaseModel):
    success: bool
    audio_url: Optional[str] = None
    error: Optional[str] = None
    task_id: Optional[str] = None
    ai_provider: Optional[str] = None
    agnes_debug: Optional[str] = None


@router.post("/generate", response_model=GenerateResponse)
async def generate_music(request: GenerateRequest):
    try:
        prompt = request.prompt.strip()
        if len(prompt) < 5:
            raise HTTPException(status_code=400, detail="提示词至少需要 5 个字符")

        task_suffix = uuid.uuid4().hex[:6]

        # ── 1. Agnes 优化提示词 + 生成歌词 ──
        print(f"[generate] 第 1 层: Agnes 优化提示词...")
        agnes_request = AgnesSongRequest(
            prompt=prompt,
            style=request.style,
            duration=request.duration or 180,
            type=request.type,
        )
        agnes_result = await agnes_service.generate_song(agnes_request)

        ai_provider = "agnes" if agnes_result.optimized_prompt and agnes_result.optimized_prompt != prompt else "gemini"
        agnes_debug = (
            f"success={agnes_result.success}, "
            f"opt_changed={'yes' if agnes_result.optimized_prompt != prompt else 'no'}, "
            f"error={agnes_result.error}, "
            f"key_set={bool(agnes_service.API_KEY)}"
        )

        final_prompt = agnes_result.optimized_prompt or prompt
        if agnes_result.generated_lyrics:
            final_prompt = agnes_result.generated_lyrics

        # ── 2. Mureka 生成音频 ──
        print(f"[generate] 第 2 层: Mureka 生成音频...")
        mureka_request = MurekaSongRequest(
            lyrics=final_prompt,
            style=request.style,
            duration=request.duration,
        )
        try:
            mureka_result = await mureka_service.generate_song(mureka_request)
            if mureka_result.success:
                return GenerateResponse(
                    success=True,
                    audio_url=mureka_result.audio_url,
                    task_id=mureka_result.task_id,
                    ai_provider=f"{ai_provider}+mureka",
                    agnes_debug=agnes_debug,
                )
        except QuotaExceededError:
            print("[降级] Mureka 配额耗尽 → HF")
        except Exception as e:
            print(f"[降级] Mureka 异常: {e} → HF")

        # ── 3. HF 兜底 ──
        print(f"[generate] 第 3 层: HF MusicGen 兜底...")
        hf_audio = await _try_hf_fallback(prompt=final_prompt, duration=request.duration)
        if hf_audio:
            return GenerateResponse(
                success=True,
                audio_url=hf_audio,
                task_id=f"hf-{hash(final_prompt) & 0xffffff:06x}-{task_suffix}",
                ai_provider=f"{ai_provider}+hf",
                agnes_debug=agnes_debug,
            )

        # ── 4. Mock 兜底 ──
        if not MOCK_FALLBACK_ENABLED:
            print("[generate] 所有引擎失败，MOCK_FALLBACK 未启用，返回错误")
            return GenerateResponse(
                success=False,
                error="所有 AI 引擎均不可用，且 MOCK 已被关闭",
                ai_provider="none",
                agnes_debug=agnes_debug,
            )

        print(f"[generate] 第 4 层: Mock 示例音频兜底")
        import random
        mock_urls = [
            "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
            "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
            "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        ]
        return GenerateResponse(
            success=True,
            audio_url=random.choice(mock_urls),
            task_id=f"mock-{hash(prompt) & 0xffffff:06x}-{task_suffix}",
            ai_provider=f"{ai_provider}+mock",
            agnes_debug=agnes_debug,
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[generate 未捕获] {type(e).__name__}: {e}")
        traceback.print_exc()
        return GenerateResponse(
            success=False,
            error=f"{type(e).__name__}: {e}",
            ai_provider="error",
            agnes_debug="",
        )


@router.get("/styles")
async def list_styles():
    return {
        "styles": [
            {"value": "pop", "label": "流行", "description": "主流流行音乐"},
            {"value": "rock", "label": "摇滚", "description": "摇滚乐"},
            {"value": "electronic", "label": "电子", "description": "电子音乐"},
            {"value": "hip-hop", "label": "嘻哈", "description": "嘻哈/说唱"},
            {"value": "r&b", "label": "R&B", "description": "节奏布鲁斯"},
            {"value": "jazz", "label": "爵士", "description": "爵士乐"},
            {"value": "classical", "label": "古典", "description": "古典音乐"},
            {"value": "ambient", "label": "氛围", "description": "氛围音乐"},
            {"value": "cinematic", "label": "电影配乐", "description": "电影原声"},
            {"value": "lo-fi", "label": "Lo-Fi", "description": "低保真音乐"},
        ]
    }
