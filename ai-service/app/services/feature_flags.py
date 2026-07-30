"""功能开关灰度管控 — 单环境变量 FEATURE_STAGE 控制整批功能。

阶段定义：
  stage=1  公测基础版（默认）
    开放: ai_music / ai_lyrics / ai_tts / ai_mv_simple / health / docs
    关闭: voice_clone / remix / mastering / chord_split / stem_separation
          multi_collab / copyright_scan / material_store / membership_pay
          community_ugc / multi_publish / batch_tasks / smart_cutout

  stage=2  专业版
    放开 stage=1 关闭的大部分重型功能

  stage=3  全量放开
    所有功能可用（保留 admin/manual_override 机制）

使用方式（路由层）：
    from app.services.feature_flags import require_feature

    @router.post("/voice-clone")
    @require_feature("voice_clone")
    async def clone_voice(...):
        ...

环境变量：
  FEATURE_STAGE=1   整数 1/2/3，默认 1
  FORCE_ENABLE_<NAME>=true   强制开启某功能（admin 调试）
  FORCE_DISABLE_<NAME>=true  强制关闭某功能（应急止血）
"""
from __future__ import annotations

import inspect
import os
from functools import wraps
from typing import Awaitable, Callable, Optional

from fastapi import HTTPException
from loguru import logger

from app.config import get_settings


# ---------------------------------------------------------------------------
# 功能 → 阶段映射
# ---------------------------------------------------------------------------

_FEATURE_MIN_STAGE: dict[str, int] = {
    # ── stage=1 开放（min_stage=1） ──
    "ai_music":         1,
    "ai_lyrics":        1,
    "ai_tts":           1,
    "ai_mv_simple":     1,
    "health":           1,
    "docs":             1,
    # ── stage=1 关闭，stage=2 开放 ──
    "voice_clone":      2,
    "remix":            2,
    "mastering":        2,
    "chord":            2,
    "stem_separation":  2,
    "smart_cutout":     2,
    "batch_tasks":      2,
    "ai_cover":         2,
    # ── stage=3 才开放的商业/协作功能 ──
    "multi_collab":     3,
    "copyright_scan":   3,
    "material_store":   3,
    "membership_pay":   3,
    "community_ugc":    3,
    "multi_publish":    3,
    # ── MV 高级渲染 stage=3 ──
    "ai_mv_advanced":   3,
}

# 中文友好名称，用于错误提示
_FEATURE_NAMES: dict[str, str] = {
    "ai_music":         "AI 音乐生成",
    "ai_lyrics":        "歌词生成",
    "ai_tts":           "基础 TTS",
    "ai_mv_simple":     "简易 MV 模板",
    "ai_mv_advanced":   "高级 MV 渲染",
    "voice_clone":      "人声克隆",
    "remix":            "Remix 混音",
    "mastering":        "母带处理",
    "chord":            "和弦专业处理",
    "stem_separation":  "音频分轨",
    "smart_cutout":     "智能抠图",
    "batch_tasks":      "批量任务",
    "ai_cover":         "AI 封面图生成",
    "multi_collab":     "多人协作工程",
    "copyright_scan":   "版权扫描",
    "material_store":   "素材商店",
    "membership_pay":   "会员订阅付费",
    "community_ugc":    "社区 UGC 榜单",
    "multi_publish":    "多平台发布",
}

# ---------------------------------------------------------------------------
# 分级限流：light（轻量） / heavy（重型）
# ---------------------------------------------------------------------------

_FEATURE_RATE_TIER: dict[str, str] = {
    # light — 核心轻量，宽松限流
    "ai_music":        "light",
    "ai_lyrics":       "light",
    "ai_tts":          "light",
    "ai_mv_simple":    "light",
    "health":          "light",
    "docs":            "light",
    # heavy — 重型高算力，严格限流
    "voice_clone":     "heavy",
    "remix":           "heavy",
    "mastering":       "heavy",
    "chord":           "heavy",
    "stem_separation": "heavy",
    "smart_cutout":    "heavy",
    "batch_tasks":     "heavy",
    "ai_cover":        "heavy",
    "ai_mv_advanced":  "heavy",
    "multi_collab":    "heavy",
    "copyright_scan":  "heavy",
    "material_store":  "heavy",
    "membership_pay":  "heavy",
    "community_ugc":   "heavy",
    "multi_publish":   "heavy",
}


# ---------------------------------------------------------------------------
# 检查逻辑
# ---------------------------------------------------------------------------


def _current_stage() -> int:
    """读取当前阶段（环境变量优先，否则走 config）。"""
    raw = os.getenv("FEATURE_STAGE")
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning("FEATURE_STAGE={} 非法，兜底 1", raw)
    try:
        return get_settings().feature_stage
    except Exception:
        return 1


def rate_tier(feature: str) -> str:
    """返回功能限流等级：'light'（轻量）/ 'heavy'（重型）。"""
    return _FEATURE_RATE_TIER.get(feature, "heavy")


