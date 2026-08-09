"""
AI 音乐生成路由

降级链：Agnes (提示词优化) → Mureka (音频) → HF MusicGen → Mock
环境变量：
  HF_FALLBACK   默认 true，控制是否启用 HuggingFace 兜底
  MOCK_FALLBACK 默认 true，控制最终是否返回示例音频（生产可关闭）
"""
from __future__ import annotations

import os
import time
import uuid
import random
import asyncio
import json
import pathlib
from typing import Optional, Dict, Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.mureka_service import mureka_service, MurekaSongRequest, QuotaExceededError
from app.services.agnes_music_service import agnes_service, AgnesSongRequest
from app.services.feature_flags import require_feature

router = APIRouter(prefix="/ai", tags=["ai-music"])

HF_FALLBACK_ENABLED = os.getenv("HF_FALLBACK", "true").lower() in ("1", "true", "yes")
MOCK_FALLBACK_ENABLED = os.getenv("MOCK_FALLBACK", "true").lower() in ("1", "true", "yes")


def _build_heartmula_tags(style: str, vocal_word: str = "Portuguese") -> str:
    """按风格生成 HeartMuLa 音乐标签（tags 词条带 <tag> 前缀由模型侧补）。
    vocal_word 来自 Language Registry（如 Portuguese/Chinese），不再硬编码单一语言。"""
    style_word = (style or "pop").lower().replace("-", " ").replace("&", "and")
    return f"show <{style_word}>, {style_word}, {vocal_word}, female vocal, song, romantic, 96 bpm"

# ── 全局单例 ──
_http_client: Optional[httpx.AsyncClient] = None

# ── 简易内存任务存储（用于前端轮询） ──
_job_store: Dict[str, Dict[str, Any]] = {}

# HeartMuLa 进程级并发信号量：Modal max_inputs=1，串行化避免并发触发 concurrency 异常被误判为配额降级
_heartmula_sem = asyncio.Semaphore(1)

def _create_job(result: Dict[str, Any]) -> str:
    """创建任务并返回 job_id"""
    job_id = uuid.uuid4().hex[:8]
    _job_store[job_id] = {
        "status": "completed",
        "result": result,
        "progress": 100,
    }
    return job_id

def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """获取任务状态"""
    return _job_store.get(job_id)


# ── 跨容器 job 状态文件持久化（共享卷，与 mv.py 同款模式） ──
def _music_status_dir() -> pathlib.Path:
    """跨容器状态目录 = data/music_jobs（共享卷上）。"""
    from app.services.local_storage import get_local_storage
    base = get_local_storage()._base  # .../data/uploads
    d = base.parent / "music_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_music_job_status(job_id: str, payload: dict) -> None:
    """将任务状态写成 JSON 文件（共享卷）。整文件写入/替换，跨容器一致。"""
    try:
        p = _music_status_dir() / f"{job_id}.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as exc:
        print(f"[generate] 状态文件写失败 job={job_id}: {exc}", flush=True)


def _read_music_job_status_file(job_id: str) -> Optional[dict]:
    """读取共享卷上的任务状态文件（跨容器轮询主通道）。"""
    try:
        p = _music_status_dir() / f"{job_id}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

# ── 全局单例 ──
_http_client: Optional[httpx.AsyncClient] = None
_cdn_uploader: Optional["CDNUploader"] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=120.0)
    return _http_client


def _get_cdn_uploader():
    global _cdn_uploader
    if _cdn_uploader is None:
        from app.services.cdn_uploader import CDNUploader
        _cdn_uploader = CDNUploader()
    return _cdn_uploader


