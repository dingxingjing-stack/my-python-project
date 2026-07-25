"""SQLite async database layer with migration support."""
from __future__ import annotations

import os
import pathlib
from datetime import datetime

import aiosqlite

from app.config import get_settings

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Return the shared async connection (lifecycle managed in main.py)."""
    global _db
    if _db is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _db


async def init_db() -> None:
    """Open the database and run pending migrations."""
    global _db

    db_path = _resolve_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row
    # Enable WAL mode for better concurrent read/write
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _run_migrations(_db)


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


def _resolve_path() -> str:
    s = get_settings()
    # Allow override via env, default to ai-service/data/music.db
    env_path = os.getenv("SQLITE_PATH")
    if env_path:
        return env_path
    base = pathlib.Path(__file__).resolve().parent.parent  # ai-service/
    return str(base / "data" / "music.db")


# ---------------------------------------------------------------------------
# Migration runner (up-only)
# ---------------------------------------------------------------------------

_MIGRATIONS = {
    1: """
        CREATE TABLE IF NOT EXISTS artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            bio_md TEXT,
            avatar_url TEXT,
            monthly_listeners INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_artists_slug ON artists(slug);
    """,
    2: """
        CREATE TABLE IF NOT EXISTS albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            slug TEXT NOT NULL,
            release_date TEXT,
            cover_url TEXT,
            total_tracks INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(artist_id, slug)
        );
        CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist_id);
    """,
    3: """
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            track_number INTEGER NOT NULL,
            disc_number INTEGER DEFAULT 1,
            duration_ms INTEGER NOT NULL,
            hls_url TEXT NOT NULL,
            mp3_url TEXT,
            lyrics_lrc TEXT,
            waveform_url TEXT,
            bitrate INTEGER DEFAULT 320,
            sample_rate INTEGER DEFAULT 44100,
            isrc TEXT UNIQUE,
            plays_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_plays ON tracks(plays_count DESC);
    """,
    4: """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password_hash TEXT,
            display_name TEXT NOT NULL,
            avatar_url TEXT,
            premium_tier TEXT DEFAULT 'free' CHECK(premium_tier IN ('free','premium','family')),
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    """,
    5: """
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            is_public INTEGER DEFAULT 1,
            cover_url TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_playlists_user ON playlists(user_id);
    """,
    6: """
        CREATE TABLE IF NOT EXISTS playlist_tracks (
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (playlist_id, track_id)
        );
    """,
    7: """
        CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(title, content='tracks', content_rowid='id');
    """,
    8: """
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, track_id)
        );
    """,
    9: """
        CREATE TABLE IF NOT EXISTS ai_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type TEXT NOT NULL CHECK(type IN ('music_generation','voice_clone','remix','mv_generation','lyrics_generation')),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','failed')),
            input_params TEXT NOT NULL,
            result_data TEXT,
            progress_pct INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_user ON ai_jobs(user_id);
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_jobs(status);
    """,
    10: """
        CREATE TABLE IF NOT EXISTS ai_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES ai_jobs(id),
            track_id INTEGER REFERENCES tracks(id),
            generation_type TEXT NOT NULL CHECK(generation_type IN ('original','cover','remix','clone')),
            original_track_id INTEGER,
            prompt_text TEXT,
            style_tags TEXT,
            model_name TEXT NOT NULL,
            generation_cost REAL,
            is_public INTEGER DEFAULT 1,
            plays_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ai_tracks_job ON ai_tracks(job_id);
    """,
    11: """
        CREATE TABLE IF NOT EXISTS voice_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT,
            sample_track_id INTEGER NOT NULL REFERENCES tracks(id),
            model_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','training','ready','failed')),
            voice_params TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_voice_models_user ON voice_models(user_id);
    """,
    12: """
        -- 一键创作作品表（含音频/视频/歌词完整信息）
        CREATE TABLE IF NOT EXISTS ai_creations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            user_id INTEGER DEFAULT 1,
            prompt_text TEXT,
            title TEXT DEFAULT 'Untitled',
            style_tags TEXT,
            language TEXT DEFAULT 'zh',
            lyrics TEXT,
            lrc TEXT,
            audio_url TEXT,
            cover_url TEXT,
            video_url TEXT,
            duration_ms INTEGER,
            model_name TEXT DEFAULT 'suno-v5',
            is_public INTEGER DEFAULT 1,
            plays_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ai_creations_user ON ai_creations(user_id);
    """,
    13: """
        -- 重建 ai_jobs 表以放宽 type CHECK 约束
        CREATE TABLE IF NOT EXISTS ai_jobs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','failed')),
            input_params TEXT NOT NULL DEFAULT '{}',
            result_data TEXT,
            progress_pct INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        INSERT OR IGNORE INTO ai_jobs_new SELECT * FROM ai_jobs;
        DROP TABLE IF EXISTS ai_jobs;
        ALTER TABLE ai_jobs_new RENAME TO ai_jobs;
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_user ON ai_jobs(user_id);
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_jobs(status);
    """,
    # ── V1.0 新增迁移 ──
    14: """
        -- Credits 余额表
        CREATE TABLE IF NOT EXISTS credits (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            balance INTEGER DEFAULT 10,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """,
    15: """
        -- Credits 交易记录
        CREATE TABLE IF NOT EXISTS credit_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('daily_grant','purchase','referral','lyrics','music','cover','mv','reserve','consumed','refund')),
            description TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_credit_tx_user ON credit_transactions(user_id);
    """,
    16: """
        -- Like 系统
        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            creation_id INTEGER NOT NULL REFERENCES ai_creations(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, creation_id)
        );
        CREATE INDEX IF NOT EXISTS idx_likes_creation ON likes(creation_id);
    """,
    17: """
        -- 分享记录
        CREATE TABLE IF NOT EXISTS shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creation_id INTEGER NOT NULL REFERENCES ai_creations(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            share_code TEXT UNIQUE NOT NULL,
            click_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_shares_code ON shares(share_code);
    """,
    18: """
        -- 邀请好友
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            referred_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            referral_code TEXT NOT NULL,
            reward_claimed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals(referral_code);
    """,
    19: """
        -- 用户设置
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            language TEXT DEFAULT 'en',
            default_song_language TEXT DEFAULT 'auto',
            default_genre TEXT DEFAULT 'pop',
            default_vocal TEXT DEFAULT 'auto',
            default_privacy TEXT DEFAULT 'public',
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """,
    20: """
        -- ai_creations 增加 V1.0 字段
        ALTER TABLE ai_creations ADD COLUMN status TEXT DEFAULT 'completed'
            CHECK(status IN ('draft','completed'));
        ALTER TABLE ai_creations ADD COLUMN share_code TEXT;
        ALTER TABLE ai_creations ADD COLUMN likes_count INTEGER DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_ai_creations_status ON ai_creations(status);
        CREATE INDEX IF NOT EXISTS idx_ai_creations_share_code ON ai_creations(share_code);
    """,
    # ── V1.0 业务表 ──
    21: """
        -- 歌词版本表
        CREATE TABLE IF NOT EXISTS lyrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creation_id INTEGER REFERENCES ai_creations(id) ON DELETE CASCADE,
            user_id INTEGER DEFAULT 1,
            version INTEGER DEFAULT 1,
            title TEXT,
            lyrics_text TEXT NOT NULL,
            lrc_text TEXT,
            prompt_text TEXT,
            style_tags TEXT,
            language TEXT DEFAULT 'zh',
            model_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_lyrics_creation ON lyrics(creation_id);
        CREATE INDEX IF NOT EXISTS idx_lyrics_user ON lyrics(user_id);
    """,
    22: """
        -- 歌曲音频表
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creation_id INTEGER REFERENCES ai_creations(id) ON DELETE CASCADE,
            lyrics_id INTEGER REFERENCES lyrics(id),
            user_id INTEGER DEFAULT 1,
            version INTEGER DEFAULT 1,
            title TEXT,
            audio_url TEXT,
            cover_url TEXT,
            duration_ms INTEGER,
            style_tags TEXT,
            model_name TEXT,
            generation_prompt TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_songs_creation ON songs(creation_id);
    """,
    23: """
        -- 封面图片表
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creation_id INTEGER REFERENCES ai_creations(id) ON DELETE CASCADE,
            user_id INTEGER DEFAULT 1,
            image_url TEXT,
            image_type TEXT DEFAULT 'cover',
            aspect_ratio TEXT DEFAULT '1:1',
            style TEXT,
            prompt_text TEXT,
            model_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_images_creation ON images(creation_id);
    """,
    24: """
        -- MV视频表
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creation_id INTEGER REFERENCES ai_creations(id) ON DELETE CASCADE,
            user_id INTEGER DEFAULT 1,
            video_url TEXT,
            video_type TEXT DEFAULT 'mv',
            storyboard TEXT,
            scenes_data TEXT,
            duration_ms INTEGER,
            style TEXT,
            model_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_videos_creation ON videos(creation_id);
    """,
    25: """
        -- AI任务详细日志表
        CREATE TABLE IF NOT EXISTS generation_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            user_id INTEGER DEFAULT 1,
            task_type TEXT NOT NULL,
            model_name TEXT,
            provider TEXT,
            input_prompt TEXT,
            ai_response TEXT,
            credits_used INTEGER DEFAULT 0,
            elapsed_ms INTEGER DEFAULT 0,
            status TEXT DEFAULT 'completed' CHECK(status IN ('pending','processing','completed','failed')),
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_gen_jobs_user ON generation_jobs(user_id);
        CREATE INDEX IF NOT EXISTS idx_gen_jobs_type ON generation_jobs(task_type);
        CREATE INDEX IF NOT EXISTS idx_gen_jobs_status ON generation_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_gen_jobs_created ON generation_jobs(created_at);
    """,
    26: """
        -- 每日用量追踪表（Credits 重置 + AI 调用次数限制）
        CREATE TABLE IF NOT EXISTS daily_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            usage_date TEXT NOT NULL,
            ai_calls_count INTEGER DEFAULT 0,
            credits_granted INTEGER DEFAULT 0,
            credits_used INTEGER DEFAULT 0,
            mv_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, usage_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_usage_user_date ON daily_usage(user_id, usage_date);
    """,
    27: """
        -- 邀请奖励记录表
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_user_id INTEGER NOT NULL,
            referred_user_id INTEGER NOT NULL,
            reward_credits INTEGER DEFAULT 10,
            rewarded INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_user_id);
        CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_user_id);
    """,
    28: """
        -- 全局每日调用量追踪
        CREATE TABLE IF NOT EXISTS global_daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date TEXT NOT NULL UNIQUE,
            total_ai_calls INTEGER DEFAULT 0,
            total_credits_used INTEGER DEFAULT 0,
            total_mv_count INTEGER DEFAULT 0
        );
    """,
    29: """
        -- 确保默认用户存在（user_id=1）
        INSERT OR IGNORE INTO users (id, email, display_name) VALUES (1, 'demo@avireon.com', 'Demo User');
    """,
    30: """
        -- 补齐 referrals 表可能缺失的列（旧版表已存在但 schema 不同）
        ALTER TABLE referrals ADD COLUMN reward_credits INTEGER DEFAULT 10;
        ALTER TABLE referrals ADD COLUMN rewarded INTEGER DEFAULT 0;
        ALTER TABLE referrals ADD COLUMN completed_at TEXT;
    """,
    31: """
        -- credits 表三层资金字段扩展
        ALTER TABLE credits ADD COLUMN available_credits INTEGER DEFAULT 0;
        ALTER TABLE credits ADD COLUMN reserved_credits INTEGER DEFAULT 0;
        ALTER TABLE credits ADD COLUMN lifetime_credits INTEGER DEFAULT 0;
    """,
    32: """
        -- generation_tasks 状态机表（替代 generation_jobs）
        CREATE TABLE IF NOT EXISTS generation_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL UNIQUE,
            request_id TEXT UNIQUE,
            user_id INTEGER NOT NULL DEFAULT 1,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','completed','failed','cancelled')),
            input_data TEXT,
            output_data TEXT,
            credits_cost INTEGER DEFAULT 0,
            model_name TEXT,
            provider TEXT,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            timeout_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_gen_tasks_user ON generation_tasks(user_id);
        CREATE INDEX IF NOT EXISTS idx_gen_tasks_status ON generation_tasks(status);
        CREATE INDEX IF NOT EXISTS idx_gen_tasks_request ON generation_tasks(request_id);
        CREATE INDEX IF NOT EXISTS idx_gen_tasks_timeout ON generation_tasks(timeout_at);
    """,
    33: """
        -- referrals 表风控字段扩展
        ALTER TABLE referrals ADD COLUMN device_ip TEXT;
        ALTER TABLE referrals ADD COLUMN email_verified INTEGER DEFAULT 0;
        ALTER TABLE referrals ADD COLUMN daily_reward_count INTEGER DEFAULT 0;
        ALTER TABLE referrals ADD COLUMN reward_date TEXT;
    """,
    34: """
        -- 幂等键表（防重复提交）
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            idempotency_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            task_id TEXT,
            response_data TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_idem_expires ON idempotency_keys(expires_at);
    """,
    35: """
        -- admin_config 后台可配置阈值表
        CREATE TABLE IF NOT EXISTS admin_config (
            config_key TEXT PRIMARY KEY,
            config_value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now')),
            updated_by INTEGER DEFAULT 1
        );
        INSERT OR IGNORE INTO admin_config (config_key, config_value) VALUES
            ('daily_global_max_calls', '200'),
            ('daily_max_ai_calls', '10'),
            ('daily_mv_slots', '3'),
            ('daily_free_credits', '10'),
            ('referral_reward_credits', '10'),
            ('referral_daily_reward_limit', '5');
    """,
    36: """
        -- 数据迁移：同步 credits.balance → available_credits / lifetime_credits
        UPDATE credits SET available_credits = balance WHERE available_credits = 0 AND balance > 0;
        UPDATE credits SET lifetime_credits = balance WHERE lifetime_credits = 0 AND balance > 0;
    """,
    37: """
        -- 扩展 credit_transactions.type CHECK 约束（新增 reserve/consumed/refund）
        CREATE TABLE IF NOT EXISTS credit_transactions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('daily_grant','purchase','referral','lyrics','music','cover','mv','reserve','consumed','refund')),
            description TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT OR IGNORE INTO credit_transactions_new SELECT * FROM credit_transactions;
        DROP TABLE IF EXISTS credit_transactions;
        ALTER TABLE credit_transactions_new RENAME TO credit_transactions;
        CREATE INDEX IF NOT EXISTS idx_credit_tx_user ON credit_transactions(user_id);
    """,
    38: """
        ALTER TABLE ai_creations ADD COLUMN is_template INTEGER DEFAULT 0;
        ALTER TABLE ai_creations ADD COLUMN template_category TEXT DEFAULT '';
    """,
    39: """
        ALTER TABLE daily_usage ADD COLUMN bonus_generations INTEGER DEFAULT 0;
    """,
    40: """
        CREATE TABLE IF NOT EXISTS user_quota_overrides (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            daily_ai_calls_limit INTEGER,
            daily_mv_limit INTEGER,
            updated_at TEXT DEFAULT (datetime('now')),
            updated_by INTEGER REFERENCES users(id)
        );
    """,
    41: """
        ALTER TABLE ai_creations ADD COLUMN template_type TEXT DEFAULT '';
        ALTER TABLE ai_creations ADD COLUMN style_tag TEXT DEFAULT '';
    """,
    42: """
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_name TEXT NOT NULL,
            cover_img TEXT DEFAULT '',
            style_tags TEXT DEFAULT '',
            lyric_template TEXT DEFAULT '',
            music_prompt TEXT DEFAULT '',
            mv_prompt TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """,
}


async def _run_migrations(db: aiosqlite.Connection) -> None:
    # Ensure migration table exists
    await db.execute(
        "CREATE TABLE IF NOT EXISTS _migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
    )

    cursor = await db.execute("SELECT MAX(version) FROM _migrations")
    row = await cursor.fetchone()
    current = row[0] or 0

    for ver in sorted(_MIGRATIONS):
        if ver <= current:
            continue
        sql = _MIGRATIONS[ver]
        try:
            await db.executescript(sql)
        except Exception as exc:
            # ALTER TABLE ADD COLUMN 可能因列已存在而失败，跳过继续
            import logging
            logging.getLogger(__name__).warning(
                "Migration %d partial error (continuing): %s", ver, exc
            )
        await db.execute(
            "INSERT OR IGNORE INTO _migrations (version, applied_at) VALUES (?, ?)",
            (ver, datetime.utcnow().isoformat()),
        )
    await db.commit()