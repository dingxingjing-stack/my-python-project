"""服务启动自检 — 必填环境变量校验。

无第三方 LLM 密钥时，应用会通过 OpenCode 本地 Mode-A 网关（/root/ai-service 内
opencode serve + 4096 翻译网关）兜底生成，因此 SILICONFLOW/OPENROUTER 从"必填"
降级为"可选"：缺失时打印警告并按 local_gateway 路由，不再阻止启动。
"""
from __future__ import annotations

import os
import sys
import warnings

REQUIRED: dict[str, str] = {}

OPTIONAL: dict[str, str] = {
    "SILICONFLOW_API_KEY": "硅基流动（文本+代码+图片；缺失则 TEXT/CODE 走本地 Mode-A 网关）",
    "OPENROUTER_API_KEY":  "OpenRouter 自由模型池（缺失则 TEXT/CODE 走本地 Mode-A 网关）",
    "RUNWAY_API_KEY":      "Runway 视频生成（缺失则 MV 无动态镜头）",
    "AGNES_API_KEY":       "Agnes 提示词优化（缺失则降级本地 LLM）",
}


def run_startup_checks(*, exit_on_failure: bool = False) -> list[str]:
    missing: list[str] = []
    ok: list[str] = []

    for key, desc in REQUIRED.items():
        val = os.getenv(key, "")
        if not val or val.startswith("your-"):
            missing.append(f"{key} ({desc})")
        else:
            ok.append(f"{key}")

    for key, desc in OPTIONAL.items():
        val = os.getenv(key, "")
        if not val or val.startswith("your-"):
            print(f"[STARTUP] 可选: {key} ({desc}) 未配置，对应功能降级。", flush=True)

    if missing:
        msg = f"缺失必填密钥 ({len(missing)} 项):\n  " + "\n  ".join(missing)
        if exit_on_failure:
            print("[STARTUP] 致命错误:", msg, file=sys.stderr, flush=True)
            raise RuntimeError(msg)
        else:
            print("[STARTUP] 警告:", msg, flush=True)

    return missing