async def _try_hf_fallback(prompt: str, duration: Optional[int]) -> Optional[str]:
    """
    尝试调用 Hugging Face Inference API 的 facebook/musicgen-large 模型生成音频。

    返回生成的音频 CDN URL，失败时返回 None。
    """
    if not HF_FALLBACK_ENABLED:
        print("[generate] 第 3 层: HF_FALLBACK 未启用，跳过")
        return None

    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if not hf_token:
        print("[generate] 第 3 层: 未配置 HF_TOKEN / HUGGINGFACE_TOKEN，跳过")
        return None

    tmp_path = None
    try:
        api_url = "https://api-inference.huggingface.co/models/facebook/musicgen-large"
        headers = {"Authorization": f"Bearer {hf_token}"}
        payload: dict = {"inputs": prompt}
        if duration:
            payload["parameters"] = {"max_new_tokens": int(duration * 50)}

        client = _get_http_client()
        response = await client.post(api_url, headers=headers, json=payload)

        if response.status_code != 200:
            truncated = response.text[:200]
            print(f"[generate] 第 3 层: HF API 错误 {response.status_code}: {truncated}")
            return None

        audio_data = response.content
        if not audio_data:
            print("[generate] 第 3 层: HF API 返回空数据")
            return None

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_data)
            tmp.flush()
            tmp_path = tmp.name

        uploader = _get_cdn_uploader()
        cdn_url = uploader.upload_audio(tmp_path)
        if cdn_url:
            return cdn_url
        print("[generate] 第 3 层: HF CDN 上传失败")
        return None

    except Exception as e:
        print(f"[generate] 第 3 层: HF 异常 {type(e).__name__}: {e}")
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
    title: Optional[str] = None
    lyrics: Optional[str] = None
    language: Optional[str] = None
    instrumental: bool = False


class GenerateResponse(BaseModel):
    success: bool
    audio_url: Optional[str] = None
    error: Optional[str] = None
    task_id: Optional[str] = None
    ai_provider: Optional[str] = None
    agnes_debug: Optional[str] = None


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str = "queued"


@router.post("/generate")
@require_feature("ai_music")
async def generate_music(request: GenerateRequest):
    # 0) 每日额度检查（与 lyrics 等 AI 调用一致），超限直接 429
    from app.services.usage_tracker import check_daily_limits
    await check_daily_limits(user_id=1, action="ai_music")

    # 1) 先创建任务，返回 job_id 供前端轮询
    job_id = uuid.uuid4().hex[:8]
    _job_store[job_id] = {"status": "queued", "progress": 0, "result": None, "error": None}
    _write_music_job_status(job_id, _job_store[job_id])
    
    # 后台异步跑生成，避免阻塞返回
    asyncio.create_task(_run_generation(job_id, request))
    
    return {"job_id": job_id}

