"""Admin 后台管理 API 路由 — V2.0 扩展。

新增：
- 任务状态分布统计
- Credits 收支流水 + 当日资金变动
- 全局 AI 调用限额实时进度
- 邀请奖励统计
- 在线修改全局阈值（实时生效）
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard")
async def admin_dashboard():
    """管理员仪表盘 — 完整统计。"""
    db = await get_db()
    s = get_settings()
    today = date.today().isoformat()

    # 用户统计
    cur = await db.execute("SELECT COUNT(*) FROM users")
    total_users = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM users WHERE created_at >= date('now')")
    new_users_today = (await cur.fetchone())[0]

    # 作品统计
    cur = await db.execute("SELECT COUNT(*) FROM ai_creations WHERE status = 'completed'")
    total_creations = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM ai_creations WHERE created_at >= date('now') AND status = 'completed'")
    creations_today = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM ai_creations WHERE video_url IS NOT NULL AND video_url != ''")
    mv_count = (await cur.fetchone())[0]

    # ── AI 任务统计（generation_jobs 表） ──
    cur = await db.execute("SELECT COUNT(*) FROM generation_jobs WHERE created_at >= date('now')")
    ai_calls_today = (await cur.fetchone())[0]
    cur = await db.execute(
        """SELECT task_type, COUNT(*) as cnt
           FROM generation_jobs WHERE created_at >= date('now') GROUP BY task_type"""
    )
    ai_stats_today = {r["task_type"]: {"count": r["cnt"]} for r in await cur.fetchall()}
    cur = await db.execute("SELECT COUNT(*) FROM generation_jobs WHERE created_at >= date('now') AND status = 'failed'")
    failed_today = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM generation_jobs WHERE created_at >= date('now') AND status = 'completed'")
    generations_today = (await cur.fetchone())[0] or 0
    cur = await db.execute("SELECT COUNT(*) FROM generation_jobs")
    total_ai_jobs = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM generation_jobs WHERE status = 'failed'")
    total_failed = (await cur.fetchone())[0]

    # ── 任务状态分布（generation_tasks 表 — V2.0 状态机） ──
    task_status_dist = {}
    try:
        cur = await db.execute(
            """SELECT status, COUNT(*) as cnt FROM generation_tasks
               WHERE created_at >= date('now') GROUP BY status"""
        )
        task_status_dist = {r["status"]: r["cnt"] for r in await cur.fetchall()}
    except Exception:
        pass  # 表可能还未创建

    # ── 每日用量统计 ──
    cur = await db.execute(
        "SELECT SUM(ai_calls_count), SUM(mv_count) FROM daily_usage WHERE usage_date = ?",
        (today,),
    )
    daily_row = await cur.fetchone()
    daily_calls = daily_row[0] or 0

    daily_mv = daily_row[1] or 0

    # ── 全局 AI 调用进度 ──
    cur = await db.execute(
        "SELECT total_ai_calls FROM global_daily_stats WHERE stat_date = ?", (today,)
    )
    global_row = await cur.fetchone()
    global_calls = global_row["total_ai_calls"] if global_row else 0

    # ── admin_config 当前配置 ──
    config_values = {}
    try:
        cur = await db.execute("SELECT config_key, config_value FROM admin_config")
        config_values = {r["config_key"]: r["config_value"] for r in await cur.fetchall()}
    except Exception:
        pass

    return {
        "users": {"total": total_users, "today": new_users_today},
        "creations": {"total": total_creations, "today": creations_today, "with_mv": mv_count},
        "ai_tasks": {
            "total": total_ai_jobs, "today": ai_calls_today,
            "failed_today": failed_today, "total_failed": total_failed,
            "generations_today": generations_today,
            "by_type": ai_stats_today,
        },
        "task_status": task_status_dist,
        "daily_limits": {
            "calls_today": daily_calls,
            "mv_today": daily_mv,
            "global_calls": global_calls,
            "global_limit": int(config_values.get("daily_global_max_calls", s.daily_global_max_calls)),
            "user_limit": int(config_values.get("daily_max_ai_calls", s.daily_max_ai_calls)),
            "mv_limit": int(config_values.get("daily_mv_slots", s.daily_mv_slots)),
        },
        "config": config_values,
    }


# ---------------------------------------------------------------------------
# 在线修改全局阈值
# ---------------------------------------------------------------------------


class ConfigUpdate(BaseModel):
    value: str


@router.put("/config/{key}")
async def update_config(key: str, body: ConfigUpdate):
    """在线修改全局阈值，实时生效。"""
    allowed_keys = {
        "daily_global_max_calls", "daily_max_ai_calls", "daily_mv_slots",
    }
    if key not in allowed_keys:
        raise HTTPException(400, f"Config key '{key}' not modifiable")

    db = await get_db()
    await db.execute(
        """INSERT INTO admin_config (config_key, config_value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(config_key) DO UPDATE SET config_value = ?, updated_at = datetime('now')""",
        (key, body.value, body.value),
    )
    await db.commit()
    return {"success": True, "key": key, "value": body.value}


@router.get("/config")
async def get_config():
    """获取当前所有配置。"""
    db = await get_db()
    cur = await db.execute("SELECT config_key, config_value, updated_at FROM admin_config")
    rows = await cur.fetchall()
    return {r["config_key"]: {"value": r["config_value"], "updated_at": r["updated_at"]} for r in rows}


# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------


@router.get("/users")
async def admin_list_users(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
    q: str = Query(default=None),
):
    """用户列表。"""
    db = await get_db()
    where = ""
    params: list = []
    if q:
        where = "WHERE u.email LIKE ? OR u.display_name LIKE ?"
        params = [f"%{q}%", f"%{q}%"]

    cur = await db.execute(
        f"""
        SELECT u.id, u.email, u.display_name, u.premium_tier, u.created_at
        FROM users u
        {where}
        ORDER BY u.created_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    )
    rows = await cur.fetchall()
    items = [dict(r) for r in rows]

    cnt = await db.execute(f"SELECT COUNT(*) FROM users {where}", params)
    total = (await cnt.fetchone())[0]

    return {
        "items": items,
        "pagination": {"total": total, "limit": limit, "offset": offset},
    }


