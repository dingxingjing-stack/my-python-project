"""崩溃恢复机制 — 服务重启后自动恢复未完成任务。

功能：
1. 启动时扫描 queued/processing 且超时的任务 → 标记 failed
2. 后台 asyncio task 每 5 分钟巡检 processing 超 30 分钟的任务
3. 清理过期幂等键
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from loguru import logger

from app.config import get_settings
from app.database import get_db
from app.services.task_state_machine import TaskStatus, transition
from app.services.idempotency import cleanup_expired


# 巡检间隔（秒）
_RECOVERY_INTERVAL = 300  # 5 分钟


async def run_startup_recovery() -> int:
    """启动时恢复：扫描所有未完成任务，超时则标记失败并退款。

    返回恢复的任务数量。
    """
    db = await get_db()
    now = datetime.utcnow().isoformat()

    # 查找 queued/processing 且已超时的任务
    cur = await db.execute(
        """SELECT * FROM generation_tasks
           WHERE status IN ('queued', 'processing')
             AND timeout_at IS NOT NULL
             AND timeout_at < ?""",
        (now,),
    )
    stale_tasks = await cur.fetchall()

    recovered = 0
    for task in stale_tasks:
        task_id = task["task_id"]
        user_id = task["user_id"]

        try:
            await transition(task_id, TaskStatus.FAILED, error="Service restart — task timed out")
            recovered += 1
            logger.info("[Recovery] Recovered stale task: {} (user={})", task_id, user_id)
        except Exception as exc:
            logger.warning("[Recovery] Failed to recover task {}: {}", task_id, exc)

    s = get_settings()
    timeout_threshold = (
        datetime.utcnow() - __import__("datetime").timedelta(minutes=s.task_timeout_minutes)
    ).isoformat()

    cur2 = await db.execute(
        """SELECT * FROM generation_tasks
           WHERE status = 'processing'
             AND updated_at < ?""",
        (timeout_threshold,),
    )
    long_tasks = await cur2.fetchall()

    for task in long_tasks:
        task_id = task["task_id"]
        if task["timeout_at"] and task["timeout_at"] < now:
            continue

        try:
            await transition(task_id, TaskStatus.FAILED, error="Processing timeout (>30 min)")
            recovered += 1
            logger.info("[Recovery] Recovered long-processing task: {}", task_id)
        except Exception as exc:
            logger.warning("[Recovery] Failed to recover task {}: {}", task_id, exc)

    # 清理过期幂等键
    cleaned = await cleanup_expired()
    if cleaned:
        logger.info("[Recovery] Cleaned {} expired idempotency keys", cleaned)

    if recovered:
        logger.info("[Recovery] Startup recovery complete: {} tasks recovered", recovered)
    else:
        logger.info("[Recovery] Startup recovery: no stale tasks found")

    return recovered


async def periodic_recovery_loop() -> None:
    """后台定时巡检任务（每 5 分钟）。"""
    while True:
        try:
            await asyncio.sleep(_RECOVERY_INTERVAL)
            db = await get_db()
            s = get_settings()
            now = datetime.utcnow().isoformat()
            timeout_threshold = (
                datetime.utcnow() - __import__("datetime").timedelta(minutes=s.task_timeout_minutes)
            ).isoformat()

            # 查找超时任务
            cur = await db.execute(
                """SELECT * FROM generation_tasks
                   WHERE status IN ('queued', 'processing')
                     AND (
                       (timeout_at IS NOT NULL AND timeout_at < ?)
                       OR (status = 'processing' AND updated_at < ?)
                     )""",
                (now, timeout_threshold),
            )
            stale_tasks = await cur.fetchall()

            for task in stale_tasks:
                task_id = task["task_id"]
                try:
                    await transition(task_id, TaskStatus.FAILED, error="Periodic recovery — timeout")
                    logger.info("[Recovery] Periodic: recovered task {}", task_id)
                except Exception as exc:
                    logger.warning("[Recovery] Periodic: failed to recover {}: {}", task_id, exc)

            # 清理过期幂等键
            await cleanup_expired()

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("[Recovery] Periodic loop error: {}", exc)
            await asyncio.sleep(60)  # 出错后等 1 分钟再试