async def _run_generation(job_id: str, request: GenerateRequest):
    """后台任务：真正跑生成流程，完成后写入 _job_store"""
    t_start = time.time()
    try:
        # 更新状态
        _job_store[job_id]["status"] = "processing"
        _job_store[job_id]["progress"] = 10
        _write_music_job_status(job_id, _job_store[job_id])
        
        prompt = request.prompt.strip()
        if len(prompt) < 5:
            raise HTTPException(status_code=400, detail="提示词至少需要 5 个字符")

        task_suffix = uuid.uuid4().hex[:6]

        # ── 第 1 层：Agnes 优化提示词 / 生成歌词 ──
        print("[generate] 第 1 层: Agnes 优化提示词...")
        _job_store[job_id]["progress"] = 12
        agnes_request = AgnesSongRequest(
            prompt=request.prompt.strip(),
            style=request.style,
            duration=request.duration or 180,
            type=request.type,
        )
        agnes_result = await agnes_service.generate_song(agnes_request)
        _job_store[job_id]["progress"] = 20

        ai_provider = "agnes" if agnes_result.optimized_prompt and agnes_result.optimized_prompt != request.prompt.strip() else "gemini"
        agnes_debug = (
            f"success={agnes_result.success}, "
            f"opt_changed={'yes' if agnes_result.optimized_prompt != request.prompt.strip() else 'no'}, "
            f"error={agnes_result.error}, "
            f"key_set={bool(agnes_service.API_KEY)}"
        )

        final_prompt = (agnes_result.optimized_prompt or request.prompt.strip()).strip()
        if agnes_result.generated_lyrics:
            final_prompt = agnes_result.generated_lyrics.strip()

        _job_store[job_id]["progress"] = 35

        # ── 语言分流：Registry 判定的 HeartMuLa 通道（当前仅 pt verified） ──
        # resolve_music_provider() 只对 music_verified=True 的语言返回 "heartmula"，
        # 未实测语言（zh/en/es/ja/ko）保持 external，不强制进入本地生成。
        from app.services.language_registry import resolve_music_provider, get as get_lang_cap

        language = (request.language or "").lower()
        lang_cap = get_lang_cap(language)
        if resolve_music_provider(language) == "heartmula":
            print(f"[generate] Language={language} → HeartMuLa 本地生成...")
            _job_store[job_id]["progress"] = 40
            try:
                from app.services.modal_gpu_client import heartmula_generate

                hm_lyrics = (request.lyrics or final_prompt or request.prompt).strip()
                hm_tags = _build_heartmula_tags(request.style, lang_cap.heartmula_vocal_word)
                hm_duration = min(request.duration or 60, 120)
                # 进程级串行化：HeartMuLa Modal max_inputs=1，避免并发触发 concurrency 异常被误判为配额降级
                async with _heartmula_sem:
                    hm = await heartmula_generate(
                        lyrics=hm_lyrics,
                        tags=hm_tags,
                        language=language,
                        duration=hm_duration,
                    )
                if hm and hm.get("mp3"):
                    uploader = _get_cdn_uploader()
                    import tempfile
                    import pathlib as _pathlib

                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                        tf.write(hm["mp3"])
                        tf.flush()
                        tmp_path = tf.name
                    try:
                        audio_url = await uploader.upload_audio(tmp_path)
                    finally:
                        try:
                            _pathlib.Path(tmp_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                    if audio_url:
                        hm_meta = {k: v for k, v in hm.items() if k != "mp3"}
                        result = {
                            "success": True,
                            "audio_url": audio_url,
                            "task_id": f"heartmula-{uuid.uuid4().hex[:6]}",
                            "ai_provider": f"{ai_provider}+heartmula",
                            "agnes_debug": agnes_debug,
                            "hm_detail": hm_meta,
                        }
                        _job_store[job_id] = {"status": "completed", "progress": 100, "result": result, "error": None}
                        _write_music_job_status(job_id, _job_store[job_id])
                        return
                    print("[generate] HeartMuLa CDN 上传失败 → 降级到第 2 层")
                else:
                    print("[generate] HeartMuLa 返回空 → 降级到第 2 层")
            except Exception as e:
                print(f"[generate] HeartMuLa 异常 {type(e).__name__}: {e} → 降级到第 2 层")

        # ── 第 2 层：Mureka 生成音频 ──
        print("[generate] 第 2 层: Mureka 生成音频...")
        mureka_duration = min(request.duration or 60, 300)
        mureka_request = MurekaSongRequest(
            lyrics=final_prompt,
            style=request.style,
            duration=mureka_duration,
        )
        _job_store[job_id]["progress"] = 45
        try:
            mureka_result = await mureka_service.generate_song(mureka_request)
            if mureka_result.success:
                audio_url = mureka_result.audio_url
                # 回源：拉取远端音频 → 上传本地 CDN，保证 URL 稳定；失败回退原 URL
                mp3_bytes = await mureka_service.fetch_audio(mureka_result.audio_url)
                if mp3_bytes:
                    uploader = _get_cdn_uploader()
                    import pathlib as _pathlib

                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                        tf.write(mp3_bytes)
                        tf.flush()
                        tmp_path = tf.name
                    try:
                        local_url = await uploader.upload_audio(tmp_path)
                    finally:
                        try:
                            _pathlib.Path(tmp_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                    if local_url:
                        audio_url = local_url
                        print(f"[generate] 第 2 层 Mureka 音频已回源上传: {local_url}")
                result = {
                    "success": True,
                    "audio_url": audio_url,
                    "task_id": mureka_result.task_id,
                    "ai_provider": f"{ai_provider}+mureka",
                    "agnes_debug": agnes_debug,
                }
                _job_store[job_id] = {"status": "completed", "progress": 100, "result": result, "error": None}
                _write_music_job_status(job_id, _job_store[job_id])
                return
        except QuotaExceededError:
            print("[generate] 第 2 层失败: Mureka 配额耗尽 → 降级到第 3 层 HF")
        except Exception as e:
            print(f"[generate] 第 2 层失败: Mureka 异常 {type(e).__name__}: {e} → 降级到第 3 层 HF")

        # ── 第 3 层：HF MusicGen 兜底 ──
        print("[generate] 第 3 层: HF MusicGen 兜底...")
        _job_store[job_id]["progress"] = 65
        hf_audio = await _try_hf_fallback(prompt=final_prompt, duration=request.duration)
        _job_store[job_id]["progress"] = 80
        if hf_audio:
            result = {
                "success": True,
                "audio_url": hf_audio,
                "task_id": f"hf-{hash(final_prompt) & 0xffffff:06x}-{uuid.uuid4().hex[:6]}",
                "ai_provider": f"{ai_provider}+hf",
                "agnes_debug": agnes_debug,
            }
            _job_store[job_id] = {"status": "completed", "progress": 100, "result": result, "error": None}
            _write_music_job_status(job_id, _job_store[job_id])
            return

        # 第 4 层：Mock 示例音频兜底
        if not MOCK_FALLBACK_ENABLED:
            _job_store[job_id] = {"status": "failed", "progress": 100, "result": None, "error": "所有 AI 引擎均不可用，且 MOCK 已被关闭"}
            _write_music_job_status(job_id, _job_store[job_id])
            return

        _job_store[job_id]["progress"] = 90

        mock_urls = [
            "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
            "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
            "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        ]
        result = {
            "success": True,
            "audio_url": random.choice(mock_urls),
            "task_id": f"mock-{hash(prompt) & 0xffffff:06x}-{uuid.uuid4().hex[:6]}",
            "ai_provider": f"{ai_provider}+mock",
            "agnes_debug": agnes_debug,
        }
        _job_store[job_id] = {"status": "completed", "progress": 100, "result": result, "error": None}
        _write_music_job_status(job_id, _job_store[job_id])

    except HTTPException:
        _job_store[job_id] = {"status": "failed", "progress": 100, "result": None, "error": "HTTP Exception"}
        _write_music_job_status(job_id, _job_store[job_id])
    except Exception as e:
        import traceback
        print(f"[generate 未捕获] {type(e).__name__}: {e}")
        traceback.print_exc()
        _job_store[job_id] = {"status": "failed", "progress": 100, "result": None, "error": f"{type(e).__name__}: {e}"}
        _write_music_job_status(job_id, _job_store[job_id])
    finally:
        dur_ms = int((time.time() - t_start) * 1000)
        fin = _job_store[job_id]
        fin["finished_at"] = time.time()
        fin["duration_ms"] = dur_ms
        print(f"[generate] job={job_id} status={fin.get('status')} elapsed={dur_ms}ms", flush=True)


@router.get("/job/{job_id}")
async def get_job_status(job_id: str):
    """查询生成任务状态 — 前端轮询用。

    优先级：内存 _job_store（快速路径）→ 共享卷状态文件（跨容器兜底）→ task_state_machine。
    """
    # 1. 优先查 _job_store（music.py 内部写入的实际位置）
    job = _job_store.get(job_id)
    if job:
        return {
            "job_id": job_id,
            "status": job.get("status", "processing"),
            "progress": job.get("progress", 0),
            "result": job.get("result"),
            "error": job.get("error"),
        }
    # 2. 共享卷状态文件（跨容器轮询兜底）
    file_status = _read_music_job_status_file(job_id)
    if file_status:
        return {"job_id": job_id, **file_status}
    # 2. 降级查 task_state_machine（供 create.py 等用例写入的位置）
    try:
        from app.services.task_state_machine import get_task
        task = await get_task(job_id)
    except Exception:
        task = None
    if not task:
        return {
            "job_id": job_id,
            "status": "not_found",
            "progress": 0,
            "error": "任务不存在"
        }
    
    status = task["status"]
    progress_map = {
        "queued": 10,
        "processing": 50,
        "completed": 100,
        "failed": 100,
        "cancelled": 100,
    }
    
    result = None
    if status == "completed":
        try:
            result = json.loads(task.get("output", "{}"))
        except:
            result = {"audio_url": task.get("output", "")}
    
    return {
        "job_id": job_id,
        "status": status,
        "progress": progress_map.get(status, 0),
        "result": result,
        "error": task.get("error"),
    }


@router.get("/styles")
async def list_styles():
    """获取支持的音乐风格"""
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
