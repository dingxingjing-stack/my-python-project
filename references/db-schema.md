# 完整数据库 Schema（D1 SQLite 语法）

## 核心表

### artists（艺术家）
```sql
CREATE TABLE artists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  bio_md TEXT,
  avatar_url TEXT,
  monthly_listeners INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_artists_slug ON artists(slug);
CREATE INDEX idx_artists_listeners ON artists(monthly_listeners DESC);
```

### albums（专辑）
```sql
CREATE TABLE albums (
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

CREATE INDEX idx_albums_artist ON albums(artist_id);
CREATE INDEX idx_albums_release ON albums(release_date DESC);
```

### tracks（曲目）
```sql
CREATE TABLE tracks (
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

CREATE INDEX idx_tracks_album ON tracks(album_id);
CREATE INDEX idx_tracks_plays ON tracks(plays_count DESC);
```

### users（用户）
```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE,
  phone TEXT UNIQUE,
  password_hash TEXT,
  display_name TEXT NOT NULL,
  avatar_url TEXT,
  premium_tier TEXT DEFAULT 'free' CHECK(premium_tier IN ('free', 'premium', 'family')),
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_phone ON users(phone);
```

### playlists（播放列表）
```sql
CREATE TABLE playlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  is_public INTEGER DEFAULT 1,
  cover_url TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_playlists_user ON playlists(user_id);
```

### playlist_tracks（播放列表曲目，有序）
```sql
CREATE TABLE playlist_tracks (
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  added_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (playlist_id, track_id)
);

CREATE INDEX idx_playlist_tracks_playlist ON playlist_tracks(playlist_id);
```

## 全文搜索（FTS5）

```sql
-- 创建虚拟表
CREATE VIRTUAL TABLE tracks_fts USING fts5(
  title,
  content='tracks',
  content_rowid='id'
);

-- 自动同步触发器
CREATE TRIGGER tracks_ai AFTER INSERT ON tracks BEGIN
  INSERT INTO tracks_fts(rowid, title) VALUES (new.id, new.title);
END;

CREATE TRIGGER tracks_ad AFTER DELETE ON tracks BEGIN
  INSERT INTO tracks_fts(tracks_fts, rowid, title) VALUES ('delete', old.id, old.title);
END;

CREATE TRIGGER tracks_au AFTER UPDATE ON tracks BEGIN
  INSERT INTO tracks_fts(tracks_fts, rowid, title) VALUES ('delete', old.id, old.title);
  INSERT INTO tracks_fts(rowid, title) VALUES (new.id, new.title);
END;
```

## 播放记录（用于推荐算法）

```sql
CREATE TABLE play_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  played_at TEXT DEFAULT (datetime('now')),
  play_duration_ms INTEGER, -- 实际听了多久
  completed INTEGER DEFAULT 0 -- 是否听完
);

CREATE INDEX idx_play_history_user ON play_history(user_id);
CREATE INDEX idx_play_history_track ON play_history(track_id);
```

## 收藏（喜欢/不喜欢）

```sql
CREATE TABLE favorites (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, track_id)
);
```

## 迁移文件命名规范

```
packages/db/migrations/
├── 0001_initial_schema.sql
├── 0002_add_user_preferences.sql
├── 0003_add_playlists.sql
└── ...
```

用 `wrangler d1 migrations` 命令管理：
```bash
wrangler d1 migrations create music-db "add user preferences"
wrangler d1 migrations apply music-db --local
wrangler d1 migrations apply music-db --remote
```
