"""Modal 部署入口 — Avireon AI Music Platform"""

from pathlib import Path
import modal

REPO_ROOT = Path(__file__).parent

# ── Image 构建阶段 ──
# 注意: add_local_* 必须放在所有构建步骤末尾（Modal 要求）
# ignore=["data/"] 排除本地 data 目录（SQLite DB + 上传文件），避免镜像非空目录挂载 Volume 冲突
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install_from_requirements(str(REPO_ROOT / "requirements.txt"))
    .env({"PYTHONPATH": "/root"})
    .add_local_dir(
        str(REPO_ROOT),
        remote_path="/root/ai-service",
        ignore=["data/"],
    )
)

app = modal.App("avireon-ai-music")

# 持久化存储卷 — 存放数据目录（SQLite 数据库 + 上传文件），跨容器共享、不被回收
data_volume = modal.Volume.from_name("avireon-data-v2", create_if_missing=True)

# ── GPU 推理镜像（CogVideoX 视频 / MusicGen 音乐），与 CPU web 镜像完全隔离 ──
# 模型权重通过 HF 本地下载缓存到 model_volume，无需任何外部 API key
model_volume = modal.Volume.from_name("avireon-models-v1", create_if_missing=True)

gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "torch>=2.3",
        "diffusers>=0.30",
        "transformers>=4.44",
        "accelerate>=0.33",
        "sentencepiece",
        "soundfile",
        "imageio",
        "imageio-ffmpeg",
        "huggingface_hub",
    )
    .env({
        "HF_HOME": "/models/hf",
        "PYTHONIOENCODING": "utf-8",
    })
)


@app.function(
    image=gpu_image,
    gpu="A10G",
    timeout=60 * 30,
    max_containers=4,
    volumes={"/models": model_volume},
)
@modal.concurrent(max_inputs=2)
def cogvideo_generate(prompt: str, num_frames: int = 49, steps: int = 30) -> bytes:
    """CogVideoX-2b 文本生成视频，返回 mp4 字节。模型本地缓存到 /models/hf，无外部 API key。"""
    import os
    import pathlib
    import tempfile
    os.makedirs("/models/hf", exist_ok=True)
    import torch
    from diffusers import CogVideoXPipeline
    from diffusers.utils import export_to_video

    pipe = CogVideoXPipeline.from_pretrained(
        "THUDM/CogVideoX-2b", torch_dtype=torch.float16, low_cpu_mem_usage=True,
    )
    # 顺序 CPU offload：每步仅将当前组件放上 GPU，大幅降低显存占用（A10G 22G 可跑）
    pipe.enable_sequential_cpu_offload(gpu_id=0)
    if hasattr(pipe, "enable_vae_tiling"):
        pipe.enable_vae_tiling()
    if hasattr(pipe.vae, "enable_slicing"):
        pipe.vae.enable_slicing()
    num_frames = max(9, min(int(num_frames), 49))
    if (num_frames - 1) % 8 != 0:
        num_frames = 49
    out = pipe(prompt=prompt, num_frames=num_frames, num_inference_steps=int(steps), guidance_scale=6.0)
    frames = out.frames[0]
    fd, tmp = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    export_to_video(frames, tmp, fps=8)
    data = pathlib.Path(tmp).read_bytes()
    pathlib.Path(tmp).unlink(missing_ok=True)
    return data


@app.function(
    image=gpu_image,
    gpu="T4",
    timeout=60 * 20,
    max_containers=4,
    volumes={"/models": model_volume},
)
@modal.concurrent(max_inputs=4)
def musicgen_generate(prompt: str, max_new_tokens: int = 512) -> bytes:
    """MusicGen-small 生成音乐，返回 wav 字节。模型本地缓存到 /models/hf，无外部 API key。"""
    import os
    import pathlib
    import tempfile
    os.makedirs("/models/hf", exist_ok=True)
    import torch
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

    processor = AutoProcessor.from_pretrained("facebook/musicgen-small")
    model = MusicgenForConditionalGeneration.from_pretrained("facebook/musicgen-small")
    model.to("cuda")
    inputs = processor(text=[prompt], padding=True, return_tensors="pt").to("cuda")
    with torch.no_grad():
        audio = model.generate(**inputs, max_new_tokens=int(max_new_tokens))
    samples = audio[0].cpu().numpy()
    import soundfile as sf
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(tmp, samples[0], samplerate=32000)
    data = pathlib.Path(tmp).read_bytes()
    pathlib.Path(tmp).unlink(missing_ok=True)
    return data


