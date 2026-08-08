"""Modal 部署入口 — Avireon AI Music Platform"""

from pathlib import Path
import modal

REPO_ROOT = Path(__file__).parent

# ── Image 构建阶段 ──
# 注意: add_local_* 必须放在所有构建步骤末尾（Modal 要求）
# ignore=["data/"] 排除本地 data 目录（SQLite DB + 上传文件），避免镜像非空目录挂载 Volume 冲突
OPENCODE_VERSION = "v1.18.15"


def _install_opencode():
    """下载并安装 opencode 原生二进制（linux-x64 静态包，无需 Node）。

    容器内以子进程方式运行 `opencode serve`，作为本地 Mode-A 免费网关。
    """
    import os
    import pathlib
    import shutil
    import tarfile
    import urllib.request

    url = (
        f"https://github.com/anomalyco/opencode/releases/download/"
        f"{OPENCODE_VERSION}/opencode-linux-x64.tar.gz"
    )
    dst = "/root/.opencode/bin/opencode"
    tmp = "/tmp/opencode.tar.gz"
    if os.path.exists(dst):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    print(f"[opencode] downloading {url}", flush=True)
    urllib.request.urlretrieve(url, tmp)
    with tarfile.open(tmp, "r:gz") as tf:
        tf.extractall("/tmp/opencode-x")
    for cand in pathlib.Path("/tmp/opencode-x").rglob("opencode"):
        if cand.is_file():
            shutil.copy(cand, dst)
            os.chmod(dst, 0o755)
            break
    print(f"[opencode] installed -> {dst}", flush=True)


image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "util-linux")
    .run_function(_install_opencode)
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

# ── HeartMuLa 3B + HeartCodec 独立卷 / 镜像 ──
# 权重已由 avireon-heartmula-poc 下载到 heartmula-models（/models/heartmula-ckpt/...），
# 与 avireon-models-v1 完全隔离，不共享 HF_HOME。
heartmula_volume = modal.Volume.from_name("heartmula-models", create_if_missing=True)

heartmula_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1", "git")
    .pip_install(
        "torch>=2.4,<2.11",
        "torchaudio>=2.4,<2.11",
        "torchcodec",
        "torchvision>=0.19,<0.26",
        "transformers==4.57.0",
        "tokenizers==0.22.1",
        "torchtune==0.4.0",
        "torchao==0.9.0",
        "accelerate==1.12.0",
        "bitsandbytes==0.49.0",
        "einops==0.8.1",
        "vector-quantize-pytorch==1.27.15",
        "soundfile",
        "tqdm==4.67.1",
    )
    .pip_install("git+https://github.com/HeartMuLa/heartlib.git@main")
    .env({
        "HF_HOME": "/models/hf",
        "PYTHONIOENCODING": "utf-8",
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
    })
)

gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "espeak-ng")
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
        "numpy",
        "optimum-quanto",
        "kokoro>=0.9.4",
        "misaki[zh]",
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


_FLUX_PIPE = None


