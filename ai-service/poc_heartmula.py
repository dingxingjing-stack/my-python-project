"""Stage 3B PoC: HeartMuLa 3B + HeartCodec on Modal T4 (lazy_load, 15GB).

Independent app (avireon-heartmula-poc) — does NOT touch production app.
Weights stored in dedicated volume `heartmula-models`.
PoC target: PT lyrics + tags -> HeartMuLa -> HeartCodec -> MP3.
"""
import time

import modal

VOLUME_NAME = "heartmula-models"
CKPT_ROOT = "/models/heartmula-ckpt"  # from_pretrained(pretrained_path) expects this layout

# from _resolve_paths: pretrained_path/HeartMuLa-oss-<version>, .../HeartCodec-oss,
#   .../tokenizer.json, .../gen_config.json
HF_REPOS = {
    # local subdir name -> hf repo
    "HeartMuLa-oss-3B": "HeartMuLa/HeartMuLa-oss-3B-happy-new-year",
    "HeartCodec-oss": "HeartMuLa/HeartCodec-oss-20260123",
    "HeartMuLaGen": "HeartMuLa/HeartMuLaGen",
}
# HeartMuLaGen repo only provides tokenizer.json + gen_config.json (config-only, no weights)

gpu_image = (
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

app = modal.App("avireon-heartmula-poc")
heartmula_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def _log(msg):
    print(f"[3B] {msg}", flush=True)


@app.function(
    image=gpu_image,
    timeout=60 * 40,
    volumes={"/models": heartmula_volume},
)
def download_weights() -> dict:
    """Download HeartMuLa + HeartCodec weights into volume (idempotent)."""
    import os
    import time as _t

    from huggingface_hub import login, snapshot_download

    token = os.environ.get("HF_TOKEN")
    if token:
        login(token=token, token_type="token")

    os.makedirs("/models/hf", exist_ok=True)
    os.makedirs(CKPT_ROOT, exist_ok=True)
    out = {}
    for subdir, repo in HF_REPOS.items():
        dest = os.path.join(CKPT_ROOT, subdir)
        try:
            t0 = _t.time()
            _log(f"downloading {repo} -> {dest}")
            snapshot_download(
                repo_id=repo,
                local_dir=dest,
                allow_patterns=["*.safetensors", "*.json", "*.bin", "*.txt", "*.model"],
            )
            out[subdir] = {"ok": True, "elapsed_s": round(_t.time() - t0, 1)}
        except Exception as exc:
            out[subdir] = {"ok": False, "error": str(exc)[:400]}
    # flatten HeartMuLaGen config files up one dir into ckpt root (repo stores them at repo root;
    # snapshot_download keeps them inside HeartMuLaGen/ — copy up)
    import shutil
    for fname in ("tokenizer.json", "gen_config.json"):
        src = os.path.join(CKPT_ROOT, "HeartMuLaGen", fname)
        dst = os.path.join(CKPT_ROOT, fname)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            _log(f"linked {fname} -> {CKPT_ROOT}")
    heartmula_volume.commit()
    return {"status": "done", "root": CKPT_ROOT, "repos": out}


@app.function(image=gpu_image, timeout=600, volumes={"/models": heartmula_volume})
def verify_import() -> dict:
    """Verify heartlib imports cleanly under Modal Python 3.11."""
    import json
    import torch

    got = {}
    for lib in ("torch", "transformers", "torchaudio", "torchao", "torchtune"):
        try:
            m = __import__(lib)
            got[lib] = getattr(m, "__version__", "?")
        except Exception as exc:
            got[lib] = f"ERR {exc.__class__.__name__}: {str(exc)[:200]}"
    try:
        from heartlib import HeartMuLaGenPipeline

        got["heartlib"] = "import OK"
    except Exception as exc:
        got["heartlib"] = f"ERR {exc.__class__.__name__}: {str(exc)[:300]}"
    got["cuda_avail"] = bool(torch.cuda.is_available())
    print(json.dumps(got, ensure_ascii=False), flush=True)
    return got


@app.function(
    image=gpu_image,
    gpu="T4",
    timeout=60 * 45,
    max_containers=1,
    volumes={"/models": heartmula_volume},
)
def generate_pt(
    lyrics: str = None,
    tags: str = None,
    max_audio_length_ms: int = 60000,
    lazy_load: bool = True,
    label: str = 'poc-pt-1',
    cfg_scale: float = 1.0,
) -> dict:
    if lyrics is None:
        lyrics = PT_LYRICS
    if tags is None:
        tags = PT_TAGS
    import os
    import pathlib
    import subprocess
    import time

    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')
    import torch

    os.makedirs('/models/hf', exist_ok=True)
    tmp = pathlib.Path('/models/hf/poc/')
    tmp.mkdir(parents=True, exist_ok=True)
    ly_path = tmp / 'lyrics.txt'
    tg_path = tmp / 'tags.txt'
    ly_path.write_text(lyrics, encoding='utf-8')
    tg_path.write_text(tags, encoding='utf-8')
    wav_path = tmp / (label + '.wav')
    mp3_path = tmp / (label + '.mp3')

    t0 = time.time()

    from heartlib.pipelines.music_generation import _resolve_paths, HeartMuLaGenConfig
    from heartlib.heartmula.modeling_heartmula import HeartMuLa
    from heartlib.heartcodec.modeling_heartcodec import HeartCodec
    from tokenizers import Tokenizer

    mula_path, codec_path, tok_path, gcfg_path = _resolve_paths(CKPT_ROOT, '3B')
    tokenizer = Tokenizer.from_file(tok_path)
    gcfg = HeartMuLaGenConfig.from_file(gcfg_path)

    def _wrap(s):
        s = s.lower()
        if not s.startswith('<tag>'):
            s = '<tag>' + s
        ids = tokenizer.encode(s).ids
        if ids[0] != gcfg.text_bos_id:
            ids = [gcfg.text_bos_id] + ids
        if ids[-1] != gcfg.text_eos_id:
            ids = ids + [gcfg.text_eos_id]
        return ids

    tags_ids = _wrap(tags)
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

    tokens = tokens.to('cuda')
    tokens_mask = tokens_mask.to('cuda')
    muq_embed = muq_embed.to('cuda')
    pos = pos.to('cuda')

    model = HeartMuLa.from_pretrained(mula_path, device_map=torch.device('cuda'), dtype=torch.bfloat16)
    model.setup_caches(1)
    print('[3B] model loaded, alloc=' + str(round(torch.cuda.memory_allocated()/1024**3, 2)) + 'GB', flush=True)
    t1 = time.time()

    frames_out = []
    with torch.no_grad(), torch.autocast(device_type='cuda', dtype=torch.bfloat16):
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

    max_frames = max_audio_length_ms // 80
    for i in range(max_frames):
        ct, cm = _pad(curr)
        with torch.no_grad(), torch.autocast(device_type='cuda', dtype=torch.bfloat16):
            curr = model.generate_frame(
                tokens=ct, tokens_mask=cm, input_pos=pos[:, -1:] + i + 1,
                temperature=1.0, topk=50, cfg_scale=cfg_scale,
                continuous_segments=None, starts=None,
            )
        frames_out.append(curr[0:1])
        if torch.any(curr[0:1] >= gcfg.audio_eos_id):
            print('[3B] audio EOS reached early', flush=True)
            break
    frames = torch.stack(frames_out).permute(1, 2, 0).squeeze(0)
    t2 = time.time()
    print('[3B] generated ' + str(len(frames_out)) + ' frames in ' + str(round(t2-t1,1)) + 's', flush=True)

    del model
    torch.cuda.empty_cache()

    codec = HeartCodec.from_pretrained(codec_path, device_map=torch.device('cuda'), dtype=torch.float32)
    frames_dev = frames.to(codec.device)
    wav = codec.detokenize(frames_dev)
    t3 = time.time()

    wav_float = wav.to(torch.float32).cpu()
    print('[3B] wav tensor shape=' + str(tuple(wav_float.shape)) + ' min=' + str(round(float(wav_float.min()),4)) + ' max=' + str(round(float(wav_float.max()),4)) + ' nans=' + str(int(torch.isnan(wav_float).sum())), flush=True)
    data = wav_float.numpy()
    if data.ndim == 3:
        data = data.squeeze(0)
    if data.ndim == 1:
        data = data.reshape(1, -1)  # mono -> (1, T)
    import soundfile as sf
    sf.write(str(wav_path), data.T if data.ndim == 2 else data, 48000, subtype='PCM_16')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(wav_path), str(mp3_path)], check=True, timeout=120)
    t4 = time.time()

    peak = torch.cuda.max_memory_allocated() / 1024**3 if torch.cuda.is_available() else 0
    size = mp3_path.stat().st_size if mp3_path.exists() else 0
    smi = ''
    try:
        smi = subprocess.run(
            ['nvidia-smi', '--query-gpu=memory.total,memory.used', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:
        pass

    heartmula_volume.commit()
    result = {
        'mp3_path': str(mp3_path),
        'bytes': size,
        'load_s': round(t1 - t0, 1),
        'gen_frames': len(frames_out),
        'gen_s': round(t2 - t1, 1),
        'codec_s': round(t3 - t2, 1),
        'total_s': round(t4 - t0, 1),
        'peak_alloc_gb': round(peak, 2),
        'lazy_load': lazy_load,
        'audio_ms': max_audio_length_ms,
        'nvidia_smi': smi,
    }
    return result

@app.function(
    image=gpu_image,
    gpu="T4",
    timeout=60 * 40,
    max_containers=1,
    volumes={"/models": heartmula_volume},
)
def probe_load() -> dict:
    """Decisive: measure VRAM of low-level mula load (bf16 vs default) to confirm fp32 bloat."""
    import os
    import time as _t
    import torch

    from heartlib.heartmula.modeling_heartmula import HeartMuLa

    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    for dtype_name, dt in [("bf16", torch.bfloat16), ("default", None)]:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        t0 = _t.time()
        try:
            if dt is not None:
                model = HeartMuLa.from_pretrained(
                    os.path.join(CKPT_ROOT, "HeartMuLa-oss-3B"), device_map=torch.device("cuda"), dtype=dt
                )
            else:
                model = HeartMuLa.from_pretrained(
                    os.path.join(CKPT_ROOT, "HeartMuLa-oss-3B"), device_map=torch.device("cuda")
                )
            alloc = torch.cuda.memory_allocated() / 1024**3
            peak = torch.cuda.max_memory_allocated() / 1024**3
            print(f"[probe] dtype={dtype_name} alloc={alloc:.2f}GB peak={peak:.2f}GB load_s={_t.time()-t0:.1f}", flush=True)
            del model
        except Exception as exc:
            msg = str(exc)[:200].replace("\n", " ")
            print(f"[probe] dtype={dtype_name} ERR {type(exc).__name__}: {msg}", flush=True)
    return {"done": True}


@app.function(
    image=gpu_image,
    gpu="T4",
    timeout=60 * 40,
    max_containers=1,
    volumes={"/models": heartmula_volume},
)
def probe_forward() -> dict:
    """Measure VRAM at each stage: load bf16 -> setup_caches -> one generate_frame."""
    import os
    import time as _t
    import torch

    from heartlib.heartmula.modeling_heartmula import HeartMuLa

    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    def allocated():
        return round(torch.cuda.memory_allocated() / 1024**3, 2)

    t0 = _t.time()
    model = HeartMuLa.from_pretrained(
        os.path.join(CKPT_ROOT, "HeartMuLa-oss-3B"), device_map=torch.device("cuda"), dtype=torch.bfloat16
    )
    print(f"[fw] loaded alloc={allocated()}GB load_s={_t.time()-t0:.1f}", flush=True)
    model.setup_caches(1)
    print(f"[fw] after setup_caches alloc={allocated()}GB peak={round(torch.cuda.max_memory_allocated()/1024**3,2)}GB", flush=True)

    # reproduce real multi-frame loop (like generation): prompt then N audio frames
    import time as _t2
    seq = 48  # like a short prompt
    tokens = torch.zeros((1, seq, 9), dtype=torch.long, device="cuda")
    tokens_mask = torch.ones((1, seq, 9), dtype=torch.bool, device="cuda")
    pos = torch.arange(seq, dtype=torch.long, device="cuda").unsqueeze(0)
    cur = None
    n_frames = 250  # ~ 250*80ms = 20s of audio
    t_loop0 = _t2.time()
    for i in range(n_frames):
        if cur is None:
            toks, msk = tokens, tokens_mask
            in_pos = pos
        else:
            toks = torch.ones((1, 1, 9), dtype=torch.long, device="cuda")
            msk = torch.ones((1, 1, 9), dtype=torch.bool, device="cuda")
            in_pos = pos[:, -1:] + i + 1
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            cur = model.generate_frame(tokens=toks, tokens_mask=msk, input_pos=in_pos, temperature=1.0, topk=50, cfg_scale=1.0)
        if i % 30 == 0:
            print(f"[fw] frame {i}: alloc={allocated()}GB peak={round(torch.cuda.max_memory_allocated()/1024**3,2)}GB", flush=True)
    print(f"[fw] {n_frames} frames done in {_t2.time()-t_loop0:.1f}s -> fps={(n_frames)/(_t2.time()-t_loop0):.1f}it/s", flush=True)
    print(f"[fw] final alloc={allocated()}GB peak={round(torch.cuda.max_memory_allocated()/1024**3,2)}GB", flush=True)
    return {"done": True}


PT_LYRICS = (
    "Nao e tarde para amar,\n"
    "Bailando ao som do nosso amor,\n"
    "O samba acende essa luz,\n"
    "O coracao nos guia.\n\n"
    "No ritmo da cidade a brilhar,\n"
    "Sinto a vida pulsar no peito,\n"
    "Cada verso que eu canto hoje,\n"
    "Eh um beijo guardado pra voce.\n\n"
    "Os meus passos nao tem pressa,\n"
    "O tempo parou pra nos dois,\n"
    "Uma guitarra, um violao,\n"
    "E a lua caiu sobre nos.\n\n"
    "Nao ha outra pessoa assim,\n"
    "Seu sorriso me mostra o caminho,\n"
    "Samba, amor, tambor e flor,\n"
    "O mundo gira em nossa volta.\n\n"
    "Deixa o hino do coracao,\n"
    "Tomar as favelas da alma,\n"
    "Na batucada eu me entrego,\n"
    "Voce e meu abrigo, minha calma.\n\n"
    "Sexta-feira chegou para sempre,\n"
    "A nossa historia e um carnaval,\n"
    "Nao e tarde, nunca e tarde,\n"
    "Para amar ate o final."
)

PT_TAGS = (
    "show <samba>, samba enredo, Brazilian love song, Portuguese, female vocal, "
    "acoustic guitar, cavaquinho, 92 bpm, romantic pop"
)