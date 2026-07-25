"""每日生成次数限流 — 简化为纯计数系统。"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException
from loguru import logger

from app.config import get_settings
from app.database import get_db


async def ensure_daily_reset(user_id: int) -> dict:
    """确保今日 daily_usage 记录存在。"""
    s = get_settings()
    today = date.today().isoformat()
    db = await get_db()

    cur = await db.execute(
        "SELECT * FROM daily_usage WHERE user_id = ? AND usage_date = ?",
        (user_id, today),
    )
    row = await cur.fetchone()
    if row:
        return dict(row)

    await db.execute(
        "INSERT INTO daily_usage (user_id, usage_date, ai_calls_count, credits_granted, credits_used, mv_count) VALUES (?, ?, 0, 0, 0, 0)",
        (user_id, today),
    )
    await db.commit()
    return {"user_id": user_id, "usage_date": today, "ai_calls_count": 0, "credits_granted": 0, "credits_used": 0, "mv_count": 0}


async def _get_user_quota_override(user_id: int) -> dict:
    """获取用户限额覆盖配置。"""
    db = await get_db()
    cur = await db.execute(
        "SELECT daily_ai_calls_limit, daily_mv_limit FROM user_quota_overrides WHERE user_id = ?",
        (user_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else {}


async def check_daily_limits(user_id: int, action: str) -> dict:
    """检查用户今日是否还能调用 AI。返回用量信息或抛出 429 异常。"""
    s = get_settings()
    today = date.today().isoformat()
    usage = await ensure_daily_reset(user_id)
    override = await _get_user_quota_override(user_id)

    base_limit = override.get("daily_ai_calls_limit") or s.daily_max_ai_calls
    effective_limit = base_limit + (usage.get("bonus_generations", 0) or 0)
    if usage["ai_calls_count"] >= effective_limit:
        raise HTTPException(429, f"今日生成次数已达上限（{effective_limit} 次），请明日再试。")

    global_calls = await get_global_calls_today()
    if global_calls >= s.daily_global_max_calls:
        raise HTTPException(429, "今日平台生成总量已达上限，请明日再试。")

    return usage


async def record_usage(user_id: int, action: str, credits_used: int = 0) -> None:
    """记录一次 AI 调用。"""
    today = date.today().isoformat()
    db = await get_db()

    mv_increment = ", mv_count = mv_count + 1" if action == "mv" else ""
    await db.execute(
        f"UPDATE daily_usage SET ai_calls_count = ai_calls_count + 1 {mv_increment} WHERE user_id = ? AND usage_date = ?",
        (user_id, today),
    )

    mv_flag = 1 if action == "mv" else 0
    await db.execute(
        "INSERT INTO global_daily_stats (stat_date, total_ai_calls, total_mv_count) VALUES (?, 1, ?) ON CONFLICT(stat_date) DO UPDATE SET total_ai_calls = total_ai_calls + 1, total_mv_count = total_mv_count + ?",
        (today, mv_flag, mv_flag),
    )
    await db.commit()


async def get_global_calls_today() -> int:
    today = date.today().isoformat()
    db = await get_db()
    cur = await db.execute("SELECT total_ai_calls FROM global_daily_stats WHERE stat_date = ?", (today,))
    row = await cur.fetchone()
    return row["total_ai_calls"] if row else 0


async def get_balance(user_id: int) -> dict:
    return {"balance": 0, "available": 0, "reserved": 0, "lifetime": 0}


async def reserve_credits(user_id: int, amount: int, task_id: str) -> bool:
    return True


async def commit_reserved(user_id: int, amount: int, task_id: str) -> None:
    pass


async def rollback_reserved(user_id: int, amount: int, task_id: str) -> None:
    pass


async def check_mv_cost(user_id: int) -> tuple[bool, int]:
    s = get_settings()
    today = date.today().isoformat()
    db = await get_db()
    override = await _get_user_quota_override(user_id)
    mv_limit = override.get("daily_mv_limit") or s.daily_mv_slots
    cur = await db.execute("SELECT mv_count FROM daily_usage WHERE user_id = ? AND usage_date = ?", (user_id, today))
    row = await cur.fetchone()
    mv_used = row["mv_count"] if row else 0
    return (mv_used < mv_limit), 0


async def add_bonus_generation(user_id: int) -> None:
    """为用户今日增加一次 bonus 生成次数。"""
    today = date.today().isoformat()
    db = await get_db()
    await db.execute(
        "INSERT INTO daily_usage (user_id, usage_date, ai_calls_count, credits_granted, credits_used, mv_count, bonus_generations) "
        "VALUES (?, ?, 0, 0, 0, 0, 1) "
        "ON CONFLICT(user_id, usage_date) DO UPDATE SET bonus_generations = bonus_generations + 1",
        (user_id, today),
    )
    await db.commit()


def generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:16]}"
