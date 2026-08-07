"""容器内多进程启动器：opencode serve + OpenAI 兼容网关 + 业务 FastAPI。

在 Modal 的 web 容器（@modal.asgi_app 单一 ASGI 进程）中，启动业务 FastAPI 之前，
以子进程方式拉起两个网关服务并等待就绪：

    进程1  opencode serve --host 127.0.0.1:4098    OpenCode 原生后端（资源受限）
    进程2  python -m app.services.opencode_gateway  OpenAI 兼容网关 127.0.0.1:4096
    进程3  业务 FastAPI（ASGI 主进程）

三个进程共用同一容器 Network Namespace，127.0.0.1 内互相可达。
本模块仅在 web 进程启动时被调用；GPU 子函数不经过本模块。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

GATEWAY_BACKEND_PORT = int(os.getenv("GATEWAY_BACKEND_PORT", "4098"))
GATEWAY_PUBLIC_PORT = int(os.getenv("GATEWAY_PUBLIC_PORT", "4096"))
GATEWAY_PUBLIC_HOST = os.getenv("GATEWAY_PUBLIC_HOST", "127.0.0.1")
BACKEND_READY_TIMEOUT = float(os.getenv("OPENCODE_READY_TIMEOUT", "120"))
PROBE_INTERVAL = 0.5


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """探测 TCP 端口是否可连接。"""
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _find_opencode() -> str:
    """定位 opencode 可执行文件。"""
    for name in ("opencode", "opencode.exe"):
        p = shutil.which(name)
        if p:
            return p
    for cand in (
        "/usr/local/bin/opencode",
        "/root/.opencode/bin/opencode",
        "/root/.local/bin/opencode",
        "/root/.npm-global/bin/opencode",
        "/usr/bin/opencode",
    ):
        if os.path.exists(cand):
            return cand
    return "opencode"


def _start_opencode_backend(logf) -> subprocess.Popen | None:
    """启动 opencode serve，用 prlimit + nice 限制资源。"""
    bin_path = _find_opencode()
    port = GATEWAY_BACKEND_PORT
    cmd = [
        "prlimit",
        "--nofile=256:32768", "--nproc=128:128", "--cpu=120",
        "--nice=-5",  # 比主业务进程稍低优先级
        bin_path, "serve", "--hostname", "127.0.0.1", "--port", str(port),
    ]
    if shutil.which("prlimit") is None:
        # 无 prlimit 时退回 nice + 直接起
        cmd = ["nice", "-n", "15", bin_path, "serve", "--hostname", "127.0.0.1", "--port", str(port)]

    env = dict(os.environ)
    env.setdefault("NODE_OPTIONS", "--max-old-space-size=512")
    try:
        return subprocess.Popen(
            cmd,
            stdout=logf, stderr=subprocess.STDOUT,
            env=env, start_new_session=True,
        )
    except Exception as exc:
        print(f"[launcher] opencode serve launch failed: {type(exc).__name__}: {exc}", flush=True)
        ret = _fallback_opencode(bin_path, port, logf, env)
        return ret


def _fallback_opencode(bin_path: str, port: int, logf, env: dict) -> subprocess.Popen | None:
    """prlimit/nice 均不可用的兜底：直接进程启动（仍限制 NODE_HEAP）。"""
    try:
        return subprocess.Popen(
            [bin_path, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
            stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True,
        )
    except Exception as exc:
        print(f"[launcher] opencode fallback failed: {exc}", flush=True)
        return None


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def start_gateways(logf=None):
    """拉起 opencode backend + OpenAI 兼容网关两个子进程。

    若 4096 已可用（本地共享宿主网关），则不重复启动并返回空列表。
    """
    if _port_open(GATEWAY_PUBLIC_HOST, GATEWAY_PUBLIC_PORT):
        print(
            f"[launcher] gateway already listening on {GATEWAY_PUBLIC_HOST}:{GATEWAY_PUBLIC_PORT}, reuse existing",
            flush=True,
        )
        return []

    logf = logf or (subprocess.DEVNULL if os.environ.get("GATEWAY_QUIET") else sys.stdout)
    procs: list[subprocess.Popen] = []

    # 1) opencode 原生 backend（若 4098 未占用）
    if not _port_open(GATEWAY_PUBLIC_HOST, GATEWAY_BACKEND_PORT):
        p = _start_opencode_backend(logf)
        if p:
            procs.append(p)
        else:
            print("[launcher] WARNING: opencode backend not started; gateway will be degraded", flush=True)
    else:
        print(f"[launcher] backend {GATEWAY_BACKEND_PORT} already listening, reuse", flush=True)

    # 2) OpenAI 兼容网关（纯 Python）
    ready = sys.executable

    import uuid

    pyvenv = os.path.join(_repo_root(), ".venv", "Scripts", "python.exe")
    if os.path.exists(pyvenv) and os.environ.get("LOCAL_DEV"):
        ready = pyvenv

    gwenv = dict(os.environ)
    gwenv["OPENCODE_BACKEND_URL"] = f"http://127.0.0.1:{GATEWAY_BACKEND_PORT}"
    gwenv["GATEWAY_PORT"] = str(GATEWAY_PUBLIC_PORT)
    gwenv["GATEWAY_HOST"] = GATEWAY_PUBLIC_HOST
    gwenv["OPENCODE_MODEL"] = os.getenv("LOCAL_GATEWAY_MODEL", "opencode/free")
    try:
        gw = subprocess.Popen(
            [ready, "-m", "app.services.opencode_gateway"],
            stdout=logf, stderr=subprocess.STDOUT,
            cwd=_repo_root(), env=gwenv, start_new_session=True,
        )
        procs.append(gw)
    except Exception as exc:
        print(f"[launcher] gateway launch failed: {exc}", flush=True)
    return procs


def wait_ready(procs: list[subprocess.Popen] | None, timeout: float | None = None) -> bool:
    """等待 backend(4098) 与 gateway(4096) 就绪，返回是否成功。"""
    timeout = timeout or BACKEND_READY_TIMEOUT
    deadline = time.monotonic() + timeout

    backend_ready = False
    while time.monotonic() < deadline:
        if _port_open(GATEWAY_PUBLIC_HOST, GATEWAY_BACKEND_PORT):
            backend_ready = True
            break
        if procs:
            alive = any(p.poll() is None for p in procs)
            if not alive:
                break
        time.sleep(PROBE_INTERVAL)

    if not backend_ready:
        print(f"[launcher] ERROR: opencode backend (:{GATEWAY_BACKEND_PORT}) not ready in {timeout:.0f}s", flush=True)
        return False

    while time.monotonic() < deadline:
        if _port_open(GATEWAY_PUBLIC_HOST, GATEWAY_PUBLIC_PORT):
            print(f"[launcher] gateway ready on {GATEWAY_PUBLIC_HOST}:{GATEWAY_PUBLIC_PORT}", flush=True)
            return True
        time.sleep(PROBE_INTERVAL)

    print(f"[launcher] ERROR: gateway (:{GATEWAY_PUBLIC_PORT}) not ready in {timeout:.0f}s", flush=True)
    return False