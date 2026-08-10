"""每日生成次数限流 — 简化为纯计数系统（支持双服务商独立额度）。"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException
from loguru import logger

from app.config import get_settings
from app.database import get_db

# 有效服务商列表（local_gateway 为本地免费网关，不参与额度统计）
VALID_PROVIDERS = ("siliconflow", "openrouter", "local_gateway")


async def ensure_provider_table() -> None:
    """确保 provider_daily_usage 表存在。"""
    db = await get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS provider_daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            call_count INTEGER DEFAULT 0,
            UNIQUE(provider, usage_date)
        )
    """)
    await db.commit()


async def _migrate_daily_usage() -> None:
    """迁移 daily_usage 表，添加 heavy_calls_count 列（幂等）。"""
    db = await get_db()
    try:
        await db.execute("ALTER TABLE daily_usage ADD COLUMN heavy_calls_count INTEGER DEFAULT 0")
        await db.commit()
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE user_quota_overrides ADD COLUMN daily_heavy_feature_calls INTEGER")
        await db.commit()
    except Exception:
        pass


async def ensure_provider_daily_reset(provider: str) -> dict:
    """确保今日某服务商的用量记录存在。"""
    assert provider in VALID_PROVIDERS, f"Unknown provider: {provider}"
    today = date.today().isoformat()
    db = await get_db()
    await ensure_provider_table()

    cur = await db.execute(
        "SELECT * FROM provider_daily_usage WHERE provider = ? AND usage_date = ?",
        (provider, today),
    )
    row = await cur.fetchone()
    if row:
        return dict(row)

    await db.execute(
        "INSERT INTO provider_daily_usage (provider, usage_date, call_count) VALUES (?, ?, 0)",
        (provider, today),
    )
    await db.commit()
    return {"provider": provider, "usage_date": today, "call_count": 0}


async def check_provider_daily_limits(provider: str) -> dict:
    """检查该服务商今日调用是否已达上限。本地网关（local_gateway）免费，不限额度。"""
    s = get_settings()
    if provider == "local_gateway":
        today = date.today().isoformat()
        return {"provider": provider, "usage_date": today, "call_count": 0}
    limit = (
        s.daily_siliconflow_calls if provider == "siliconflow"
        else s.daily_openrouter_calls
    )
    usage = await ensure_provider_daily_reset(provider)
    if usage["call_count"] >= limit:
        raise HTTPException(
            429,
            f"服务商 {provider} 今日调用已达上限（{limit} 次），请明日再试。",
        )
    return usage


async def record_provider_usage(provider: str) -> None:
    """记录一次服务商调用。"""
    today = date.today().isoformat()
    db = await get_db()
    await db.execute(
        "INSERT INTO provider_daily_usage (provider, usage_date, call_count) "
        "VALUES (?, ?, 1) ON CONFLICT(provider, usage_date) "
        "DO UPDATE SET call_count = call_count + 1",
        (provider, today),
    )
    await db.commit()


