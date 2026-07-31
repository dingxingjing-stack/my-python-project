"""AI MV 视频全流程 — V2.0 纯本地方案。

链路：
1. Nemotron 生成故事剧本 + 分镜 JSON
2. 硅基 SDXL 为每帧分镜生成静态图片
3. Runway 将静态图片转动态视频片段
4. FFmpeg 拼接动态片段 + 音频 + 字幕 → 成品 MV
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.database import get_db
from app.services.ai_scheduler import get_scheduler
from app.services.local_storage import get_local_storage
from app.services.feature_flags import require_feature

router = APIRouter(prefix="/ai", tags=["ai-mv"])


@router.post("/mv/generate")
@require_feature("ai_mv_advanced")
async def generate_mv_full(request: Request):
    """MV 全流程生成 — 分镜→生图→动态→合成，扣 20 Credits。"""
    req = await request.json()
    user_id = req.get("user_id", 1)
    lyrics = req.get("lyrics", "")
    title = req.get("title", "Untitled")
    mv_style = req.get("style", "Cinematic")
    num_scenes = req.get("num_scenes", 2)
    creation_id = req.get("creation_id")

    if not lyrics:
        raise HTTPException(400, "Missing lyrics")

    scheduler = get_scheduler()
    job_id = str(uuid.uuid4())[:8]

    db = await get_db()
    await db.execute(
        "INSERT INTO generation_jobs (job_id, user_id, task_type, status) VALUES (?, ?, ?, ?)",
        (job_id, user_id, "mv_full", "processing"),
    )
    await db.commit()

    try:
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

        return {
            "success": True,
            "data": {
                "job_id": job_id,
                "video_url": video_url,
                "note": "MV generated via SDXL + Runway + FFmpeg",
            },
        }

    except Exception as exc:
        await db.execute(
            "UPDATE generation_jobs SET status='failed', error_message=? WHERE job_id=?",
            (str(exc)[:500], job_id),
        )
        await db.commit()
        raise HTTPException(500, f"MV generation failed: {exc}")


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
    """完整 MV 链路：分镜 → SDXL 生图 → Runway → FFmpeg 合成。"""
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

    # Step 2: 硅基 SDXL 为每帧分镜生图（并发执行，节省 75% 时间）
    async def _gen_one_image(i_scene):
        i, scene = i_scene
        prompt = scene.get("image_prompt", "") or scene.get("description", f"Scene {i+1}")
        try:
            image_result = await scheduler.generate_image_sdxl(
                prompt=prompt,
                width=1024, height=576,
                num_images=1,
                user_id=user_id,
            )
            if image_result.data.get("image_urls"):
                return image_result.data["image_urls"][0]
        except Exception:
            pass
        return ""

    scene_images = await asyncio.gather(*[_gen_one_image(is_) for is_ in enumerate(scenes)])
    scene_images = list(scene_images)

    # Step 3: Runway 图片转动态（并发执行，或用占位视频）
    from app.services.runway_client import get_runway_client
    runway = get_runway_client()

    async def _gen_one_video(img_url):
        if not img_url:
            return None
        try:
            if runway.is_configured:
                task = await runway.image_to_video(
                    start_image=img_url,
                    prompt=f"{mv_style} scene, cinematic motion",
                    duration=5,
                )
                task_id = task.get("id", "")
                if task_id:
                    result = await runway.wait_for_task(task_id, timeout=120)
                    video_url = result.get("output", {}).get("url", "")
                    if video_url:
                        resp = await httpx.AsyncClient().get(video_url, timeout=120)
                        return storage.save_video(resp.content, ext="mp4")
        except Exception:
            pass
        return None

    video_segments_raw = await asyncio.gather(*[_gen_one_video(u) for u in scene_images])
    video_segments = [seg for seg in video_segments_raw if seg]

    # Step 4: FFmpeg 合成最终 MV
    final_video_url = await _compose_mv(
        scene_images=scene_images,
        video_segments=video_segments,
        lyrics=lyrics,
        style=mv_style,
        storage=storage,
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
    video_segments: list[str],
    lyrics: str,
    style: str,
    storage,
) -> str:
    """FFmpeg 合成：图片/视频 + 字幕 → 最终 MV。"""
    ffmpeg_available = True
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        ffmpeg_available = False

    if not ffmpeg_available or not scene_images:
        return "/static/videos/placeholder.mp4"

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

    try:
        # 如果有 Runway 视频片段，用 concat；否则用图片 slideshow
        if video_segments:
            concat_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            for seg in video_segments:
                concat_file.write(f"file '{seg}'\n")
            concat_file.close()

            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file.name]
            if subtitle_file:
                cmd += ["-vf", f"subtitles={subtitle_file}"]
            cmd += ["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", output_path]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            Path(concat_file.name).unlink(missing_ok=True)
        else:
            # 用图片生成 slideshow
            image_list = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            for img in scene_images:
                image_list.write(f"file '{img}'\nduration 5\n")
            image_list.close()

            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", image_list.name]
            if subtitle_file:
                cmd += ["-vf", f"subtitles={subtitle_file}"]
            cmd += ["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", output_path]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            Path(image_list.name).unlink(missing_ok=True)

        video_bytes = Path(output_path).read_bytes()
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
