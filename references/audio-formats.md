# 音频格式对比 + 转码参数

## 格式对比

| 格式 | 用途 | 优点 | 缺点 | 推荐场景 |
|---|---|---|---|---|
| **FLAC** | 原始上传 | 无损，开源 | 文件大（20-30MB/首） | 用户上传原始文件 |
| **MP3 320k** | 备用下载 | 兼容性好，音质接近无损 | 有损，文件较大（10MB/首） | 付费用户下载 |
| **HLS (m3u8)** | **在线播放（首选）** | 自适应码率，断点续传，CDN 友好 | 需要转码 | 所有在线播放 |
| **AAC** | 移动端 | 比 MP3 效率高 30% | 有专利 | iOS 备用 |
| **OGG** | 开源场景 | 免费，音质好 | 兼容性差 | 不推荐 |
| **WAV** | 临时处理 | 无损，无压缩 | 超大（50MB+/首） | 不存储，仅处理用 |

## Cloudflare Stream 转码规范

### 自动转码（推荐）
上传原始文件到 Stream 后，**自动生成**：
- HLS 多码率：
  - `360p` (64k audio + 240p video，如果有 MV)
  - `480p` (128k audio)
  - `720p` (320k audio)
- 封面缩略图：多种尺寸（160x160, 320x320, 640x640）
- 波形数据：JSON 格式

### 手动转码参数（如果不用 Stream）
用 FFmpeg 转 HLS：

```bash
# 生成 HLS（多码率）
ffmpeg -i input.mp3 \
  -c:a aac -b:a 64k -f hls -hls_time 10 -hls_list_size 0 -hls_segment_filename output_%03d.ts output_64k.m3u8 \
  -c:a aac -b:a 128k -f hls -hls_time 10 -hls_list_size 0 -hls_segment_filename output_%03d.ts output_128k.m3u8 \
  -c:a aac -b:a 320k -f hls -hls_time 10 -hls_list_size 0 -hls_segment_filename output_%03d.ts output_320k.m3u8

# 合并为单一 m3u8（带多码率）
cat > output.m3u8 << EOF
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=64000
output_64k.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=128000
output_128k.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=320000
output_320k.m3u8
EOF
```

### 波形生成
用 `audiowaveform`：

```bash
# 生成波形 JSON（256 采样点）
audiowaveform -i input.mp3 -o output.json -z 256 --pixels-per-second 100

# 压缩（gzip）
gzip output.json  # 输出 output.json.gz（节省 70% 空间）
```

前端用 `wavesurfer.js` 加载：
```typescript
import WaveSurfer from "wavesurfer.js";

const wavesurfer = WaveSurfer.create({
  container: "#waveform",
  waveColor: "#4a9eff",
  progressColor: "#1a73e8",
  url: "/api/waveform/123", // 返回 gzip JSON
});

// 后端（Workers）设置响应头
headers.set("Content-Encoding", "gzip");
headers.set("Content-Type", "application/json");
```

## 码率推荐

| 场景 | 推荐码率 | 说明 |
|---|---|---|
| 宽带 + WiFi | 320k | 最佳音质 |
| 4G/5G | 128k | 平衡音质和流量 |
| 弱网（2G/3G） | 64k | 保证能听 |
| 会员下载（高音质） | FLAC 或无损 MP3 320k | 原始质量 |
| 免费用户在线播放 | 128k | 节省流量 |

## 版权相关

### DRM（数字版权管理）
Cloudflare Stream 支持：
- **Signed URLs**：限时播放链接（防盗链）
- **Domain Restriction**：限制播放域名
- **Token Auth**：用户级别鉴权

**不要用**：
- 自行实现 AES-128 加密（Stream 自带）
- 把原始音频 URL 暴露给前端（必须用签名 URL）

### 区域限制
用 Workers 检查请求 IP：
```typescript
// 检查版权区域
const country = c.req.raw.cf?.country;
const blockedCountries = ["CU", "IR", "KP"]; // 美国制裁国家

if (blockedCountries.includes(country)) {
  return c.json({
    success: false,
    error: { code: "REGION_BLOCKED", message: "该地区不可用" }
  }, 403);
}
```

## 存储优化

### R2 生命周期规则
```json
{
  "rules": [
    {
      "id": "delete-old-mp3",
      "status": "enabled",
      "filter": {
        "prefix": "mp3/"
      },
      "expiration": {
        "days": 90  // 90 天没播放就删 MP3 备份
      }
    }
  ]
}
```

### 热数据缓存（KV）
```typescript
// 热门曲目缓存 1 小时
const cacheKey = `track:${trackId}`;
let track = await env.KV.get(cacheKey, "json");

if (!track) {
  track = await db.query.tracks.findFirst({ where: eq(tracks.id, trackId) });
  await env.KV.put(cacheKey, JSON.stringify(track), { expirationTtl: 3600 });
}
```

## 音量标准化（LUFS）

用 `loudnorm` 滤镜（FFmpeg）：
```bash
# 分析音量
ffmpeg -i input.mp3 -af loudnorm=print_format=json -f null -

# 应用标准化（双阶段）
ffmpeg -i input.mp3 -af loudnorm=I=-14:TP=-1.5:LRA=11 -c:a aac output.mp3
```

目标：-14 LUFS（Spotify/Apple Music 标准）

## 元数据解析

用 `music-metadata`（Node.js）：
```typescript
import * as mm from "music-metadata";

const metadata = await mm.parseFile("input.mp3");

console.log({
  title: metadata.common.title,
  artist: metadata.common.artist,
  album: metadata.common.album,
  trackNumber: metadata.common.track.no,
  duration: metadata.format.duration,
  bitrate: metadata.format.bitrate,
  sampleRate: metadata.format.sampleRate,
});
```

存储到 D1：
```sql
INSERT INTO tracks (title, track_number, duration_ms, bitrate, sample_rate)
VALUES (?, ?, ?, ?, ?);
```
