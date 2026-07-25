# API 设计详细规范

## 基础 URL
- 生产：`https://api.your-music-site.com`
- 本地 Workers：`http://localhost:8787`

## 版本控制
所有 API 带版本号：`/api/v1/...`

## 响应格式

### 成功
```json
{
  "success": true,
  "data": {...},
  "meta": {
    "request_id": "req_abc123",
    "timestamp": "2026-07-03T17:00:00Z"
  }
}
```

### 错误
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "TRACK_NOT_FOUND",
    "message": "曲目不存在",
    "details": {...}
  }
}
```

### 列表（带分页）
```json
{
  "success": true,
  "data": [...],
  "pagination": {
    "next_cursor": "eyJpZCI6MTIzfQ==",
    "has_more": true,
    "total": 1234
  }
}
```

## 错误码规范

| HTTP 状态码 | error.code | 说明 |
|---|---|---|
| 400 | VALIDATION_ERROR | 参数错误 |
| 401 | UNAUTHORIZED | 未登录 |
| 403 | FORBIDDEN | 无权限 |
| 404 | NOT_FOUND | 资源不存在 |
| 409 | CONFLICT | 冲突（如重复收藏） |
| 429 | RATE_LIMITED | 请求过于频繁 |
| 500 | INTERNAL_ERROR | 服务器错误 |

## 核心端点

### 曲目

#### 获取曲目详情
```
GET /api/v1/tracks/:id
```
响应：
```json
{
  "success": true,
  "data": {
    "id": 123,
    "title": "Song Name",
    "track_number": 1,
    "duration_ms": 210000,
    "hls_url": "https://stream.example.com/...",
    "mp3_url": "https://r2.example.com/...",
    "lyrics_lrc": "[00:00.00]...",
    "waveform_url": "https://cdn.example.com/waveforms/123.json",
    "plays_count": 123456,
    "album": {
      "id": 45,
      "title": "Album Name",
      "cover_url": "..."
    },
    "artists": [
      {"id": 7, "name": "Artist Name", "slug": "artist-name"}
    ]
  }
}
```

#### 搜索曲目
```
GET /api/v1/tracks?q=keyword&cursor=...&limit=20
```
用 FTS5 搜索：
```sql
SELECT tracks.*, artists.name as artist_name
FROM tracks_fts
JOIN tracks ON tracks_fts.rowid = tracks.id
JOIN albums ON tracks.album_id = albums.id
JOIN artists ON albums.artist_id = artists.id
WHERE tracks_fts MATCH :query
ORDER BY rank
LIMIT :limit;
```

#### 记录播放
```
POST /api/v1/tracks/:id/play
```
Body：
```json
{
  "play_duration_ms": 120000,
  "completed": false,
  "client_timestamp": "2026-07-03T17:00:00Z"
}
```

### 播放列表

#### 获取用户播放列表
```
GET /api/v1/playlists?user_id=:userId
```

#### 创建播放列表
```
POST /api/v1/playlists
```
Body：
```json
{
  "name": "My Favorite Songs",
  "is_public": true,
  "cover_url": "..."
}
```

#### 添加曲目到播放列表
```
POST /api/v1/playlists/:playlistId/tracks
```
Body：
```json
{
  "track_id": 123,
  "position": 0  -- 插入位置，0 表示最前面
}
```

### 用户

#### 注册
```
POST /api/v1/auth/register
```
Body：
```json
{
  "email": "user@example.com",
  "password": "secure_password",
  "display_name": "User Name"
}
```
用 Better Auth 处理，不要自己实现密码哈希。

#### 登录
```
POST /api/v1/auth/login
```
Body：
```json
{
  "email": "user@example.com",
  "password": "secure_password"
}
```

#### 获取当前用户
```
GET /api/v1/auth/session
```
需要鉴权（HttpOnly cookie）。

### 流媒体签名 URL

#### 获取播放 URL
```
POST /api/v1/stream/:trackId
```
Body：
```json
{
  "quality": "auto"  -- auto | 64 | 128 | 320
}
```
响应：
```json
{
  "success": true,
  "data": {
    "url": "https://videodelivery.net/.../manifest(video+audio).m3u8?...",
    "expires_at": "2026-07-03T17:10:00Z",
    "quality": "128k"
  }
}
```

签名逻辑（Workers 端）：
```typescript
// Cloudflare Stream 签名
const streamUrl = await env.STREAM.sign({
  id: track.streamId,
  exp: Math.floor(Date.now() / 1000) + 600, // 10 分钟有效
  allowedOrigins: [corsOrigin],
});

