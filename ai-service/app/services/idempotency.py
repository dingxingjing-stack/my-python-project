"""幂等性保障 — 防重复提交。

前端请求 Header 携带 X-Idempotency-Key（UUID），
后端用 SQLite INSERT OR IGNORE 模拟 SETNX（无需 Redis）。
60 秒有效期，过期自动清理。

同一 request_id 无论多少次重复请求：
  - 仅创建 1 条任务
  - 仅预扣 1 次 Credits
  - 直接返回缓存响应
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from app.database import get_db


# 幂等键有效期（秒）
_IDEMPOTENCY_TTL = 60


async def check_idempotency(
    idempotency_key: str,
    user_id: int = 1,
) -> Optional[dict]:
    """检查幂等键是否已存在。

    返回:
      - None: 新请求，可以继续
      - dict: 已有响应，直接返回给客户端
    """
    if not idempotency_key:
        return None

    db = await get_db()

    # 清理过期键
    await db.execute(
        "DELETE FROM idempotency_keys WHERE expires_at < datetime('now')",
    )

    cur = await db.execute(
        "SELECT * FROM idempotency_keys WHERE idempotency_key = ?",
        (idempotency_key,),
    )
    row = await cur.fetchone()

    if row:
        logger.info("[Idempotency] Hit: key={} task_id={}", idempotency_key, row["task_id"])
        import json
        return json.loads(row["response_data"]) if row["response_data"] else {"task_id": row["task_id"], "cached": True}

    return None


async def register_idempotency(
    idempotency_key: str,
    user_id: int,
    task_id: str,
    response_data: Optional[dict] = None,
) -> None:
    """注册幂等键（INSERT OR IGNORE 防并发重复）。"""
    if not idempotency_key:
        return

    db = await get_db()
    expires_at = (datetime.utcnow() + timedelta(seconds=_IDEMPOTENCY_TTL)).isoformat()

    import json
    await db.execute(
        """INSERT OR IGNORE INTO idempotency_keys
           (idempotency_key, user_id, task_id, response_data, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            idempotency_key,
            user_id,
            task_id,
            json.dumps(response_data) if response_data else None,
            expires_at,
        ),
    )
    await db.commit()
    logger.info("[Idempotency] Registered: key={} task={}", idempotency_key, task_id)


async def update_idempotency_response(
    idempotency_key: str,
    response_data: dict,
) -> None:
    """任务完成后更新幂等键的缓存响应。"""
    if not idempotency_key:
        return

    db = await get_db()
    import json
    await db.execute(
        """UPDATE idempotency_keys
           SET response_data = ?
           WHERE idempotency_key = ?""",
        (json.dumps(response_data), idempotency_key),
    )
    await db.commit()


async def cleanup_expired() -> int:
    """清理过期幂等键，返回清理数量。"""
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM idempotency_keys WHERE expires_at < datetime('now')"
    )
    await db.commit()
    return cur.rowcount if cur.rowcount else 0
