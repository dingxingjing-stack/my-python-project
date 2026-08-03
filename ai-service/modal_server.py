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
    volumes={"/root/ai-service/data": data_volume},
    secrets=[
        modal.Secret.from_name("openrouter-key"),
        modal.Secret.from_name("siliconflow-key"),
        modal.Secret.from_name("avireon-secrets"),
        modal.Secret.from_name("avireon-config"),
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
                       "api.mureka.ai", "www.soundhelix.com", "openrouter.ai"]:
        try:
            ip = _sock.getaddrinfo(probe_host, 443, proto=_sock.IPPROTO_TCP)
            print(f"  [DNS] {probe_host} -> {ip[0][4][0]}")
        except Exception as e:
            print(f"  [DNS] {probe_host} -> FAIL {type(e).__name__}: {e}")
