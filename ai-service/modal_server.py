"""Modal 部署入口 — Avireon AI Music Platform"""

from pathlib import Path
import modal

REPO_ROOT = Path(__file__).parent

# ── Image 构建阶段 ──
# 注意: add_local_* 必须放在所有构建步骤末尾（Modal 要求）
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install_from_requirements(str(REPO_ROOT / "requirements.txt"))
    .env({"PYTHONPATH": "/root"})
    .add_local_dir(
        str(REPO_ROOT),
        remote_path="/root/ai-service",
    )
)

app = modal.App("avireon-ai-music")


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
    ],
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


@app.function(image=image)
def doctor():
    import sys, pathlib, importlib
    pkg_root = "/root/ai-service"
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)
    importlib.invalidate_caches()

    for rel in ["app", "app/models", "app/routers",
                "app/routes", "app/routes/ai", "app/services"]:
        p = pathlib.Path(pkg_root) / rel / "__init__.py"
        sz = p.stat().st_size if p.exists() else -1
        print(f"  {p} size={sz}")

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