async def ensure_daily_reset(user_id: int) -> dict:
    """确保今日 daily_usage 记录存在。"""
    s = get_settings()
    today = date.today().isoformat()
    db = await get_db()

    # 迁移：确保 heavy_calls_count 列存在
    await _migrate_daily_usage()

    cur = await db.execute(
        "SELECT * FROM daily_usage WHERE user_id = ? AND usage_date = ?",
        (user_id, today),
    )
    row = await cur.fetchone()
    if row:
        return dict(row)

    await db.execute(
        "INSERT INTO daily_usage (user_id, usage_date, ai_calls_count, credits_granted, credits_used, mv_count, heavy_calls_count) VALUES (?, ?, 0, 0, 0, 0, 0)",
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
    """检查用户今日是否还能调用 AI。返回用量信息或抛出 429 异常。

    支持分级限流：
      - light 类功能走通用上限 daily_max_ai_calls
      - heavy 类功能额外受 daily_heavy_feature_calls 限制
    """
    from app.services.feature_flags import rate_tier

    s = get_settings()
    today = date.today().isoformat()
    usage = await ensure_daily_reset(user_id)
    override = await _get_user_quota_override(user_id)

    tier = rate_tier(action)

    # 重型功能单独计数
    if tier == "heavy":
        heavy_limit = override.get("daily_heavy_feature_calls") or s.daily_heavy_feature_calls
        heavy_used = usage.get("heavy_calls_count", 0)
        if heavy_used >= heavy_limit:
            raise HTTPException(429, f"今日重型功能调用次数已达上限（{heavy_limit} 次），请明日再试。")

    base_limit = override.get("daily_ai_calls_limit") or s.daily_max_ai_calls
    effective_limit = base_limit + (usage.get("bonus_generations", 0) or 0)
    if usage["ai_calls_count"] >= effective_limit:
        raise HTTPException(429, f"今日生成次数已达上限（{effective_limit} 次），请明日再试。")

    global_calls = await get_global_calls_today()
    if global_calls >= s.daily_global_max_calls:
        raise HTTPException(429, "今日平台生成总量已达上限，请明日再试。")

    return usage


async def record_usage(user_id: int, action: str, credits_used: int = 0) -> None:
    """记录一次 AI 调用。

    自动识别轻重等级：heavy 功能额外计入 heavy_calls_count。
    """
    from app.services.feature_flags import rate_tier

    today = date.today().isoformat()
    await ensure_daily_reset(user_id)
    db = await get_db()

    heavy_inc = ""
    if rate_tier(action) == "heavy":
        heavy_inc = ", heavy_calls_count = COALESCE(heavy_calls_count, 0) + 1"

    await db.execute(
        f"UPDATE daily_usage SET ai_calls_count = ai_calls_count + 1 {heavy_inc} WHERE user_id = ? AND usage_date = ?",
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


async def record_mv_usage(user_id: int) -> None:
    """记录一次 MV 生成（独立计入 mv_count + ai_calls_count + global）。"""
    today = date.today().isoformat()
    await ensure_daily_reset(user_id)
    db = await get_db()
    await db.execute(
        "UPDATE daily_usage SET mv_count = mv_count + 1, ai_calls_count = ai_calls_count + 1 "
        "WHERE user_id = ? AND usage_date = ?",
        (user_id, today),
    )
    await db.execute(
        "INSERT INTO global_daily_stats (stat_date, total_ai_calls, total_mv_count) "
        "VALUES (?, 1, 1) ON CONFLICT(stat_date) DO UPDATE SET "
        "total_ai_calls = total_ai_calls + 1, total_mv_count = total_mv_count + 1",
        (today,),
    )
    await db.commit()


async def check_mv_daily_limits(user_id: int) -> None:
    """检查用户今日 MV 生成次数是否已达上限（daily_mv_slots / override），超限抛 429。"""
    s = get_settings()
    today = date.today().isoformat()
    await ensure_daily_reset(user_id)
    db = await get_db()
    override = await _get_user_quota_override(user_id)
    mv_limit = override.get("daily_mv_limit") or s.daily_mv_slots
    cur = await db.execute(
        "SELECT mv_count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
        (user_id, today),
    )
    row = await cur.fetchone()
    mv_used = row["mv_count"] if row else 0
    if mv_used >= mv_limit:
        raise HTTPException(429, f"今日 MV 生成次数已达上限（{mv_limit} 次），请明日再试。")


async def add_bonus_generation(user_id: int) -> None:
    """为用户今日增加一次 bonus 生成次数。"""
    today = date.today().isoformat()
    db = await get_db()
    await db.execute(
        "INSERT INTO daily_usage (user_id, usage_date, ai_calls_count, credits_granted, credits_used, mv_count, heavy_calls_count, bonus_generations) "
        "VALUES (?, ?, 0, 0, 0, 0, 0, 1) "
        "ON CONFLICT(user_id, usage_date) DO UPDATE SET bonus_generations = bonus_generations + 1",
        (user_id, today),
    )
    await db.commit()


def generate_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:16]}"
