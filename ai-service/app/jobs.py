"""In-memory async job registry.

A job is the unit of work for all AI generation operations. The Python
service runs each job in an asyncio background task and stores status in
memory. The frontend polls `/api/v1/ai/job/{job_id}` for status.

For a production deployment with multiple replicas, replace this with
Redis; the API surface is intentionally minimal to keep the swap easy.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from loguru import logger

from app.models.schemas import JobStatus


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status.value,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
        }


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex)
        self._jobs[job.id] = job
        logger.info("Created job id={}", job.id)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def all(self) -> Dict[str, Job]:
        return dict(self._jobs)

    # ------------------------------------------------------------------
    # Background task management
    # ------------------------------------------------------------------

    def schedule(self, job: Job, coro) -> None:
        task = asyncio.create_task(self._run(job, coro))
        self._tasks[job.id] = task

    async def _run(self, job: Job, coro) -> None:
        try:
            await coro
        except Exception as exc:
            logger.exception("Job {} failed", job.id)
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._tasks.pop(job.id, None)


registry = JobRegistry()