def _get_flux_pipe():
    """懒加载并缓存 FLUX 流水线（模块级单例）。

    容器存活窗口（scaledown_window=300s）内多次调用复用同一实例，
    避免每次调用重复加载 219 权重分片（~120s/次）。
    """
    global _FLUX_PIPE
    if _FLUX_PIPE is not None:
        return _FLUX_PIPE

    import os
    import torch
    from diffusers import (
        AutoencoderKL,
        FlowMatchEulerDiscreteScheduler,
        FluxPipeline,
        FluxTransformer2DModel,
    )
    from optimum.quanto import freeze, qfloat8, quantize
    from huggingface_hub import hf_hub_download
    from transformers import CLIPTextModel, CLIPTokenizer, T5EncoderModel, T5TokenizerFast

    os.makedirs("/models/hf", exist_ok=True)
    # Niansun 镜像缺 scheduler_config.json，仅含 config.json，逐组件显式加载（确定性装配）。
    # 注意: 不能用 low_cpu_mem_usage=True —— 会让参数留在 meta/lazy 张量，
    # 之后 enable_model_cpu_offload 内部对 meta 张量调 .to() 抛
    # "Cannot copy out of meta tensor"（流水线并发必现）。改为显式加载到 CPU（容器内存已提高到 48GB）。
    repo = "Niansuh/FLUX.1-schnell"
    scheduler_cfg = hf_hub_download(repo, "scheduler/config.json", local_files_only=True)
    scheduler = FlowMatchEulerDiscreteScheduler.from_config(scheduler_cfg)
    transformer = FluxTransformer2DModel.from_pretrained(
        repo, subfolder="transformer", torch_dtype=torch.bfloat16, local_files_only=True
    )
    text_encoder = CLIPTextModel.from_pretrained(
        repo, subfolder="text_encoder", torch_dtype=torch.bfloat16, local_files_only=True
    )
    text_encoder_2 = T5EncoderModel.from_pretrained(
        repo, subfolder="text_encoder_2", torch_dtype=torch.bfloat16, local_files_only=True
    )
    vae = AutoencoderKL.from_pretrained(
        repo, subfolder="vae", torch_dtype=torch.bfloat16, local_files_only=True
    )
    tokenizer = CLIPTokenizer.from_pretrained(repo, subfolder="tokenizer", local_files_only=True)
    tokenizer_2 = T5TokenizerFast.from_pretrained(repo, subfolder="tokenizer_2", local_files_only=True)

    # FP8 量化 Transformer（权重 ~24GB → ~12GB），配合 CPU offload 在 16GB 显存上运行
    # 注意: quanto 的 quantize() 原地修改并返回 None，不能重新赋值
    quantize(transformer, weights=qfloat8)
    freeze(transformer)

    pipe = FluxPipeline(
        scheduler=scheduler,
        text_encoder=text_encoder,
        text_encoder_2=text_encoder_2,
        tokenizer=tokenizer,
        tokenizer_2=tokenizer_2,
        transformer=transformer,
        vae=vae,
    )
    pipe.enable_model_cpu_offload(gpu_id=0)
    if hasattr(pipe.vae, "enable_slicing"):
        pipe.vae.enable_slicing()
    if hasattr(pipe.vae, "enable_tiling"):
        pipe.vae.enable_tiling()
    _FLUX_PIPE = pipe
    return pipe


@app.function(
    image=gpu_image,
    gpu="T4",
    memory=49152,
    timeout=60 * 15,
    max_containers=1,
    scaledown_window=300,
    volumes={"/models": model_volume},
)
@modal.concurrent(max_inputs=1)
def flux_image_generate(prompt: str, width: int = 1024, height: int = 576, seed: int = 0) -> bytes:
    """FLUX.1-schnell (FP8 量化) 本地文生图，返回 jpg 字节。

    Apache-2.0 商用免费；4 步推理；FP8 量化 Transformer 后显存 ~12GB，T4 16GB 可跑。
    模型权重缓存到 /models/hf，无外部 API key。流水线按容器懒加载并缓存复用。
    """
    import os
    import pathlib
    import tempfile
    import torch

    pipe = _get_flux_pipe()
    generator = torch.Generator().manual_seed(int(seed))
    out = pipe(
        prompt=prompt,
        width=int(width),
        height=int(height),
        guidance_scale=0.0,
        num_inference_steps=4,
        max_sequence_length=256,
        generator=generator,
        output_type="pil",
    )
    img = out.images[0]
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img.save(tmp, "JPEG", quality=90)
    data = pathlib.Path(tmp).read_bytes()
    pathlib.Path(tmp).unlink(missing_ok=True)
    return data


@app.function(
    image=gpu_image,
    gpu="T4",
    timeout=60 * 10,
    max_containers=2,
    scaledown_window=300,
    volumes={"/models": model_volume},
)
@modal.concurrent(max_inputs=2)
def kokoro_tts(text: str, voice: str = "", speed: float = 1.0) -> bytes:
    """Kokoro-82M 本地 TTS，返回 24kHz wav 字节。

    Apache-2.0；自动按文本语言选择 voice（含中文用 z 语言 + zf_xiaobei，否则英文 af_heart）。
    模型权重缓存到 /models/hf，无外部 API key。
    """
    import io
    import os
    os.makedirs("/models/hf", exist_ok=True)
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    lang_code = "z" if has_cjk else "a"
    if not voice:
        voice = "zf_xiaobei" if has_cjk else "af_heart"

    pipeline = KPipeline(lang_code=lang_code)
    chunks = []
    for _gs, _ps, audio in pipeline(text[:600], voice=voice, speed=float(speed)):
        if audio is not None and len(audio):
            chunks.append(audio)
    if not chunks:
        return b""
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    buf = io.BytesIO()
    sf.write(buf, audio, 24000, format="WAV")
    return buf.getvalue()


