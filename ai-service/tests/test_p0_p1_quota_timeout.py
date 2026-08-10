"""
P0/P1 修复回归测试
===================
覆盖（严格对应 2026-08-09 已批准范围）：
  1. Lyrics 429 不得被 Mock 吞掉
  2. Music / MV 完善额度检查与 record_usage
  3. Music / MV 增加总超时和 job 终止机制
  4. MV 正确使用用户已生成的 audio_url
  5. 以上修复的回归验证

说明：
  - 全部测试使用临时 SQLite（SQLITE_PATH 指向 tmp），不碰 data/music.db。
  - 每个测试前重建 schema 并清空用量表，保证计数断言确定性。
  - 仅测内部函数/路由内部逻辑，不触发任何外部 AI/Modal 调用。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

# 测试隔离：在导入 app 前把 DB 指向临时目录
_TMP_DIR = tempfile.mkdtemp(prefix="avireon-test-")
os.environ["SQLITE_PATH"] = str(Path(_TMP_DIR) / "test.db")
os.environ["MOCK_FALLBACK"] = "true"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException, Request as StarletteRequest

from app.database import init_db, get_db, close_db
from app.services.usage_tracker import (
    ensure_daily_reset,
    check_daily_limits,
    record_usage,
    check_mv_daily_limits,
    record_mv_usage,
    get_global_calls_today,
)


def _make_request(body: dict) -> StarletteRequest:
    """构造一个最小 Starlette Request，仅用于 request.json()。"""
    scope = {
        "type": "http", "method": "POST", "path": "/api/v1/ai/lyrics",
        "headers": [], "query_string": b"", "client": ("t", 1),
        "server": ("t", 80), "scheme": "http",
    }
    import json as _json
    payload = _json.dumps(body).encode("utf-8")

    class FakeReceive:
        async def __call__(self):
            return {"type": "http.request", "body": payload}

    return StarletteRequest(scope, receive=FakeReceive())


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """每个测试前重建临时数据库并清空用量表。"""
    await init_db()
    db = await get_db()
    for tbl in ("daily_usage", "global_daily_stats", "generation_jobs"):
        try:
            await db.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    await db.commit()
    yield
    await close_db()


@pytest_asyncio.fixture
async def user():
    """确保 user_id=1 存在。"""
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO users (id, email, display_name) VALUES (?, ?, ?)",
        (1, "test@avireon.com", "TestUser"),
    )
    await db.commit()
    return 1


# ===========================================================================
# 1. Lyrics 429 不得被 Mock 吞掉
# ===========================================================================


class TestLyrics429NotMocked:
    @pytest.mark.asyncio
    async def test_lyrics_quota_exceeded_raises_429(self, user):
        """额度耗尽时 check_daily_limits 必须抛 429，而不是静默放行。"""
        db = await get_db()
        import datetime
        today = datetime.date.today().isoformat()
        await ensure_daily_reset(user)
        await db.execute(
            "UPDATE daily_usage SET ai_calls_count = 10 WHERE user_id = ? AND usage_date = ?",
            (user, today),
        )
        await db.commit()

        with pytest.raises(HTTPException) as exc_info:
            await check_daily_limits(user, "ai_lyrics")
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_lyrics_route_429_not_replaced_by_mock(self, monkeypatch, user):
        """路由层：scheduler 抛 HTTPException(429) 时必须原样透传，不得落入 Mock 兜底。"""
        from app.routes.ai import lyrics as lyrics_route

        class FakeScheduler:
            async def generate_lyrics(self, **kwargs):
                raise HTTPException(429, "今日生成次数已达上限")

        monkeypatch.setattr(lyrics_route, "get_scheduler", lambda: FakeScheduler())

        req = _make_request({"prompt": "test song", "style": "pop", "language": "zh", "user_id": user})
        # require_feature 包装后仍可调用；429 会向上抛出而非返回 mock
        with pytest.raises(HTTPException) as exc_info:
            await lyrics_route.generate_lyrics(req)
        assert exc_info.value.status_code == 429
        assert "上限" in exc_info.value.detail


# ===========================================================================
# 2. Music / MV 完善额度检查与 record_usage
# ===========================================================================


class TestMusicUsageTracking:
    @pytest.mark.asyncio
    async def test_music_check_daily_limits_429(self, user):
        """音乐生成：超上限抛 429。"""
        db = await get_db()
        import datetime
        today = datetime.date.today().isoformat()
        await ensure_daily_reset(user)
        await db.execute(
            "UPDATE daily_usage SET ai_calls_count = 10 WHERE user_id = ? AND usage_date = ?",
            (user, today),
        )
        await db.commit()
        with pytest.raises(HTTPException) as exc_info:
            await check_daily_limits(user, "ai_music")
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_record_usage_increments(self, user):
        """record_usage 后 daily_usage.ai_calls_count 增加。"""
        before = await get_global_calls_today()
        await record_usage(user, "ai_music", 0)
        db = await get_db()
        import datetime
        today = datetime.date.today().isoformat()
        cur = await db.execute(
            "SELECT ai_calls_count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user, today),
        )
        row = await cur.fetchone()
        assert row["ai_calls_count"] >= 1
        assert await get_global_calls_today() >= before + 1


class TestMVUsageTracking:
    @pytest.mark.asyncio
    async def test_mv_daily_limits_429(self, user):
        """MV 额度耗尽时抛 429。"""
        db = await get_db()
        import datetime
        today = datetime.date.today().isoformat()
        await ensure_daily_reset(user)
        await db.execute(
            "UPDATE daily_usage SET mv_count = 3 WHERE user_id = ? AND usage_date = ?",
            (user, today),
        )
        await db.commit()
        with pytest.raises(HTTPException) as exc_info:
            await check_mv_daily_limits(user)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_mv_record_usage_increments_both(self, user):
        """record_mv_usage 同时增加 mv_count 与 ai_calls_count。"""
        await ensure_daily_reset(user)
        global_before = await get_global_calls_today()
        await record_mv_usage(user)
        db = await get_db()
        import datetime
        today = datetime.date.today().isoformat()
        cur = await db.execute(
            "SELECT mv_count, ai_calls_count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user, today),
        )
        row = await cur.fetchone()
        assert row["mv_count"] == 1
        assert row["ai_calls_count"] == 1
        assert await get_global_calls_today() >= global_before + 1

    @pytest.mark.asyncio
    async def test_mv_first_call_creates_daily_row(self, user):
        """首日无 daily_usage 行时，record_mv_usage 也应正确计数（回归：ensure_daily_reset）。"""
        await record_mv_usage(user)
        db = await get_db()
        import datetime
        today = datetime.date.today().isoformat()
        cur = await db.execute(
            "SELECT mv_count, ai_calls_count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user, today),
        )
        row = await cur.fetchone()
        assert row["mv_count"] == 1
        assert row["ai_calls_count"] == 1


# ===========================================================================
# 3. Music / MV 总超时与 job 终止
# ===========================================================================


class TestMusicJobTimeout:
    @pytest.mark.asyncio
    async def test_run_generation_timeout_marks_failed(self, monkeypatch):
        """总超时后 job 置 failed。"""
        import app.routes.ai.music as m

        job_id = uuid.uuid4().hex[:8]
        m._job_store[job_id] = {
            "status": "processing", "progress": 5,
            "result": None, "error": None, "started_at": time.time(),
        }

        async def _fake_inner(*args, **kwargs):
            await asyncio.sleep(10)  # 永不返回，触发超时

        monkeypatch.setattr(m, "_run_generation_inner", _fake_inner)
        monkeypatch.setattr(m, "MUSIC_TIMEOUT_SECS", 0.1)
        monkeypatch.setattr(m, "_write_music_job_status", lambda *a, **k: None)

        await m._run_generation(job_id, None)
        assert m._job_store[job_id]["status"] == "failed"
        assert "超时" in m._job_store[job_id]["error"]

    @pytest.mark.asyncio
    async def test_get_job_status_stale_processing_failed(self, monkeypatch):
        """轮询侧：processing 超过总超时 → 返回 failed（避免前端永久转圈）。"""
        import app.routes.ai.music as m

        job_id = uuid.uuid4().hex[:8]
        old_start = time.time() - m.MUSIC_TIMEOUT_SECS - 10
        monkeypatch.setattr(
            m,
            "_read_music_job_status_file",
            lambda _j: {"status": "processing", "progress": 50, "started_at": old_start,
                        "result": None, "error": None},
        )
        monkeypatch.setattr(m, "_write_music_job_status", lambda *a, **k: None)

        resp = await m.get_job_status(job_id)
        assert resp["status"] == "failed"
        assert "超时" in resp["error"]


class TestMVJobTimeout:
    @pytest.mark.asyncio
    async def test_mv_run_job_timeout_marks_failed(self, monkeypatch):
        """MV job 总超时 → failed 并回写 SQLite。"""
        import app.routes.ai.mv as m

        job_id = uuid.uuid4().hex[:8]
        m._mv_job_store[job_id] = {
            "status": "processing", "progress": 5,
            "result": None, "error": None, "started_at": time.time(),
        }

        async def _fake_gen(*args, **kwargs):
            await asyncio.sleep(10)

        monkeypatch.setattr(m, "_generate_mv_from_lyrics", _fake_gen)
        monkeypatch.setattr(m, "MV_TIMEOUT_SECS", 0.1)
        monkeypatch.setattr(m, "_write_job_status", lambda *a, **k: None)

        db = await get_db()
        await db.execute(
            "INSERT INTO generation_jobs (job_id, user_id, task_type, status) VALUES (?, ?, ?, ?)",
            (job_id, 1, "mv_full", "processing"),
        )
        await db.commit()

        await m._run_mv_job(job_id, 1, "lyrics", "title", "Cinematic", 1, None)
        assert m._mv_job_store[job_id]["status"] == "failed"
        assert "超时" in m._mv_job_store[job_id]["error"]

        cur = await db.execute(
            "SELECT status FROM generation_jobs WHERE job_id = ?", (job_id,)
        )
        row = await cur.fetchone()
        assert row["status"] == "failed"

    @pytest.mark.asyncio
    async def test_mv_poll_stale_processing_failed(self, monkeypatch):
        """MV 轮询：状态文件 processing 超时 → failed。"""
        import app.routes.ai.mv as m

        job_id = uuid.uuid4().hex[:8]
        old_start = time.time() - m.MV_TIMEOUT_SECS - 10
        monkeypatch.setattr(
            m,
            "_read_job_status_file",
            lambda _j: {"status": "processing", "progress": 50, "started_at": old_start,
                        "result": None, "error": None},
        )
        monkeypatch.setattr(m, "_write_job_status", lambda *a, **k: None)

        resp = await m.get_mv_job_status(job_id)
        assert resp["status"] == "failed"
        assert "超时" in resp["error"]


# ===========================================================================
# 4. MV 正确使用用户已生成的 audio_url
# ===========================================================================


class FakeMVSched:
    """可控的 mv_scheduler 假实现。"""
    def __init__(self, recorder: dict):
        self._recorder = recorder

    async def generate_scene_image(self, *a, **k):
        return "/uploads/img1.jpg", "flux"

    async def generate_music(self, *a, **k):
        self._recorder["generate_music_called"] = True
        return "/uploads/fallback.mp3", "kokoro"

    gpu_quota_exhausted = False


class FakeStoryResult:
    text = '[{"scene":1,"description":"d","image_prompt":"p"}]'
    model_name = "fake"


class FakeScheduler:
    async def generate_mv_storyboard(self, **kwargs):
        return FakeStoryResult()


class TestMVAudioUrlPriority:
    @pytest.mark.asyncio
    async def test_audio_url_passed_through_generate_mv_full(self, monkeypatch):
        """路由提交时把 audio_url 透传给后台任务。"""
        import app.routes.ai.mv as m

        captured = {}

        def _fake_spawn(job_id, user_id, lyrics, title, mv_style, num_scenes, creation_id, audio_url=""):
            captured["audio_url"] = audio_url
            return True  # 假装 Modal spawn 成功，避免本地起任务

        monkeypatch.setattr(m, "_spawn_modal_mv_job", _fake_spawn)

        req = _make_request({
            "lyrics": "line1", "title": "T", "style": "Cinematic",
            "num_scenes": 1, "user_id": 1, "audio_url": "/uploads/audio/x.mp3",
        })
        resp = await m.generate_mv_full(req)
        assert "job_id" in resp
        assert captured.get("audio_url") == "/uploads/audio/x.mp3"

    @pytest.mark.asyncio
    async def test_generate_mv_uses_user_song_when_audio_url_given(self, monkeypatch):
        """给 audio_url 时直接用，不再调 mv_sched.generate_music。"""
        import app.routes.ai.mv as m

        recorder = {"generate_music_called": False}
        fake_sched = FakeMVSched(recorder)

        async def _fake_compose(*a, **k):
            return "/uploads/out.mp4"

        monkeypatch.setattr(m, "_compose_mv", _fake_compose)
        monkeypatch.setattr("app.services.mv_scheduler.get_mv_scheduler", lambda: fake_sched)
        monkeypatch.setattr(m, "get_local_storage", lambda: FakeStorage())

        result = await m._generate_mv_from_lyrics(
            lyrics="line1\nline2", title="T", mv_style="Cinematic",
            num_scenes=1, user_id=1, creation_id=None, scheduler=FakeScheduler(),
            audio_url="/uploads/audio/song.mp3",
        )
        assert result == "/uploads/out.mp4"
        assert recorder["generate_music_called"] is False

    @pytest.mark.asyncio
    async def test_generate_mv_falls_back_when_no_audio_url(self, monkeypatch):
        """无 audio_url 时才走 mv_sched.generate_music 兜底。"""
        import app.routes.ai.mv as m

        recorder = {"generate_music_called": False}
        fake_sched = FakeMVSched(recorder)

        async def _fake_compose(*a, **k):
            return "/uploads/out.mp4"

        monkeypatch.setattr(m, "_compose_mv", _fake_compose)
        monkeypatch.setattr("app.services.mv_scheduler.get_mv_scheduler", lambda: fake_sched)
        monkeypatch.setattr(m, "get_local_storage", lambda: FakeStorage())

        await m._generate_mv_from_lyrics(
            lyrics="line1\nline2", title="T", mv_style="Cinematic",
            num_scenes=1, user_id=1, creation_id=None, scheduler=FakeScheduler(),
            audio_url="",
        )
        assert recorder["generate_music_called"] is True


class FakeStorage:
    """minimal stand-in for get_local_storage()."""
    _base = Path(_TMP_DIR) / "uploads"