// R2 签名（备用 MP3）
const mp3Url = await getSignedUrl(
  env.R2,
  track.mp3Key,
  { expiresIn: 3600 } // 1 小时
);
```

## 鉴权（Better Auth）

### 配置（Workers 端）
```typescript
import { betterAuth } from "better-auth";
import { CloudflareWorkersRequest } from "better-auth/cloudflare";

export const auth = betterAuth({
  database: {
    db: env.DB, // D1
    type: "sqlite",
  },
  emailAndPassword: {
    enabled: true,
  },
  socialProviders: {
    github: {
      clientId: env.GITHUB_CLIENT_ID,
      clientSecret: env.GITHUB_CLIENT_SECRET,
    },
    google: {
      clientId: env.GOOGLE_CLIENT_ID,
      clientSecret: env.GOOGLE_CLIENT_SECRET,
    },
  },
  session: {
    expiresIn: 60 * 60 * 24 * 7, // 7 天
    updateAge: 60 * 60 * 24, // 1 天内不刷新
  },
});
```

### 中间件（Hono）
```typescript
// 鉴权中间件
app.use("/api/v1/*", async (c, next) => {
  const session = await auth.api.getSession({
    headers: c.req.raw.headers,
  });

  if (!session) {
    return c.json({
      success: false,
      error: { code: "UNAUTHORIZED", message: "请先登录" }
    }, 401);
  }

  c.set("user", session.user);
  await next();
});
```

## 限流（Workers Rate Limiting）

```typescript
import { Ratelimit } from "@upstash/ratelimit";

const ratelimit = new Ratelimit({
  redis: env.KV, // 或 Upstash Redis
  limiter: Ratelimit.slidingWindow(100, "1 h"), // 免费用户 100 次/小时
});

app.use("/api/v1/*", async (c, next) => {
  const user = c.get("user");
  const limit = user?.premium_tier === "free" ? 100 : 1000;
  
  const { success } = await ratelimit.limit(
    `${user.id}:${c.req.path}`,
    { limit }
  );

  if (!success) {
    return c.json({
      success: false,
      error: { code: "RATE_LIMITED", message: "请求过于频繁" }
    }, 429);
  }

  await next();
});
```

## 类型定义（前后端共享）

```typescript
// packages/shared/src/types.ts

export interface Track {
  id: number;
  title: string;
  trackNumber: number;
  durationMs: number;
  hlsUrl: string;
  mp3Url?: string;
  lyricsLrc?: string;
  waveformUrl?: string;
  playsCount: number;
  album: Album;
  artists: Artist[];
}

export interface Album {
  id: number;
  title: string;
  slug: string;
  releaseDate?: string;
  coverUrl?: string;
  totalTracks: number;
  durationMs: number;
}

export interface Artist {
  id: number;
  name: string;
  slug: string;
  bioMd?: string;
  avatarUrl?: string;
  monthlyListeners: number;
}

export interface Playlist {
  id: number;
  name: string;
  isPublic: boolean;
  coverUrl?: string;
  tracks: Track[];
  userId: number;
}

export interface User {
  id: number;
  email?: string;
  phone?: string;
  displayName: string;
  avatarUrl?: string;
  premiumTier: "free" | "premium" | "family";
}
```