class QuotaOverride(BaseModel):
    daily_ai_calls_limit: Optional[int] = None
    daily_mv_limit: Optional[int] = None
    daily_heavy_feature_calls: Optional[int] = None


@router.post("/users/{user_id}/quota")
async def admin_set_user_quota(user_id: int, body: QuotaOverride):
    """设置用户每日限额覆盖。"""
    db = await get_db()
    await db.execute(
        """INSERT INTO user_quota_overrides (user_id, daily_ai_calls_limit, daily_mv_limit, daily_heavy_feature_calls, updated_at, updated_by)
           VALUES (?, ?, ?, ?, datetime('now'), 1)
           ON CONFLICT(user_id) DO UPDATE SET
               daily_ai_calls_limit = COALESCE(?, daily_ai_calls_limit),
               daily_mv_limit = COALESCE(?, daily_mv_limit),
               daily_heavy_feature_calls = COALESCE(?, daily_heavy_feature_calls),
               updated_at = datetime('now')""",
        (user_id, body.daily_ai_calls_limit, body.daily_mv_limit, body.daily_heavy_feature_calls,
         body.daily_ai_calls_limit, body.daily_mv_limit, body.daily_heavy_feature_calls),
    )
    await db.commit()
    return {"success": True}


@router.delete("/users/{user_id}")
async def admin_delete_user(user_id: int):
    """删除用户。"""
    db = await get_db()
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# 任务管理
# ---------------------------------------------------------------------------


@router.get("/jobs")
async def admin_list_jobs(
    limit: int = Query(default=30, le=100),
    offset: int = Query(default=0),
    status: str = Query(default=None),
):
    """任务列表（优先查 generation_tasks，回退 ai_jobs）。"""
    db = await get_db()
    where = ""
    params: list = []
    if status:
        where = "WHERE status = ?"
        params = [status]

    # 优先用 generation_tasks 表
    try:
        cur = await db.execute(
            f"""SELECT id, task_id, user_id, task_type as type, status, credits_cost,
                       error_message, created_at, completed_at
                FROM generation_tasks {where}
                ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = await cur.fetchall()
        items = [dict(r) for r in rows]
        cnt = await db.execute(f"SELECT COUNT(*) FROM generation_tasks {where}", params)
        total = (await cnt.fetchone())[0]
    except Exception:
        # 回退到旧表
        cur = await db.execute(
            f"""SELECT id, user_id, type, status, progress_pct, error_message, created_at, completed_at
                FROM ai_jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = await cur.fetchall()
        items = [dict(r) for r in rows]
        cnt = await db.execute(f"SELECT COUNT(*) FROM ai_jobs {where}", params)
        total = (await cnt.fetchone())[0]

    return {
        "items": items,
        "pagination": {"total": total, "limit": limit, "offset": offset},
    }


@router.get("/quotas")
async def admin_list_quotas(limit: int = Query(default=20, le=100), offset: int = Query(default=0)):
    db = await get_db()
    cur = await db.execute(
        "SELECT uo.*, u.email, u.display_name FROM user_quota_overrides uo LEFT JOIN users u ON uo.user_id = u.id ORDER BY uo.updated_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cur.fetchall()
    return {"items": [dict(r) for r in rows]}