_HEARTMULA_CKPT = "/models/heartmula-ckpt"


@app.function(
    image=heartmula_image,
    gpu="T4",
    timeout=60 * 45,
    max_containers=1,
    memory=16384,
    volumes={"/models": heartmula_volume},
)
@modal.concurrent(max_inputs=1)
def heartmula_generate(
    lyrics: str = "",
    tags: str = "",
    language: str = "pt",
    duration: int = 60,
    cfg_scale: float = 1.0,
) -> dict:
    """HeartMuLa 3B + HeartCodec 本地生成音乐，返回 MP3 字节。

    使用低层推理路径（HeartMuLaGenPipeline dtype 有 bug 会 fp32 OOM，必须走此路径）：
    HeartMuLa.from_pretrained(path, device_map=cuda, dtype=bfloat16) + 手动帧循环 +
    HeartCodec.detokenize([2,T]) → WAV → ffmpeg MP3。
    权重已缓存到 heartmula-models 卷（/models/heartmula-ckpt），无外部 API key。
    """
    import os
    import pathlib
    import subprocess
    import time

    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    import torch

    os.makedirs("/models/hf", exist_ok=True)
    tmp = pathlib.Path("/tmp/heartmula")
    tmp.mkdir(parents=True, exist_ok=True)
    label = f"hm-{int(time.time())}"
    wav_path = tmp / (label + ".wav")
    mp3_path = tmp / (label + ".mp3")

    t0 = time.time()

    from heartlib.pipelines.music_generation import _resolve_paths, HeartMuLaGenConfig
    from heartlib.heartmula.modeling_heartmula import HeartMuLa
    from heartlib.heartcodec.modeling_heartcodec import HeartCodec
    from tokenizers import Tokenizer

    mula_path, codec_path, tok_path, gcfg_path = _resolve_paths(_HEARTMULA_CKPT, "3B")
    tokenizer = Tokenizer.from_file(tok_path)
    gcfg = HeartMuLaGenConfig.from_file(gcfg_path)

    def _wrap(s):
        s = s.lower()
        if not s.startswith("<tag>"):
            s = "<tag>" + s
        ids = tokenizer.encode(s).ids
        if ids[0] != gcfg.text_bos_id:
            ids = [gcfg.text_bos_id] + ids
        if ids[-1] != gcfg.text_eos_id:
            ids = ids + [gcfg.text_eos_id]
        return ids

    tags_ids = _wrap(tags if tags else f"show, {language}, song, instrumental")
    lyrics_ids = _wrap(lyrics)
    prompt_len = len(tags_ids) + 1 + len(lyrics_ids)
    tokens = torch.zeros([prompt_len, 9], dtype=torch.long)
    tokens[: len(tags_ids), -1] = torch.tensor(tags_ids)
    tokens[len(tags_ids) + 1 :, -1] = torch.tensor(lyrics_ids)
    tokens = tokens.unsqueeze(0)  # [1, seq, parallel]
    tokens_mask = torch.zeros([prompt_len, 9], dtype=torch.bool)
    tokens_mask[:, -1] = True
    tokens_mask = tokens_mask.unsqueeze(0)
    muq_embed = torch.zeros([512], dtype=torch.bfloat16).unsqueeze(0)  # [1, 512]
    muq_idx = len(tags_ids)
    pos = torch.arange(prompt_len, dtype=torch.long).unsqueeze(0)

    tokens = tokens.to("cuda")
    tokens_mask = tokens_mask.to("cuda")
    muq_embed = muq_embed.to("cuda")
    pos = pos.to("cuda")

    model = HeartMuLa.from_pretrained(mula_path, device_map=torch.device("cuda"), dtype=torch.bfloat16)
    model.setup_caches(1)
    t1 = time.time()

    frames_out = []
    with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16):
        curr = model.generate_frame(
            tokens=tokens, tokens_mask=tokens_mask, input_pos=pos,
            temperature=1.0, topk=50, cfg_scale=cfg_scale,
            continuous_segments=muq_embed, starts=[muq_idx],
        )
    frames_out.append(curr[0:1])

    def _pad(tok):
        padded = torch.ones((tok.shape[0], 9), device=tok.device, dtype=torch.long) * gcfg.empty_id
        padded[:, :-1] = tok
        padded = padded.unsqueeze(1)
        pmask = torch.ones_like(padded, dtype=torch.bool)
        pmask[..., -1] = False
        return padded, pmask

    max_frames = min(int(duration) * 1000 // 80, 7500)
    for i in range(max_frames):
        ct, cm = _pad(curr)
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            curr = model.generate_frame(
                tokens=ct, tokens_mask=cm, input_pos=pos[:, -1:] + i + 1,
                temperature=1.0, topk=50, cfg_scale=cfg_scale,
                continuous_segments=None, starts=None,
            )
        frames_out.append(curr[0:1])
        if torch.any(curr[0:1] >= gcfg.audio_eos_id):
            break
    frames = torch.stack(frames_out).permute(1, 2, 0).squeeze(0)
    t2 = time.time()

    del model
    torch.cuda.empty_cache()

    codec = HeartCodec.from_pretrained(codec_path, device_map=torch.device("cuda"), dtype=torch.float32)
    frames_dev = frames.to(codec.device)
    wav = codec.detokenize(frames_dev)
    t3 = time.time()

    wav_float = wav.to(torch.float32).cpu()
    data = wav_float.numpy()
    if data.ndim == 3:
        data = data.squeeze(0)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    import soundfile as sf

    sf.write(str(wav_path), data.T if data.ndim == 2 else data, 48000, subtype="PCM_16")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), str(mp3_path)],
        check=True, timeout=120,
    )
    t4 = time.time()

    mp3_data = mp3_path.read_bytes()
    size = len(mp3_data)
    return {
        "status": "ok",
        "mp3": mp3_data,
        "size": size,
        "load_s": round(t1 - t0, 1),
        "gen_frames": len(frames_out),
        "gen_s": round(t2 - t1, 1),
        "codec_s": round(t3 - t2, 1),
        "total_s": round(t4 - t0, 1),
        "duration_s": round(len(frames_out) * 0.08, 1),
    }


