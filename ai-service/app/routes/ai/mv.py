"""AI MV 视频全流程 — V4.0 本地开源模型优先 + FFmpeg 淡入淡出转场。

链路：
1. Nemotron 生成故事剧本 + 分镜 JSON
2. 每帧分镜生成静态图片序列（图片序列拼接 MV，非动态镜头）
   Layer 1  Modal FLUX.1-schnell 本地（FP8 量化，T4，Apache-2.0 免费）
   Layer 2  SiliconFlow SDXL（GPU 配额耗尽 / Flux 失败时兜底）
3. 音频（朗读歌词）：Layer 1  Kokoro-82M 本地 TTS → Layer 2  SoundHelix 背景音乐
4. FFmpeg 图片序列淡入淡出转场拼接 + 字幕 + 音频 mux → 成品 MV

任务采用异步 job 模式：POST /ai/mv/generate 立即返回 job_id，
前端轮询 GET /ai/mv/job/{job_id} 获取进度与结果。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from app.database import get_db
from app.services.ai_scheduler import get_scheduler
from app.services.local_storage import get_local_storage
from app.services.feature_flags import require_feature

router = APIRouter(prefix="/ai", tags=["ai-mv"])

# 内存任务存储（与 music.py 的 job 轮询模式一致）
_mv_job_store: dict[str, dict] = {}


def _mv_status_dir() -> Path:
    """跨容器状态目录 = data/mv_jobs（共享卷上，规避 SQLite 跨容器 WAL 不可靠）。"""
    from app.services.local_storage import get_local_storage
    base = get_local_storage()._base  # .../data/uploads
    d = base.parent / "mv_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_job_status(job_id: str, payload: dict) -> None:
    """将任务状态写成 JSON 文件（共享卷）。Modal 卷对整文件写入/替换一致性良好。"""
    try:
        p = _mv_status_dir() / f"{job_id}.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as exc:
        print(f"[MV] 状态文件写失败 job={job_id}: {exc}", flush=True)


def _read_job_status_file(job_id: str) -> dict | None:
    """读取共享卷上的任务状态文件（跨容器轮询的主通道）。"""
    try:
        p = _mv_status_dir() / f"{job_id}.json"
        if not p.exists():
            print(f"[MV-POLL] job={job_id} 状态文件不存在: {p}", flush=True)
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        print(f"[MV-POLL] job={job_id} 读取状态文件: {data.get('status')}", flush=True)
        return data
    except Exception as exc:
        print(f"[MV-POLL] job={job_id} 状态文件读取异常: {type(exc).__name__}: {exc}", flush=True)
        return None


@router.post("/mv/generate")
@require_feature("ai_mv_simple")
async def generate_mv_full(request: Request):
    """MV 全流程生成 — 异步任务，立即返回 job_id，前端轮询 /ai/mv/job/{job_id}。

    三层降级：Agnes 免费视频 → Modal CogVideoX → FFmpeg 幻灯片；
    音乐：Modal MusicGen → SoundHelix 免费背景音乐。
    """
    req = await request.json()
    user_id = req.get("user_id", 1)
    lyrics = req.get("lyrics", "")
    title = req.get("title", "Untitled")
    mv_style = req.get("style", "Cinematic")
    num_scenes = int(req.get("num_scenes", 1) or 1)
    creation_id = req.get("creation_id")

    if not lyrics:
        raise HTTPException(400, "Missing lyrics")

    job_id = str(uuid.uuid4())[:8]
    _mv_job_store[job_id] = {
        "status": "queued",
        "progress": 0,
        "result": None,
        "error": None,
    }

    db = await get_db()
    await db.execute(
        "INSERT INTO generation_jobs (job_id, user_id, task_type, status) VALUES (?, ?, ?, ?)",
        (job_id, user_id, "mv_full", "processing"),
    )
    await db.commit()

    # 优先用 Modal 独立函数容器执行（容器存活=任务运行期，避免 web 容器回收打断）
    spawned = _spawn_modal_mv_job(job_id, user_id, lyrics, title, mv_style, num_scenes, creation_id)
    if not spawned:
        asyncio.create_task(_run_mv_job(job_id, user_id, lyrics, title, mv_style, num_scenes, creation_id))

    return {"job_id": job_id}


def _spawn_modal_mv_job(job_id, user_id, lyrics, title, mv_style, num_scenes, creation_id) -> bool:
    """尝试通过 Modal Function.from_name spawn 后台任务；失败返回 False 走本地 asyncio。"""
    try:
        import modal
        fn = modal.Function.from_name("avireon-ai-music", "run_mv_job")
        fn.spawn(
            job_id=job_id,
            user_id=user_id,
            lyrics=lyrics,
            title=title,
            mv_style=mv_style,
            num_scenes=num_scenes,
            creation_id=creation_id,
        )
        print(f"[MV] job={job_id} 已通过 Modal 独立容器后台执行", flush=True)
        return True
    except Exception as exc:
        print(f"[MV] Modal spawn 失败（本地/降级）: {type(exc).__name__}: {exc}", flush=True)
        return False


@router.get("/mv/job/{job_id}")
async def get_mv_job_status(job_id: str):
    """查询 MV 生成任务状态 — 前端轮询用。

    优先级：共享卷状态文件（跨容器最可靠）→ SQLite → 内存 store。
    """
    # 1) 共享卷状态文件（Modal 独立容器写入，web 容器读取）
    file_status = _read_job_status_file(job_id)
    if file_status:
        return {"job_id": job_id, **file_status}

    # 2) SQLite 兜底
    db = await get_db()
    cur = await db.execute(
        "SELECT status, error_message, ai_response, model_name FROM generation_jobs WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job_id,),
    )
    row = await cur.fetchone()
    if row:
        status = row["status"]
        if status == "completed":
            video_url = row["ai_response"] or ""
            return {
                "job_id": job_id,
                "status": "completed",
                "progress": 100,
                "result": {"video_url": video_url} if video_url else None,
                "error": None,
            }
        if status == "failed":
            return {
                "job_id": job_id,
                "status": "failed",
                "progress": 100,
                "result": None,
                "error": row["error_message"],
            }
        return {
            "job_id": job_id,
            "status": status,
            "progress": 50,
            "result": None,
            "error": None,
        }

    # 3) 内存 store 兜底（本地 asyncio 模式）
    job = _mv_job_store.get(job_id)
    if job:
        return {"job_id": job_id, **job}
    raise HTTPException(404, "Job not found")


async def _run_mv_job(job_id, user_id, lyrics, title, mv_style, num_scenes, creation_id):
    """后台任务：跑完整 MV 生成链路，结果写入内存 store + SQLite（供跨容器轮询）。

    可能运行于本地 asyncio.create_task（内存 store 已初始化）或 Modal 独立容器
    （内存 store 为空），因此先补一个本地 store 条目，最终以 SQLite 为准。
    """
    if job_id not in _mv_job_store:
        _mv_job_store[job_id] = {"status": "queued", "progress": 0, "result": None, "error": None}
    _mv_job_store[job_id]["status"] = "processing"
    _mv_job_store[job_id]["progress"] = 5
    _write_job_status(job_id, {"status": "processing", "progress": 5, "result": None, "error": None})
    t0 = asyncio.get_running_loop().time()
    try:
        scheduler = get_scheduler()
        video_url = await _generate_mv_from_lyrics(
            lyrics=lyrics,
            title=title,
            mv_style=mv_style,
            num_scenes=num_scenes,
            user_id=user_id,
            creation_id=creation_id,
            scheduler=scheduler,
            job_id=job_id,
        )
        _mv_job_store[job_id] = {
            "status": "completed",
            "progress": 100,
            "result": {"video_url": video_url},
            "error": None,
        }
        _write_job_status(job_id, _mv_job_store[job_id])
        # 结果写入 SQLite，供跨容器轮询
        try:
            db = await get_db()
            await db.execute(
                "UPDATE generation_jobs SET status='completed', ai_response=?, elapsed_ms=? WHERE job_id=?",
                (video_url, int((asyncio.get_running_loop().time() - t0) * 1000), job_id),
            )
            await db.commit()
        except Exception as exc:
            print(f"[MV] 结果写库失败: {exc}", flush=True)
    except Exception as exc:
        print(f"[MV] job={job_id} 异常: {type(exc).__name__}: {exc}", flush=True)
        import traceback as _tb
        _tb.print_exc()
        _mv_job_store[job_id] = {
            "status": "failed",
            "progress": 100,
            "result": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
        _write_job_status(job_id, _mv_job_store[job_id])
        try:
            db = await get_db()
            await db.execute(
                "UPDATE generation_jobs SET status='failed', error_message=? WHERE job_id=?",
                (str(exc)[:500], job_id),
            )
            await db.commit()
        except Exception:
            pass
    finally:
        dur = int((asyncio.get_running_loop().time() - t0) * 1000)
        print(f"[MV] job={job_id} status={_mv_job_store[job_id].get('status')} elapsed={dur}ms", flush=True)


@router.post("/mv/regenerate-scene")
async def regenerate_scene(request: Request):
    """单场景重新生成。"""
    req = await request.json()
    user_id = req.get("user_id", 1)
    video_id = req.get("video_id")
    scene_index = req.get("scene_index", 0)

    if not video_id:
        raise HTTPException(400, "Missing video_id")

    db = await get_db()
    cur = await db.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
    video_row = await cur.fetchone()
    if not video_row:
        raise HTTPException(404, "Video not found")

    scenes_data = json.loads(video_row["scenes_data"] or "[]")
    if scene_index >= len(scenes_data):
        raise HTTPException(400, "Invalid scene_index")

    scheduler = get_scheduler()
    scene = scenes_data[scene_index]
    prompt = scene.get("image_prompt", "") or scene.get("description", "")

    try:
        image_result = await scheduler.generate_image_sdxl(prompt=prompt, num_images=1, user_id=user_id)
        if image_result.data.get("image_urls"):
            scenes_data[scene_index]["image_url"] = image_result.data["image_urls"][0]

        await db.execute(
            "UPDATE videos SET scenes_data = ? WHERE id = ?",
            (json.dumps(scenes_data, ensure_ascii=False), video_id),
        )
        await db.commit()

        return {
            "success": True,
            "data": {
                "scene_index": scene_index,
                "image_url": image_result.data.get("image_urls", [None])[0],
            },
        }
    except Exception as exc:
        raise HTTPException(500, f"Scene regeneration failed: {exc}")


@router.get("/mv/{video_id}")
async def get_mv(video_id: int):
    db = await get_db()
    cur = await db.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Video not found")
    return {"success": True, "data": dict(row)}


@router.get("/mv/list/{creation_id}")
async def list_mvs(creation_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM videos WHERE creation_id = ? ORDER BY created_at DESC",
        (creation_id,),
    )
    rows = await cur.fetchall()
    return {"success": True, "data": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# 内部：完整 MV 生成链路（供 create.py 调用）
# ---------------------------------------------------------------------------


async def _generate_mv_from_lyrics(
    lyrics: str,
    title: str,
    mv_style: str = "Cinematic",
    num_scenes: int = 2,
    user_id: int = 1,
    creation_id: Optional[int] = None,
    scheduler=None,
    job_id: str = "",
) -> str:
    """完整 MV 链路：分镜 → 图片序列（Flux→SiliconFlow）→ Kokoro/SoundHelix 音频 → FFmpeg 淡入淡出合成。"""
    if scheduler is None:
        from app.services.ai_scheduler import get_scheduler
        scheduler = get_scheduler()

    storage = get_local_storage()

    # Step 1: Nemotron 生成分镜
    storyboard_result = await scheduler.generate_mv_storyboard(
        lyrics=lyrics, title=title, mv_style=mv_style,
        num_scenes=num_scenes, user_id=user_id,
    )
    scenes = _parse_storyboard(storyboard_result.text)
    if not isinstance(scenes, list) or not scenes:
        scenes = [{"scene": 1, "description": "Opening scene", "image_prompt": storyboard_result.text[:200]}]

    # 分镜数不足时用歌词行补齐到 num_scenes，保证图片序列长度与请求一致
    lyric_lines = [l for l in (lyrics or "").splitlines() if l.strip()]
    while len(scenes) < max(int(num_scenes), 1):
        i = len(scenes)
        line = lyric_lines[i % len(lyric_lines)] if lyric_lines else f"Scene {i + 1}"
        scenes.append({
            "scene": i + 1,
            "description": f"Scene {i + 1}",
            "image_prompt": f"{mv_style}, cinematic music video scene, {line}",
        })

    # Step 2: 图片序列生成（Flux 本地 → SiliconFlow SDXL 兜底）
    from app.services.mv_scheduler import get_mv_scheduler
    mv_sched = get_mv_scheduler()

    async def _gen_one_image(i_scene):
        i, scene = i_scene
        if not isinstance(scene, dict):
            scene = {"description": str(scene), "image_prompt": ""}
        prompt = (scene.get("image_prompt") or scene.get("description") or "").strip()
        if not prompt:
            line = lyric_lines[i] if i < len(lyric_lines) else f"Scene {i + 1}"
            prompt = f"{mv_style}, cinematic music video scene, {line}"
        try:
            url, _channel = await mv_sched.generate_scene_image(
                {"image_prompt": prompt, "description": prompt}, mv_style, storage, i
            )
            return url or ""
        except Exception:
            return ""

    scene_images = await asyncio.gather(*[_gen_one_image(is_) for is_ in enumerate(scenes)])
    scene_images = [u for u in scene_images if u]
    if scene_images:
        print(f"[MV] 图片序列 {len(scene_images)} 张")

    # Step 3: 音频（Kokoro 本地 TTS → SoundHelix 兜底）
    audio_url, audio_channel = await mv_sched.generate_music(lyrics, title, mv_style, storage)
    if audio_url:
        print(f"[MV] 音频通道={audio_channel}")

    # 若 GPU 配额耗尽且图片全部失败：返回友好错误而非脏渲染
    if mv_sched.gpu_quota_exhausted and not scene_images:
        raise RuntimeError("算力额度耗尽，请稍后重试。当前免费 GPU 额度已用完，可稍后再试。")

    # Step 4: FFmpeg 合成最终 MV（图片序列淡入淡出 + 字幕 + 音频 mux）
    final_video_url = await _compose_mv(
        scene_images=scene_images,
        lyrics=lyrics,
        style=mv_style,
        storage=storage,
        audio_url=audio_url,
    )

    # Step 5: 存入数据库
    from app.database import get_db
    db = await get_db()
    if creation_id:
        await db.execute(
            "INSERT INTO videos (creation_id, user_id, video_url, video_type, storyboard, scenes_data, style, model_name) VALUES (?, ?, ?, 'mv', ?, ?, ?, ?)",
            (creation_id, user_id, final_video_url, storyboard_result.text[:5000], json.dumps(scenes, ensure_ascii=False)[:5000], mv_style, storyboard_result.model_name),
        )
        if job_id:
            await db.execute(
                "UPDATE generation_jobs SET status='completed', model_name=?, elapsed_ms=? WHERE job_id=?",
                (storyboard_result.model_name, storyboard_result.elapsed_ms, job_id),
            )
        await db.commit()

    return final_video_url


async def _compose_mv(
    scene_images: list[str],
    lyrics: str,
    style: str,
    storage,
    audio_url: Optional[str] = None,
) -> str:
    """FFmpeg 合成：图片序列淡入淡出转场 + 字幕 + 背景音乐 → 最终 MV。"""
    ffmpeg_available = True
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        ffmpeg_available = False

    if not ffmpeg_available:
        return "/static/videos/placeholder.mp4"

    # 背景音乐：解析 /uploads/... URL 为容器内路径
    audio_fs_path = None
    if audio_url:
        if audio_url.startswith("/uploads/"):
            ls_base = get_local_storage()._base
            audio_fs_path = str(ls_base / audio_url[len("/uploads/"):])
        elif audio_url.startswith("http"):
            audio_fs_path = audio_url

    # 过滤空/无效图片 URL，仅保留有效路径
    scene_images = [u for u in (scene_images or []) if u and u.startswith("/uploads/")]
    if not scene_images:
        # 无图片：用 FFmpeg 生成歌词文字幻灯片视频，保证 MV 可播放
        return await _compose_text_mv(lyrics, style, storage, audio_url=audio_url)

    # 生成 SRT 字幕
    subtitle_file = None
    srt_lines = _lyrics_to_srt(lyrics)
    if srt_lines:
        sf = tempfile.NamedTemporaryFile(mode="w", suffix=".srt", delete=False, encoding="utf-8")
        sf.write("\n".join(srt_lines))
        sf.close()
        subtitle_file = sf.name

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        output_path = tmp.name

    def _to_fs_path(url: str) -> str:
        """将 /uploads/... URL 转换为容器内绝对路径，供 FFmpeg 读取。"""
        if url.startswith("/uploads/"):
            from app.services.local_storage import get_local_storage
            ls_base = get_local_storage()._base
            rel = url[len("/uploads/"):]
            return str(ls_base / rel)
        return url

    try:
        # 每张图片生成 5s 片段（统一 1280x720 + 淡入淡出转场）
        segments = []
        for idx, img in enumerate(scene_images):
            seg = f"{output_path}.{idx}.ts"
            src = _to_fs_path(img)
            vf = (
                "scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,"
                "fade=t=in:st=0:d=0.6,fade=t=out:st=4.4:d=0.6"
            )
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-t", "5", "-i", src,
                "-vf", vf,
                "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                seg,
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode != 0:
                print(f"[MV][slide] 场景 {idx} FFmpeg 失败 rc={proc.returncode}: {err.decode(errors='replace')[:300]}")
                continue
            segments.append(seg)

        if not segments:
            print("[MV][slide] 所有图片片段生成失败，回退文字幻灯片 MV")
            return await _compose_text_mv(lyrics, style, storage, audio_url=audio_url)

        # concat demuxer 拼接所有片段
        concat_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        for seg in segments:
            concat_file.write(f"file '{seg}'\n")
        concat_file.close()

        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file.name]
        if subtitle_file:
            cmd += ["-vf", f"subtitles={subtitle_file}"]
        cmd += ["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", output_path]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            print(f"[MV][slide] concat 失败 rc={proc.returncode}: {err.decode(errors='replace')[:300]}")
            return await _compose_text_mv(lyrics, style, storage, audio_url=audio_url)
        Path(concat_file.name).unlink(missing_ok=True)
        for seg in segments:
            Path(seg).unlink(missing_ok=True)

        # 若存在背景音乐，将音频 mux 进视频（-shortest 截到视频长度）
        if audio_fs_path and Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            try:
                muxed = output_path + ".mux.mp4"
                mux_cmd = [
                    "ffmpeg", "-y",
                    "-i", output_path,
                    "-i", audio_fs_path,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-shortest",
                    muxed,
                ]
                proc = await asyncio.create_subprocess_exec(*mux_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await proc.communicate()
                if Path(muxed).exists() and Path(muxed).stat().st_size > 0:
                    Path(output_path).unlink(missing_ok=True)
                    output_path = muxed
                else:
                    print("[MV] 音频 mux 失败，保留无声视频")
            except Exception as exc:
                print(f"[MV] 音频 mux 异常: {exc}")

        video_bytes = Path(output_path).read_bytes()
        if not video_bytes:
            print("[MV] FFmpeg 图片合成输出为空，回退到文字幻灯片 MV")
            return await _compose_text_mv(lyrics, style, storage, audio_url=audio_url)
        return storage.save_video(video_bytes, ext="mp4")
    finally:
        try:
            Path(output_path).unlink(missing_ok=True)
        except Exception:
            pass
        if subtitle_file:
            try:
                Path(subtitle_file).unlink(missing_ok=True)
            except Exception:
                pass


async def _compose_text_mv(lyrics: str, style: str, storage, audio_url: Optional[str] = None) -> str:
    """无 SDXL 图片兜底：用 FFmpeg 生成歌词文字幻灯片视频，保证 MV 可播放。"""
    import re as _re
    lines = [l.strip() for l in lyrics.strip().splitlines() if l.strip()]
    plain = []
    for l in lines:
        if _re.match(r"^(Title|LRC|Verse|Chorus|Bridge|Intro|Outro)\s*\d*:", l, flags=_re.IGNORECASE):
            continue
        plain.append(l)
    if not plain:
        plain = ["Avireon AI Music", "Your Song"]
    pages = []
    chunk = 3
    for i in range(0, len(plain), chunk):
        pages.append(plain[i:i + chunk])
    if not pages:
        pages = [["Avireon AI Music"]]

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        output_path = tmp.name

    def _safe_text(t: str) -> str:
        """清理 drawtext 特殊字符，防止 filter 语法被破坏。"""
        import re as _r
        t = _r.sub(r"[^A-Za-z0-9\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af .,!?\-_'\"()]", " ", t)
        return t.strip().replace("'", "\\'").replace(":", "\\:")[:120]

    try:
        # 每页 5 秒，用纯色背景 + 白色文字生成视频帧
        segments = []
        for idx, page in enumerate(pages):
            seg = f"{output_path}.{idx}.ts"
            text = " | ".join(page)
            text_safe = _safe_text(text)
            if not text_safe:
                text_safe = "Avireon AI Music"
            drawtext = f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{text_safe}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2"
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=0x1a1a2e:s=1280x720:d=5",
                "-vf", drawtext,
                "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                seg,
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode != 0:
                print(f"[MV][textslide] 页面 {idx} FFmpeg 失败 rc={proc.returncode}: {err.decode(errors='replace')[:300]}")
                continue
            segments.append(seg)

        if not segments:
            print("[MV][textslide] 所有文字幻灯片生成失败，回退 placeholder")
            return "/static/videos/placeholder.mp4"

        if len(segments) == 1:
            final = output_path
            import shutil
            shutil.copyfile(segments[0], final)
        else:
            concat_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            for s in segments:
                concat_file.write(f"file '{s}'\n")
            concat_file.close()
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_file.name,
                "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                output_path,
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode != 0:
                print(f"[MV][textslide] concat 失败 rc={proc.returncode}: {err.decode(errors='replace')[:300]}")
                return "/static/videos/placeholder.mp4"
            Path(concat_file.name).unlink(missing_ok=True)

        for s in segments:
            Path(s).unlink(missing_ok=True)

        # 存在背景音乐则 mux 进视频
        if audio_url and Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            audio_fs_path = None
            if audio_url.startswith("/uploads/"):
                ls_base = get_local_storage()._base
                audio_fs_path = str(ls_base / audio_url[len("/uploads/"):])
            elif audio_url.startswith("http"):
                audio_fs_path = audio_url
            if audio_fs_path:
                try:
                    muxed = output_path + ".mux.mp4"
                    mux_cmd = [
                        "ffmpeg", "-y",
                        "-i", output_path,
                        "-i", audio_fs_path,
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-shortest",
                        muxed,
                    ]
                    proc = await asyncio.create_subprocess_exec(*mux_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await proc.communicate()
                    if Path(muxed).exists() and Path(muxed).stat().st_size > 0:
                        Path(output_path).unlink(missing_ok=True)
                        output_path = muxed
                except Exception as exc:
                    print(f"[MV][textslide] 音频 mux 异常: {exc}")

        video_bytes = Path(output_path).read_bytes()
        if not video_bytes:
            return "/static/videos/placeholder.mp4"
        return storage.save_video(video_bytes, ext="mp4")
    finally:
        try:
            Path(output_path).unlink(missing_ok=True)
        except Exception:
            pass


def _parse_storyboard(text: str) -> list[dict]:
    try:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return [{"scene": 1, "description": "Opening scene", "image_prompt": text[:200]}]


def _lyrics_to_srt(lyrics: str) -> list[str]:
    """将 LRC 格式歌词转为 SRT 字幕格式。"""
    import re
    lines = lyrics.strip().splitlines()
    srt_lines = []
    idx = 1

    # 尝试从 LRC 块解析
    in_lrc = False
    lrc_entries = []
    for line in lines:
        if line.strip().upper() == "LRC:":
            in_lrc = True
            continue
        if in_lrc and line.strip():
            lrc_match = re.match(r"\[(\d+):(\d+)\.(\d+)\]\s*(.+)", line.strip())
            if lrc_match:
                m, s, ms, text = lrc_match.groups()
                start_sec = int(m) * 60 + int(s) + int(ms) / 100
                lrc_entries.append((start_sec, text.strip()))

    if lrc_entries:
        for i, (start, text) in enumerate(lrc_entries):
            end = lrc_entries[i + 1][0] if i + 1 < len(lrc_entries) else start + 5
            srt_lines.append(str(idx))
            srt_lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
            srt_lines.append(text)
            srt_lines.append("")
            idx += 1
    else:
        # 无时间戳，生成默认字幕
        plain = re.sub(r"(Title|LRC):.*", "", lyrics, flags=re.IGNORECASE)
        plain = re.sub(r"(Verse|Chorus|Bridge|Intro|Outro)\s*\d*:", "", plain)
        plain_lines = [l.strip() for l in plain.strip().splitlines() if l.strip()]
        interval = max(3, 30 // max(len(plain_lines), 1))
        for i, line in enumerate(plain_lines):
            start = i * interval
            end = start + interval
            srt_lines.append(str(idx))
            srt_lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
            srt_lines.append(line)
            srt_lines.append("")
            idx += 1

    return srt_lines


def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