@app.function(
    image=image,
    max_containers=10,
    timeout=60 * 60,
    cpu=0.125,
    memory=4096,
    secrets=[
        modal.Secret.from_name("openrouter-key"),
        modal.Secret.from_name("siliconflow-key"),
        modal.Secret.from_name("avireon-secrets"),
        modal.Secret.from_name("avireon-config"),
        modal.Secret.from_name("agnes-key"),
    ],
    volumes={"/root/ai-service/data": data_volume},
)
@modal.asgi_app()
def web():
    import sys
    import pathlib
    import importlib

    pkg_root = "/root/ai-service"
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)

    # ── 运行时二次兜底：修复体积过小 / 缺失的 __init__.py ──
    marker = '"""package"""'
    pkg_dirs = [
        "app", "app/models", "app/routers", "app/core",
        "app/routes", "app/routes/ai", "app/services",
    ]
    for rel in pkg_dirs:
        p = pathlib.Path(pkg_root) / rel / "__init__.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        sz_before = p.stat().st_size if p.exists() else -1
        if not p.exists() or sz_before < 10:
            p.write_text(marker + "\n", encoding="utf-8")
            print(f"[auto-init] {rel}/__init__.py {sz_before}B -> {p.stat().st_size}B", flush=True)

    importlib.invalidate_caches()

    # 切换工作目录，确保 StaticFiles("static") 等相对路径能找到文件
    import os
    os.chdir(pkg_root)

    # ── 延迟导入 FastAPI app ──
    from app.main import app as fastapi_app
    return fastapi_app


@app.function(
    image=image,
    timeout=60 * 60,
    cpu=1.0,
    memory=4096,
    secrets=[
        modal.Secret.from_name("openrouter-key"),
        modal.Secret.from_name("siliconflow-key"),
        modal.Secret.from_name("avireon-secrets"),
        modal.Secret.from_name("avireon-config"),
        modal.Secret.from_name("agnes-key"),
    ],
    volumes={"/root/ai-service/data": data_volume},
)
def run_mv_job(
    job_id: str,
    user_id: int,
    lyrics: str,
    title: str,
    mv_style: str,
    num_scenes: int,
    creation_id: int,
):
    """独立容器执行 MV 全流程生成 — 容器存活期=任务运行期，不被 web 容器回收打断。

    结果写入共享数据卷的 SQLite（generation_jobs / videos 表），
    前端可跨容器轮询 GET /api/v1/ai/mv/job/{job_id}。
    """
    import sys
    import pathlib
    import importlib

    pkg_root = "/root/ai-service"
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)
    importlib.invalidate_caches()

    import asyncio
    from app.database import init_db

    async def _entry():
        await init_db()
        from app.routes.ai.mv import _run_mv_job
        await _run_mv_job(
            job_id=job_id,
            user_id=user_id,
            lyrics=lyrics,
            title=title,
            mv_style=mv_style,
            num_scenes=num_scenes,
            creation_id=creation_id,
        )

    asyncio.run(_entry())
    return {"job_id": job_id, "done": True}