@app.function(
    image=image,
    max_containers=10,
    timeout=60 * 60,
    cpu=0.125,
    memory=4096,
    secrets=[
        modal.Secret.from_name("openrouter-key"),
        modal.Secret.from_name("siliconflow-key"),
        modal.Secret.from_name("mureka-key"),
        modal.Secret.from_name("runway-key"),
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

    # ── 容器内多进程：拉起 OpenCode 本地 Mode-A 网关 ──
    # opencode serve(原生,4098) + OpenAI 兼容翻译网关(4096) 作为子进程在容器内运行，
    # 三者（业务 FastAPI / opencode / 网关）共享同一容器 Network Namespace，127.0.0.1 互通。
    # 必须等待 4096 网关就绪后再返回 FastAPI app，避免业务启动后首调才遇到 502。
    try:
        import sys as _sys
        if pkg_root not in _sys.path:
            _sys.path.insert(0, pkg_root)
        from app.services.gateway_launcher import start_gateways, wait_ready
        os.environ["LOCAL_GATEWAY_BASE_URL"] = os.environ.get(
            "LOCAL_GATEWAY_BASE_URL", "http://127.0.0.1:4096/v1"
        )
        os.environ.setdefault("OPENCODE_BACKEND_URL", "http://127.0.0.1:4098")
        procs = start_gateways()
        ok = wait_ready(procs, timeout=120)
        if not ok:
            print("[web] WARNING: gateway not ready; business will fall back to Mock", flush=True)
        else:
            print("[web] local Mode-A gateway is UP", flush=True)
    except Exception as _e:
        import traceback
        traceback.print_exc()
        print(f"[web] gateway launcher error, continue without local gateway: {_e}", flush=True)

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
        modal.Secret.from_name("mureka-key"),
        modal.Secret.from_name("runway-key"),
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
        modal.Secret.from_name("mureka-key"),
        modal.Secret.from_name("runway-key"),
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
