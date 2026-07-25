---
name: music-website
description: 大型音乐网站全栈开发规范（Cloudflare 部署）。当用户开发音乐网站、播放器、音频处理、版权相关代码时加载。涵盖 Next.js + Workers + R2 + D1 技术栈、音频处理、API 设计、AI 行为准则。
---

# 大型音乐网站开发规范（Cloudflare 方案）

## 🎯 技术栈（已确认）

- **全栈框架**：FastAPI（异步 Python）+ Jinja2 + HTMX + Alpine.js
- **数据库**：SQLite（开发/小规模）+ aiosqlite（异步驱动）
- **文件存储**：S3 兼容（Cloudflare R2 或 MinIO）+ boto3
- **音频处理**：FFmpeg（HLS 转码/波形）+ pydub
- **搜索**：SQLite FTS5 全文搜索
- **鉴权**：FastAPI Users（JWT + OAuth + 邮箱注册）
- **缓存**：redis-py / aiocache（可选，SQLite 够用先不加）
- **AI 服务层**：内建于同一 FastAPI 进程（/internal/ai/* 端点 + /api/v1/ai/* 端点）
- **前端**：Jinja2 模板 + HTMX（局部刷新）+ Alpine.js（交互）+ Tailwind CSS
- **部署**：单容器/Railway/Fly.io/VPS，入口 uvicorn

> **完整 Python 方案**。不再依赖 Cloudflare Workers/Pages/Stream/KV/Durable Objects。前端和后端同在一个 FastAPI 进程中，无 Node.js/TypeScript，无前端构建步骤。

## 🤖 AI 功能架构（Python 服务层）

### 核心能力

| 功能 | 说明 | 推荐方案 | 部署方式 |
|---|---|---|---|
| **AI 音乐生成** | 文本提示 → 原创歌曲（旋律+编曲+人声） | Suno API / Udio API / MusicGen | 第三方 API 调用 |
| **AI 声音克隆** | 上传 30s 样本 → 合成任意文本为歌声 | RVC v2 / GPT-SoVITS | 云 GPU（AutoDL/HuggingFace） |
| **AI MV 生成** | 音频+提示词 → 视频画面（同步节拍） | Stable Video Diffusion / Runway Gen-3 / Pika | 第三方 API 调用 |
| **AI 二创/Remix** | 上传歌曲 → 风格迁移/重新编排/变速变调 | MusicGen + Demucs 分离音轨 | 混合（API + 本地 GPU） |
| **AI 歌词生成** | 主题/风格 → 歌词（纯文本 LLM） | GPT-4o / Claude / DeepSeek | 第三方 API 调用 |

### 架构拓扑

```
                    用户浏览器
                         │ HTTPS
                         ▼
 ┌───────────────────────────────────────────────────────────────┐
 │                 FastAPI 单体服务（全栈）                        │
 │                                                               │
 │  ┌─────────────────────────────────────────────────────────┐  │
 │  │  Jinja2 + HTMX 前端                                       │  │
 │  │  ├─ 首页 / 发现 / 播放器 / 用户曲库                       │  │
 │  │  ├─ /create/   AI 创作面板（文本提示 / 样本上传）         │  │
 │  │  ├─ /admin/    管理后台                                   │  │
 │  │  └─ 模板文件：templates/{pages,components}/              │  │
 │  ├─────────────────────────────────────────────────────────┤  │
 │  │  REST API (/api/v1/*)                                    │  │
 │  │  ├─ /api/v1/tracks/...     曲目 CRUD + 搜索 + 签名流     │  │
 │  │  ├─ /api/v1/playlists/...  播放列表                      │  │
 │  │  ├─ /api/v1/auth/...       鉴权 (JWT)                    │  │
 │  │  ├─ /api/v1/stream/:id     获取限时签名播放 URL           │  │
 │  │  └─ /api/v1/users/...      用户资料                      │  │
 │  ├─────────────────────────────────────────────────────────┤  │
 │  │  AI API (/api/v1/ai/*)                                  │  │
 │  │  ├─ /api/v1/ai/generate         AI 音乐生成              │  │
 │  │  ├─ /api/v1/ai/clone-voice      声音克隆                 │  │
 │  │  ├─ /api/v1/ai/remix            二创/Remix               │  │
 │  │  ├─ /api/v1/ai/generate-mv      MV 生成                  │  │
 │  │  ├─ /api/v1/ai/lyrics           歌词生成                 │  │
 │  │  ├─ /api/v1/ai/job/:jobId       任务状态查询              │  │
 │  │  └─ /api/v1/ai/jobs             历史任务列表              │  │
 │  ├─────────────────────────────────────────────────────────┤  │
 │  │  业务服务层 (app/services/)                              │  │
 │  │  ├─ suno_client.py       → Suno API                      │  │
 │  │  ├─ deepseek_client.py   → DeepSeek/OpenAI 歌词          │  │
 │  │  ├─ sovits_engine.py     → GPT-SoVITS 推理               │  │
 │  │  ├─ runway_client.py     → Runway MV 生成                │  │
 │  │  └─ r2_uploader.py       → Cloudflare R2 / MinIO 上传    │  │
 │  ├─────────────────────────────────────────────────────────┤  │
 │  │  SQLite (aiosqlite)                                      │  │
 │  │  ├─ tracks, albums, artists (核心曲目)                   │  │
 │  │  ├─ users, playlists, favorites (用户/社交)              │  │
 │  │  ├─ ai_jobs, ai_tracks, voice_models (AI 记录)          │  │
 │  │  └─ 全文搜索：FTS5 (tracks_fts + albums_fts)            │  │
 │  └─────────────────────────────────────────────────────────┘  │
 │                                                               │
 │  📦 部署：Railway / Fly.io / VPS / Docker                     │
 │  💾 存储：Cloudflare R2 (S3 兼容) 或 MinIO 自建               │
 │  🎵 音频：FFmpeg 本地转码 → R2/MinIO → 签名 URL               │
 │  ⚡ AI：asyncio 后台任务 + /api/v1/ai/job/:id 轮询           │
 └───────────────────────────────────────────────────────────────┘
```

### 异步任务模型（所有 AI 操作统一使用）

用户请求 → FastAPI 创建 job → asyncio.create_task 启动后台协程 →
调用 AI API/模型 → 结果上传 R2/MinIO → 更新 job status (内存 registry) → 前端轮询完成

**job 状态机**：`pending` → `processing` → `completed` / `failed`

**前端交互**：
1. 用户提交创作请求 → 立即返回 `job_id`
2. 前端每 3 秒轮询 `GET /api/v1/ai/job/:jobId`
3. `completed` → 展示结果（音频 URL + 封面 + 歌词）
4. `failed` → 展示错误信息 + 重试按钮

### AI 生成记录数据表（新增 D1 schema）

```sql
-- AI 创作任务
CREATE TABLE ai_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK(type IN (
    'music_generation',   -- AI 生成原创歌曲
    'voice_clone',        -- 声音克隆 + 合成
    'remix',              -- 二创/Remix
    'mv_generation',      -- MV 生成
    'lyrics_generation'   -- 纯歌词生成
  )),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
  -- 输入参数（JSON，具体字段依赖 type）
  input_params TEXT NOT NULL,  -- JSON: { "prompt": "...", "style": "pop", ... }
  -- 输出结果（JSON，完成后填充）
  result_data TEXT,            -- JSON: { "hls_url": "...", "cover_url": "...", ... }
  progress_pct INTEGER DEFAULT 0,  -- 0-100 进度
  error_message TEXT,         -- 失败时的错误信息
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  completed_at TEXT
);

CREATE INDEX idx_ai_jobs_user ON ai_jobs(user_id);
CREATE INDEX idx_ai_jobs_status ON ai_jobs(status);

-- AI 生成的作品（生成成功后自动注册到曲目系统）
CREATE TABLE ai_tracks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES ai_jobs(id),
  track_id INTEGER REFERENCES tracks(id),  -- 关联到主 tracks 表
  generation_type TEXT NOT NULL
    CHECK(generation_type IN ('original', 'cover', 'remix', 'clone')),
  original_track_id INTEGER,  -- 二创/翻唱的原曲（如果有）
  prompt_text TEXT,           -- 原始创作提示词
  style_tags TEXT,            -- JSON: ["pop", "electronic", "chill"]
  model_name TEXT NOT NULL,   -- 使用的模型名称
  generation_cost REAL,       -- API 调用成本（美元）
  is_public INTEGER DEFAULT 1,
  plays_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_ai_tracks_job ON ai_tracks(job_id);
CREATE INDEX idx_ai_tracks_track ON ai_tracks(track_id);
CREATE INDEX idx_ai_tracks_type ON ai_tracks(generation_type);

-- 声音模型（用户克隆的声音）
CREATE TABLE voice_models (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,            -- 声音名称
  description TEXT,              -- 描述
  sample_track_id INTEGER NOT NULL REFERENCES tracks(id),
  model_url TEXT NOT NULL,       -- .pth/.onnx 模型文件 R2 URL
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending', 'training', 'ready', 'failed')),
  voice_params TEXT,             -- JSON: { "pitch": 0, "speed": 1.0, ... }
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_voice_models_user ON voice_models(user_id);
```

### AI API 端点设计

```
# AI 创作（异步）
POST   /api/v1/ai/generate          # 生成原创音乐
POST   /api/v1/ai/clone-voice       # 上传样本 → 启动训练 → 合成
POST   /api/v1/ai/remix             # 二创/Remix
POST   /api/v1/ai/generate-mv       # MV 生成
POST   /api/v1/ai/generate-lyrics   # 歌词生成

# 任务查询
GET    /api/v1/ai/job/:jobId        # 查询任务状态
GET    /api/v1/ai/jobs              # 用户历史任务列表（cursor分页）
DELETE /api/v1/ai/job/:jobId        # 取消排队中的任务

# 作品库
GET    /api/v1/ai/tracks            # AI 生成作品列表
GET    /api/v1/ai/voices            # 用户克隆声音列表
DELETE /api/v1/ai/voices/:voiceId   # 删除声音模型
```

### 费用控制策略

| 用户等级 | AI 生成配额 | 声音克隆配额 | MV 生成配额 |
|---|---|---|---|
| free | 3 次/月 | 1 次/月 | 0 次 | 
| premium | 50 次/月 | 5 次/月 | 10 次/月 |
| family | 100 次/月 | 10 次/月 | 20 次/月 |

- Workers 中间件校验配额（D1 `ai_generation_quota` 表）
- 每次 AI 请求扣除配额，超出返回 429
- 成本记录到 `ai_tracks.generation_cost`，用于计算 ROI

### Python 服务结构

```
ai-service/
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 环境变量（API keys/model paths）
│   ├── routes/
│   │   ├── music.py            # 音乐生成路由
│   │   ├── voice.py            # 声音克隆路由
│   │   ├── remix.py            # 二创路由
│   │   ├── mv.py               # MV 生成路由
│   │   └── lyrics.py           # 歌词生成路由
│   ├── services/
│   │   ├── suno_client.py      # Suno API 封装
│   │   ├── udio_client.py      # Udio API 封装
│   │   ├── sovits_engine.py    # GPT-SoVITS 推理封装
│   │   ├── demucs_engine.py    # 音频音轨分离
│   │   ├── musicgen_engine.py  # MusicGen 推理
│   │   ├── mv_service.py       # Runway/Pika API 封装
│   │   ├── lyric_service.py    # LLM API 封装（OpenAI/Claude）
│   │   └── r2_uploader.py      # 结果上传到 R2
│   └── models/
│       └── schemas.py          # Pydantic 请求/响应模型
├── downloads/                  # 模型下载（gitignored）
│   ├── sovits/                 # GPT-SoVITS checkpoint
│   ├── rvc/                    # RVC v2 checkpoint
│   └── musicgen/               # MusicGen checkpoint
├── requirements.txt
├── Dockerfile
└── render.yaml                 # Fly.io / Railway 部署配置
```

### 禁止事项（AI 特供）

- ❌ 不要让前端直连 AI 服务（必须通过 FastAPI 鉴权中转）

## 📁 目录约定
```
music-website/
├── ai-service/                  # FastAPI 全栈单体服务
│   ├── app/
│   │   ├── main.py              # FastAPI 入口 + 路由注册 + 生命周期
│   │   ├── config.py            # Pydantic Settings 环境变量
│   │   ├── jobs.py              # 异步任务 registry（内存）
│   │   ├── database.py          # SQLite (aiosqlite) 连接 + 迁移
│   │   ├── auth.py              # JWT 鉴权 + FastAPI Users
│   │   ├── routes/
│   │   │   ├── tracks.py        # 曲目 CRUD + 搜索 API
│   │   │   ├── playlists.py     # 播放列表 API
│   │   │   ├── auth.py          # 登录/注册/刷新 Token
│   │   │   ├── stream.py        # 签名流 URL 端点
│   │   │   ├── ai/
│   │   │   │   ├── __init__.py  # AI 路由组注册
│   │   │   │   ├── music.py     # AI 音乐生成 + 查询
│   │   │   │   ├── voice.py     # 声音克隆 + 管理
│   │   │   │   ├── lyrics.py    # 歌词生成
│   │   │   │   └── mv.py        # MV 生成
│   │   │   └── users.py         # 用户资料
│   │   ├── services/
│   │   │   ├── suno_client.py   # Suno API 封装
│   │   │   ├── deepseek_client.py  # 歌词 LLM 封装
│   │   │   ├── sovits_engine.py    # GPT-SoVITS 推理
│   │   │   ├── runway_client.py    # Runway MV 生成 API
│   │   │   ├── r2_uploader.py      # Cloudflare R2/MinIO 上传
│   │   │   └── audio.py            # FFmpeg HLS 转码 + 波形
│   │   ├── models/
│   │   │   ├── schemas.py       # Pydantic 请求/响应模型
│   │   │   └── db_models.py     # SQLite 表定义 + aiosqlite 查询
│   │   ├── templates/           # Jinja2 模板
│   │   │   ├── pages/
│   │   │   │   ├── home.html    # 首页（热门/推荐）
│   │   │   │   ├── discover.html
│   │   │   │   ├── player.html  # 播放页面
│   │   │   │   ├── library.html # 用户曲库
│   │   │   │   ├── create.html  # AI 创作面板
│   │   │   │   └── admin.html   # 管理后台
│   │   │   └── components/
│   │   │       ├── track_card.html  # 曲目卡片
│   │   │       ├── player_bar.html  # 底部播放栏
│   │   │       ├── waveform.html    # 波形可视化
│   │   │       └── ai_panel.html    # AI 生成进度/结果
│   │   └── static/
│   │       ├── css/
│   │       │   └── tailwind.css     # Tailwind v4 (CDN)
│   │       └── js/
│   │           ├── player.js        # hls.js 播放器
│   │           ├── ai-poll.js       # AI job 轮询 + HTMX
│   │           └── wavesurfer.js    # 波形库
│   ├── tests/                   # pytest
│   ├── migrations/              # SQL 迁移文件 (YYYYMMDD_name.sql)
│   ├── data/                    # SQLite 数据库文件 (gitignored)
│   │   └── music.db             # 生产默认路径
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── docs/
│   ├── tech-stack.md            # 技术选型 + 版本（自动维护）
│   ├── conventions.md           # 代码规范（自动维护）
│   ├── decisions/               # 重要决策日志
│   ├── gotchas.md               # 踩坑记录
│   └── progress.md              # 当前进度
├── references/                  # 参考规范（SKILL 静态文档）
│   ├── api-design.md
│   ├── audio-formats.md
│   ├── cloudflare-setup.md      # R2 配置（仍需要）
│   ├── db-schema.md
│   └── ai-models.md
├── scripts/                     # 脚本（部署/种子数据/FFmpeg 批处理）
├── AGENTS.md                    # OpenCode 记忆 + AI 行为约束
└── SKILL.md                     # 开放代码 Skill 定义
```

## 🎵 音频处理规范

### 上传流程
1. 用户上传原文件（支持 FLAC/MP3/WAV/OGG）
2. 前端通过 FastAPI POST /api/v1/tracks/upload 直传后端
3. 后端 subprocess 调用 FFmpeg 本地转码：
   - HLS 多码率（64k/128k/320k）→ .m3u8 + .ts segments
   - MP3 320k 备份 → 直接copy
   - 波形 JSON（audiowaveform 或 pydub 采样）
4. 转码产物上传 R2/MinIO，获得 object key
5. 元数据写入 SQLite（`tracks` 表）+ FTS5 索引

### 播放流程
1. 前端请求 GET /api/v1/stream/:trackId
2. FastAPI 验证 JWT 权限（免费/会员/版权区域）
3. 生成**限时签名 URL**（R2/MinIO presigned URL，有效期 1h）
4. 前端用 `hls.js` 或浏览器原生 `<video>` 播放 HLS 流
5. 弱网自动降级到低码率（HLS 自适应）

### 格式支持
- **上传**：FLAC, MP3, WAV, OGG, M4A
- **转码后**：HLS (m3u8) + 320kbps MP3（备份）
- **封面**：WebP 优先，JPEG 备选
- **歌词**：LRC 格式（时间戳 + 纯文本）

### 禁止事项
- ❌ 不要在前端解析大音频文件（太慢，后端 subprocess FFmpeg）
- ❌ 不要把音频文件存数据库，只存 URL
- ❌ 不要绕过 DRM（除非明确说要做盗版站）
- ❌ 不要硬编码音频 URL（必须用签名 URL）

## 🗄️ 数据库核心表（SQLite 语法）

参考 `references/db-schema.md`：

```sql
-- 艺术家
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

-- 专辑
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

-- 曲目
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

-- 全文搜索（D1 原生 FTS5）
CREATE VIRTUAL TABLE tracks_fts USING fts5(
  title,
  content='tracks',
  content_rowid='id'
);

-- 触发器：自动同步 FTS
CREATE TRIGGER tracks_ai AFTER INSERT ON tracks BEGIN
  INSERT INTO tracks_fts(rowid, title) VALUES (new.id, new.title);
END;

-- 用户
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

-- 播放列表
CREATE TABLE playlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  is_public INTEGER DEFAULT 1,
  cover_url TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

-- 播放列表曲目（有序）
CREATE TABLE playlist_tracks (
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  added_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (playlist_id, track_id)
);
```

## 🔌 API 设计

### RESTful 规范
- 资源用复数：`/api/v1/tracks/:id`
- 嵌套资源：`/api/v1/artists/:id/albums`
- 动作用动词：`/api/v1/tracks/:id/play`（记录播放）

### 分页（Cursor 方式，不要用 offset）
```json
{
  "data": [...],
  "next_cursor": "eyJpZCI6MTIzfQ==",
  "has_more": true
}
```

### 响应格式
```json
{
  "success": true,
  "data": {...},
  "error": null
}
```

### 鉴权
- 用 FastAPI Users + JWT（支持 OAuth + 邮箱注册）
- Access Token 存 HttpOnly cookie 或 Authorization: Bearer header
- Refresh Token 存数据库
- FastAPI 依赖注入中间件统一鉴权
- bcrypt 哈希密码（FastAPI Users 内置）

### 限流
- 免费用户：100 次/小时
- 会员：1000 次/小时
- 用 slowapi 中间件实现（FastAPI 兼容）
- Redis backend 可选（规模小直接内存）

## ⚡ 性能优化清单（必做）
- [ ] 音频懒加载，列表页只预加载前 3 首
- [ ] 封面用 WebP 优先，CDN 缓存 30 天
- [ ] 波形 JSON 用 gzip，单文件 < 50KB
- [ ] SQLite 查询强制走索引，禁止 `SELECT *`
- [ ] R2/MinIO 签名 URL 有效期 1 小时（防止盗链）
- [ ] HLS stream 签名 URL 有效期 10 分钟（播放中自动刷新）
- [ ] 列表 API 加内存 lru_cache（TTL 5min）或 Redis

## 🚫 禁区（绝对不要）
- ❌ 不要在前端解析大音频文件
- ❌ 不要把音频文件存数据库，只存 URL
- ❌ 不要用 offset 分页（性能差，用 cursor）
- ❌ 不要明文存用户密码（用 bcrypt 哈希，FastAPI Users 内置）
- ❌ 不要绕过 DRM（除非明确说要做盗版站）
- ❌ 不要硬编码 API URL（用环境变量）
- ❌ 不要直接暴露 R2/MinIO 原始 URL（必须用签名 URL）

---

## 🧠 AI 行为准则（强制执行）

### 1. 节制调用（不要频繁触发 AI）
- 每次只做 **一件事**，做完再问下一件
- 不要主动建议"要不要我再帮你做 X"（等用户说）
- 改代码前 **先读再改**，不要猜
- 如果遇到模糊需求，**只问一个最关键的问题**，不要连环问
- 能用本地工具（grep/find/git diff）搞定的，**不调 AI 工具**
- 读文件用 `read` 工具，**不要粘贴内容到对话里**浪费 token
- 大文件（>500 行）用 `offset` + `limit` 分批读

### 2. 慢一点，稳一点（执行步骤不要太快）
- 每个步骤完成后 **暂停等用户确认**（除非用户说"全部执行"）
- 不要假设用户已理解，改完发一句"我改了 X，你看下"
- 大改动（>50 行）前先说明方案，用户同意再动手
- 如果遇到 3 个以上文件要改，**列清单让用户确认优先级**
- 部署前 **必须** 先在本地跑通所有测试

### 3. 自动纠错
- 每次改完代码，**必须跑一遍相关测试/编译**，有红就修
- 如果连续 2 次改同一处还报错，**停下来说明问题**，不要死循环
- 发现自己的假设错了，**主动承认** + 说明正确做法
- 如果工具报错，**先读完整错误信息**，不要猜原因
- 遇到 CORS/跨域问题，**先检查 Workers 响应头**，不要乱改前端

### 4. 学习 & 记忆（累计记忆）
- 项目根目录 `docs/decisions/` 文件夹必须存在
- 每个重要决策写一条 `YYYYMMDD-<topic>.md`（例如：`20240703-audio-storage-choice.md`）
- 记录格式：
  ```markdown
  # <决策标题>
  ## 背景
  ## 选项对比
  ## 最终选择 + 原因
  ## 后续影响
  ```
- 每次新任务开始，**先扫一遍 `docs/decisions/`** 避免重复讨论
- 用户纠正你时，**立刻记到 `docs/decisions/`**，下次别再犯
- 发现新坑 → 写 `docs/gotchas.md`
- 改了技术选型 → 更新 `docs/tech-stack.md`
- 完成一个里程碑 → 更新 `docs/progress.md`

### 5. 成本意识（针对 API 调用）
- 优先用本地工具（grep/find/git diff）搞定
- 读文件用 `read` 工具，不要粘贴内容到对话里
- 大文件（>500 行）用 `offset` + `limit` 分批读
- 不要为了"展示能力"主动做额外功能
- 用户没说"继续"就不要自作主张

---

## 📚 项目记忆文件（自动维护）

AI 必须在以下路径记录项目上下文，**每次会话开始时读取**：

```
docs/
├── tech-stack.md          # 技术选型 + 版本
├── conventions.md         # 代码规范（命名/格式/禁区）
├── decisions/             # 重要决策日志（见上方格式）
├── gotchas.md             # 踩过的坑 + 解决方案
└── progress.md            # 当前进度 + 下一步
```

### 读取规则
- 新会话开始 → 读 `docs/tech-stack.md` + `docs/conventions.md` + `docs/progress.md`
- 遇到错误 → 先查 `docs/gotchas.md` 有没有记录
- 做完一个功能 → 更新 `docs/progress.md`

### 写入规则  
- 用户说了"记住这个" → 立刻写
- 发现新坑 → 写 `docs/gotchas.md`
- 改了技术选型 → 更新 `docs/tech-stack.md`
- 完成一个里程碑 → 更新 `docs/progress.md`

---

## 🛠️ 常用命令

### 开发
```bash
# 创建虚拟环境
python -m venv .venv

# 激活 (Windows)
.venv\Scripts\activate
# 激活 (Linux/Mac)
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 启动服务（热重载）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 运行测试
pytest
pytest --cov=app
```

### 数据库
```bash
# 初始化数据库 + 运行迁移
python scripts/migrate.py

# 导入种子数据
python scripts/seed.py

# 打开 SQLite shell
sqlite3 data/music.db

# 查看表结构
sqlite3 data/music.db ".schema tracks"

# 查询
sqlite3 data/music.db "SELECT * FROM tracks LIMIT 5;"
```

### 部署
```bash
# Docker 构建
docker build -t music-website .
docker run -p 8000:8000 --env-file .env music-website

# Railway
railway up

# Fly.io
fly deploy
```

---

## 📖 参考文档
- `references/db-schema.md` - 完整数据库 schema（D1 语法）
- `references/api-design.md` - API 设计详细规范
- `references/audio-formats.md` - 音频格式对比 + 转码参数
- `references/cloudflare-setup.md` - Cloudflare 服务配置步骤
- `references/ai-models.md` - AI 模型选型 + API 对比（待创建）