@app.function(
    image=image,
    volumes={"/root/ai-service/data": data_volume},
    secrets=[
        modal.Secret.from_name("openrouter-key"),
        modal.Secret.from_name("siliconflow-key"),
        modal.Secret.from_name("avireon-secrets"),
        modal.Secret.from_name("avireon-config"),
        modal.Secret.from_name("agnes-key"),
    ],
)
def doctor():
    import sys, pathlib, importlib, os
    pkg_root = "/root/ai-service"
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)
    importlib.invalidate_caches()

    # 检查 Volume 挂载目录
    for rel in ["app", "app/models", "app/routers",
                "app/routes", "app/routes/ai", "app/services"]:
        p = pathlib.Path(pkg_root) / rel / "__init__.py"
        sz = p.stat().st_size if p.exists() else -1
        print(f"  {p} size={sz}")

    up = pathlib.Path("/root/ai-service/data/uploads")
    print(f"  uploads exists={up.exists()} dir={up.is_dir()}")
    if up.exists():
        for sub in sorted(up.iterdir()):
            n = sum(1 for _ in sub.iterdir()) if sub.is_dir() else 1
            print(f"    {sub.name}: {n} files")
            if sub.is_dir():
                files = sorted(sub.iterdir())[:3]
                for f in files:
                    print(f"      {f.name} size={f.stat().st_size if f.is_file() else 'dir'}")

    try:
        import app.main
        print("  [OK] app.main")
    except Exception as e:
        print(f"  [FAIL] app.main -> {e}")
    try:
        from app.routes.ai.music import router
        print("  [OK] music")
    except Exception as e:
        print(f"  [FAIL] music -> {e}")
    try:
        import subprocess
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30)
        print(f"  ffmpeg rc={r.returncode} head={r.stdout.splitlines()[0] if r.stdout else r.stderr[:120]}")
    except Exception as e:
        print(f"  [FAIL] ffmpeg -> {e}")
    import pathlib as _pl
    for fp in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
               "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]:
        print(f"  font {fp} exists={_pl.Path(fp).exists()}")
    import os
    for k in ["SILICONFLOW_API_KEY", "OPENROUTER_API_KEY", "RUNWAY_API_KEY", "AGNES_API_KEY"]:
        v = os.getenv(k, "")
        print(f"  env {k} = {'<set len=' + str(len(v)) + '>' if v else '<EMPTY>'}")
        if v:
            print(f"    prefix={v[:8]}... suffix={v[-6:]}")

    # 清理 0 字节的残留上传文件（生成失败产生的空文件污染卷）
    import shutil
    data_dir = pathlib.Path("/root/ai-service/data/uploads")
    removed = 0
    if data_dir.exists():
        for sub in sorted(data_dir.iterdir()):
            if not sub.is_dir():
                continue
            for f in sorted(sub.iterdir()):
                try:
                    if f.is_file() and f.stat().st_size == 0:
                        f.unlink()
                        removed += 1
                        print(f"  [cleanup] removed empty: {f.name}")
                except OSError as e:
                    print(f"  [cleanup] skip {f.name}: {e}")
    print(f"  cleanup: removed {removed} empty files")

    # ── SiliconFlow 各端点实测（确认 key 可用性与可用模型） ──
    sf_key = os.getenv("SILICONFLOW_API_KEY", "")
    if sf_key:
        import httpx as _hx
        base = "https://api.siliconflow.cn/v1"
        hdr = {"Authorization": f"Bearer {sf_key}", "Content-Type": "application/json"}

        def _probe(name, method, url, body=None):
            try:
                if method == "POST":
                    r = _hx.Client(timeout=30).post(url, headers=hdr, json=body)
                else:
                    r = _hx.Client(timeout=30).get(url, headers=hdr)
                txt = r.text[:200].replace("\n", " ")
                print(f"  [SF][{name}] {method} {r.status_code}: {txt}")
                return r
            except Exception as e:
                print(f"  [SF][{name}] {method} EXC {type(e).__name__}: {e}")
                return None

        _probe("chat", "POST", f"{base}/chat/completions",
               {"model": "Qwen/Qwen2.5-7B-Instruct",
                "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8})
        _probe("models", "GET", f"{base}/models")
        for m in ["Qwen/Qwen3-8B", "Qwen/Qwen2.5-7B-Instruct",
                  "THUDM/GLM-4-9B-0414", "BAAI/bge-large-zh-v1.5"]:
            _probe(f"chat-{m.split('/')[-1]}", "POST", f"{base}/chat/completions",
                   {"model": m, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8})
        _probe("image", "POST", f"{base}/images/generations",
               {"model": "black-forest-labs/FLUX.1-schnell",
                "prompt": "a red apple", "image_size": "512x512"})
        _probe("video", "POST", f"{base}/video/generations",
               {"model": "Qwen/Qwen2.5-VL-7B-Instruct", "prompt": "a cat walking"})
    else:
        print("  [SF] SILICONFLOW_API_KEY EMPTY, skip probe")

    # ── 关键域名连通性（判断第三方服务可用性） ──
    import socket as _sock
    for probe_host in ["api-inference.huggingface.co", "api.siliconflow.cn",
                       "api.mureka.ai", "www.soundhelix.com", "openrouter.ai",
                       "apihub.agnes-ai.com"]:
        try:
            ip = _sock.getaddrinfo(probe_host, 443, proto=_sock.IPPROTO_TCP)
            print(f"  [DNS] {probe_host} -> {ip[0][4][0]}")
        except Exception as e:
            print(f"  [DNS] {probe_host} -> FAIL {type(e).__name__}: {e}")

    # ── Agnes Video V2.0 免费视频 API 实测（创建任务验证 key） ──
    agnes_key = os.getenv("AGNES_API_KEY", "")
    if agnes_key:
        import httpx as _hx
        import time as _time
        hdr = {"Authorization": f"Bearer {agnes_key}", "Content-Type": "application/json"}
        try:
            r = _hx.Client(timeout=30).post(
                "https://apihub.agnes-ai.com/v1/videos",
                headers=hdr,
                json={
                    "model": "agnes-video-v2.0",
                    "prompt": "A gentle ocean wave rolling onto a sandy beach at golden hour, slow cinematic motion",
                    "num_frames": 49,
                    "frame_rate": 24,
                },
            )
            print(f"  [AGNES][create] {r.status_code}: {r.text[:300]}")
            if r.status_code in (200, 201):
                data = r.json()
                video_id = data.get("video_id") or data.get("id")
                if video_id:
                    _time.sleep(12)
                    q = _hx.Client(timeout=30).get(
                        f"https://apihub.agnes-ai.com/agnesapi?video_id={video_id}",
                        headers={"Authorization": f"Bearer {agnes_key}"},
                    )
                    print(f"  [AGNES][poll] {q.status_code}: {q.text[:300]}")
        except Exception as e:
            print(f"  [AGNES] EXC {type(e).__name__}: {e}")
    else:
        print("  [AGNES] AGNES_API_KEY EMPTY, skip probe")
