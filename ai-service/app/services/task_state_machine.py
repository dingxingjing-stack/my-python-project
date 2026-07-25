"""任务状态机 — generation_tasks 表状态流转管控。

状态枚举：queued → processing → completed / failed / cancelled

合法流转：
  queued      → processing  (开始执行)
  queued      → failed      (超时回收)
  queued      → cancelled   (用户取消)
  processing  → completed   (执行成功)
  processing  → failed      (执行失败 / 超时)
  其他流转一律拒绝（409 Conflict）

幂等保障：
  task_id UNIQUE → 数据库兜底防重复创建
  request_id UNIQUE → 前端幂等 Key 防重复提交
"""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from fastapi import HTTPException
from loguru import logger

from app.config import get_settings
from app.database import get_db


# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 合法流转映射
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.PROCESSING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.PROCESSING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


# ---------------------------------------------------------------------------
# 状态机核心操作
# ---------------------------------------------------------------------------


async def create_task(
    user_id: int,
    task_type: str,
    task_id: str,
    *,
    request_id: Optional[str] = None,
    input_data: str = "",
    credits_cost: int = 0,
    model_name: str = "",
    provider: str = "",
) -> dict:
    """创建新任务（status=queued）。

    如果 request_id 已存在，直接返回已有任务（幂等）。
    """
    db = await get_db()

    # 幂等检查：同一 request_id 只创建一次
    if request_id:
        cur = await db.execute(
            "SELECT * FROM generation_tasks WHERE request_id = ?",
            (request_id,),
        )
        existing = await cur.fetchone()
        if existing:
            logger.info("[StateMachine] Idempotent hit: request_id={} task_id={}", request_id, existing["task_id"])
            return dict(existing)

    # 计算超时时间
    s = get_settings()
    timeout_at = (datetime.utcnow() + timedelta(minutes=s.task_timeout_minutes)).isoformat()

    await db.execute(
        """INSERT INTO generation_tasks
           (task_id, request_id, user_id, task_type, status,
            input_data, credits_cost, model_name, provider, timeout_at)
           VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
        (task_id, request_id, user_id, task_type,
         input_data[:5000], credits_cost, model_name, provider, timeout_at),
    )
    await db.commit()

    logger.info("[StateMachine] Created task: id={} type={} user={}", task_id, task_type, user_id)

    cur = await db.execute(
        "SELECT * FROM generation_tasks WHERE task_id = ?", (task_id,)
    )
    row = await cur.fetchone()
    return dict(row)


async def transition(task_id: str, new_status: TaskStatus, *, error: str = "") -> dict:
    """执行状态流转。非法流转抛 409。"""
    db = await get_db()

    cur = await db.execute(
        "SELECT * FROM generation_tasks WHERE task_id = ?", (task_id,)
    )
    task = await cur.fetchone()
    if not task:
        raise HTTPException(404, f"Task {task_id} not found")

    current = TaskStatus(task["status"])
    if new_status not in _VALID_TRANSITIONS[current]:
        raise HTTPException(
            409,
            f"Illegal transition: {current.value} → {new_status.value}",
        )

    now = datetime.utcnow().isoformat()
    completed_at = now if new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED) else None

    await db.execute(
        """UPDATE generation_tasks
           SET status = ?, updated_at = ?, completed_at = ?, error_message = ?
           WHERE task_id = ?""",
        (new_status.value, now, completed_at, error[:500] if error else None, task_id),
    )

    # 更新 output_data（如果有）
    await db.commit()

    logger.info("[StateMachine] Transition: {} {} → {}", task_id, current.value, new_status.value)

    cur = await db.execute(
        "SELECT * FROM generation_tasks WHERE task_id = ?", (task_id,)
    )
    row = await cur.fetchone()
    return dict(row)


async def update_task_output(task_id: str, output_data: str) -> None:
    """更新任务输出数据。"""
    db = await get_db()
    await db.execute(
        "UPDATE generation_tasks SET output_data = ?, updated_at = datetime('now') WHERE task_id = ?",
        (output_data[:10000], task_id),
    )
    await db.commit()


async def get_task(task_id: str) -> Optional[dict]:
    """获取任务详情。"""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM generation_tasks WHERE task_id = ?", (task_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_task_by_request(request_id: str) -> Optional[dict]:
    """通过 request_id 获取任务。"""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM generation_tasks WHERE request_id = ?", (request_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_task_status_distribution() -> dict:
    """获取当前所有任务状态分布统计。"""
    db = await get_db()
    cur = await db.execute(
        """SELECT status, COUNT(*) as cnt
           FROM generation_tasks
           WHERE created_at >= date('now')
           GROUP BY status"""
    )
    rows = await cur.fetchall()
    return {row["status"]: row["cnt"] for row in rows}


async def get_user_first_completed_task(user_id: int) -> Optional[dict]:
    """获取用户首条已完成的任务（用于邀请奖励判定）。"""
    db = await get_db()
    cur = await db.execute(
        """SELECT * FROM generation_tasks
           WHERE user_id = ? AND status = 'completed'
           ORDER BY completed_at ASC LIMIT 1""",
        (user_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None
