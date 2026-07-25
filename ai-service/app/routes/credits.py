"""使用统计 API — 仅展示每日用量，无积分系统。"""
from __future__ import annotations

from datetime import date
from fastapi import APIRouter, HTTPException, Query, Request

from app.config import get_settings
from app.database import get_db
from app.services.usage_tracker import ensure_daily_reset, get_global_calls_today, _get_user_quota_override

router = APIRouter(prefix="/api/v1/credits", tags=["stats"])


@router.get("/balance")
async def get_stats(request: Request):
    """获取当前用户每日用量信息。"""
    user_id = getattr(request.state, "user_id", 1)
    db = await get_db()
    usage = await ensure_daily_reset(user_id)
    override = await _get_user_quota_override(user_id)
    s = get_settings()

    calls_limit = override.get("daily_ai_calls_limit") or s.daily_max_ai_calls
    mv_limit = override.get("daily_mv_limit") or s.daily_mv_slots
    calls_limit += usage.get("bonus_generations", 0) or 0

    return {
        "daily": {
            "ai_calls_count": usage["ai_calls_count"],
            "ai_calls_limit": calls_limit,
            "mv_count": usage["mv_count"],
            "mv_limit": mv_limit,
            "global_calls_today": await get_global_calls_today(),
            "global_limit": s.daily_global_max_calls,
            "bonus_generations": usage.get("bonus_generations", 0) or 0,
        },
        "costs": {},
        "limits": {
            "daily_max_ai_calls": s.daily_max_ai_calls,
            "daily_global_max_calls": s.daily_global_max_calls,
            "daily_mv_slots": s.daily_mv_slots,
        },
    }