def is_enabled(feature: str) -> bool:
    """判断某功能在当前阶段是否开放。

    优先级：
      FORCE_DISABLE_<FEATURE>=true  → False（应急止血）
      FORCE_ENABLE_<FEATURE>=true   → True（admin 调试）
      _FEATURE_MIN_STAGE[feature] <= current_stage
    """
    if feature not in _FEATURE_MIN_STAGE:
        logger.warning("未知功能名 '{}'，默认关闭", feature)
        return False

    env_disable = os.getenv(f"FORCE_DISABLE_{feature.upper()}", "").lower()
    if env_disable in ("1", "true", "yes"):
        return False

    env_enable = os.getenv(f"FORCE_ENABLE_{feature.upper()}", "").lower()
    if env_enable in ("1", "true", "yes"):
        return True

    min_stage = _FEATURE_MIN_STAGE[feature]
    return _current_stage() >= min_stage


def feature_stage() -> int:
    """当前阶段（1/2/3），便于前端展示。"""
    return _current_stage()


def list_features() -> dict[str, dict]:
    """返回所有功能 + 当前是否开启 + 中文名称 + 最低阶段 + 限流等级。

    可供前端配置面板或 admin 后台调用。
    """
    result = {}
    for key, min_stage in _FEATURE_MIN_STAGE.items():
        result[key] = {
            "enabled":     is_enabled(key),
            "min_stage":   min_stage,
            "name":        _FEATURE_NAMES.get(key, key),
            "current_stage": _current_stage(),
            "rate_tier":   _FEATURE_RATE_TIER.get(key, "heavy"),
        }
    return result


def unavailable_message(feature: str) -> str:
    """生成友好提示文案。"""
    name = _FEATURE_NAMES.get(feature, feature)
    stage = _current_stage()
    return (
        f"{name} 暂未开放"
        if stage < _FEATURE_MIN_STAGE.get(feature, 3)
        else f"{name} 已被管理员临时关闭"
    )


# ---------------------------------------------------------------------------
# 路由装饰器
# ---------------------------------------------------------------------------


def require_feature(feature: str) -> Callable:
    """FastAPI 路由装饰器 — 功能未开放时返回 503。

    用法:
        @router.post("/voice-clone")
        @require_feature("voice_clone")
        async def clone_voice(...):
            ...

    返回结构遵循项目 ErrorResponse 约定:
        HTTP 503
        { "error": "功能暂未开放", "detail": "人声克隆 暂未开放" }
    """
    def decorator(func: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
        import typing
        # 保留原始函数的类型注解，确保 FastAPI 能正确解析 UploadFile 等类型
        hints = typing.get_type_hints(func)
        sig = inspect.signature(func)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not is_enabled(feature):
                logger.info(
                    "feature_gate BLOCKED | feature={} stage={} path={}",
                    feature, _current_stage(), feature,
                )
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "功能暂未开放",
                        "detail": unavailable_message(feature),
                        "feature": feature,
                        "current_stage": _current_stage(),
                        "required_stage": _FEATURE_MIN_STAGE.get(feature, 3),
                    },
                )
            return await func(*args, **kwargs)

        # 跳过 Request 类型的参数 — FastAPI 会特殊注入，不应出现在签名中
        # 否则 FastAPI 会把它误判为 query 参数导致 422
        from starlette.requests import Request as _StarletteRequest
        filtered_params = {
            name: p for name, p in sig.parameters.items()
            if hints.get(name) is not _StarletteRequest
        }
        wrapper.__annotations__ = hints
        wrapper.__signature__ = sig.replace(parameters=list(filtered_params.values()))
        return wrapper
    return decorator


def require_feature_sync(feature: str) -> Callable:
    """同步路由装饰器（少数同步端点可用）。"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not is_enabled(feature):
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "功能暂未开放",
                        "detail": unavailable_message(feature),
                        "feature": feature,
                        "current_stage": _current_stage(),
                    },
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# 分级限流装饰器
# ---------------------------------------------------------------------------


def rate_limit_heavy(feature: str) -> Callable:
    """FastAPI 路由装饰器 — 重型接口超出每日额度时返回 429。

    用法:
        @router.post("/voice-clone")
        @rate_limit_heavy("voice_clone")
        async def clone_voice(...):
            ...
    """
    def decorator(func: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from app.services.usage_tracker import check_daily_limits
            user_id = 1
            for arg in args:
                if hasattr(arg, 'state') and hasattr(arg.state, 'user_id'):
                    user_id = arg.state.user_id
                    break
            try:
                await check_daily_limits(user_id, feature)
            except HTTPException:
                raise
            return await func(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# 端点：状态查询
# ---------------------------------------------------------------------------


def features_summary() -> dict:
    """供 /api/v1/health 或 /api/v1/features 端点返回的精简状态。"""
    features = list_features()
    return {
        "stage": _current_stage(),
        "open_count": sum(1 for v in features.values() if v["enabled"]),
        "closed_count": sum(1 for v in features.values() if not v["enabled"]),
        "features": features,
    }
