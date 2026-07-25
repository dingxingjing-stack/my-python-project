# Cloudflare 服务配置步骤

## 1. 开通服务

### Cloudflare 账号
1. 注册：https://dash.cloudflare.com/sign-up
2. 验证邮箱
3. 添加支付方式（免费额度内不扣费）

### 必需服务
| 服务 | 用途 | 免费额度 | 控制台路径 |
|---|---|---|---|
| **Pages** | 部署前端 | 无限站点，500 次构建/月 | Pages 菜单 |
| **Workers** | 后端 API | 10 万次请求/天 | Workers & Pages → Workers |
| **D1** | 数据库 | 5GB 存储，每天 25 万行读取 | D1 菜单 |
| **R2** | 音频存储 | 10GB 存储，零出口费 | R2 菜单 |
| **Stream** | 音视频转码+播放 | $5/1000 分钟存储 + $1/1000 分钟播放 | Stream 菜单 |
| **KV** | 缓存 | 1GB 存储，10 万次读取/天 | KV 菜单 |
| **Queues** | 异步任务 | 10 万次操作/天 | Queues 菜单 |

## 2. 创建 R2 存储桶

### 控制台操作
1. 进入 **R2** 菜单
2. 点 **Create bucket**
3. 填写：
   - Bucket Name: `music-audio`
   - Location hint: 选离用户最近的（如 `APAC`）
4. 创建后进入 bucket → **Settings** → **CORS Policies** → 添加：
   ```json
   [
     {
       "AllowedOrigins": ["https://your-domain.com"],
       "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
       "AllowedHeaders": ["*"],
       "ExposeHeaders": ["ETag"]
     }
   ]
   ```

### Wrangler CLI（推荐）
```bash
# 安装 Wrangler
npm install -g wrangler

# 登录
wrangler login

# 创建 bucket
wrangler r2 bucket create music-audio
wrangler r2 bucket create music-covers
wrangler r2 bucket create music-waveforms
```

## 3. 创建 D1 数据库

### Wrangler CLI
```bash
# 创建数据库
wrangler d1 create music-db

# 输出示例：
# {
#   "d1_databases": [
#     {
#       "binding": "DB",
#       "database_name": "music-db",
#       "database_id": "xxxx-xxxx-xxxx-xxxx"
#     }
#   ]
# }
```

### 绑定到 Workers
在 `wrangler.toml` 添加：
```toml
[[d1_databases]]
binding = "DB"
database_name = "music-db"
database_id = "xxxx-xxxx-xxxx-xxxx"
```

### 运行迁移
```bash
# 本地开发
wrangler d1 migrations create music-db "initial schema"
# 编辑 migrations/0001_initial_schema.sql
wrangler d1 migrations apply music-db --local

# 生产环境
wrangler d1 migrations apply music-db --remote
```

## 4. 配置 Workers

### wrangler.toml
```toml
name = "music-worker"
main = "apps/worker/src/index.ts"
compatibility_date = "2024-07-03"

# D1 数据库
[[d1_databases]]
binding = "DB"
database_name = "music-db"
database_id = "xxxx-xxxx-xxxx-xxxx"

# R2 存储桶
[[r2_buckets]]
binding = "AUDIO_BUCKET"
bucket_name = "music-audio"

[[r2_buckets]]
binding = "COVER_BUCKET"
bucket_name = "music-covers"

# KV 命名空间
kv_namespaces = [
  { binding = "CACHE", id = "xxxx-xxxx-xxxx-xxxx" }
]

# Stream（在控制台开通后自动注入）
[vars]
STREAM_ACCOUNT_ID = "your-cloudflare-account-id"
STREAM_SIGNING_KEY = "your-stream-signing-key"

# 环境变量（敏感信息用 secret）
[vars.SOME_PUBLIC_VAR]
value = "public-value"

# 开发环境变量
[dev.vars]
BETTER_AUTH_SECRET = "dev-secret-only"
```

### 设置 Secrets
```bash
# 设置敏感环境变量（不在代码中暴露）
wrangler secret put BETTER_AUTH_SECRET
wrangler secret put GITHUB_CLIENT_ID
wrangler secret put GITHUB_CLIENT_SECRET
wrangler secret put GOOGLE_CLIENT_ID
wrangler secret put GOOGLE_CLIENT_SECRET
```

## 5. 开通 Cloudflare Stream

### 控制台操作
1. 进入 **Stream** 菜单
2. 点 **Enable Stream**
3. 选择套餐：
   - 按量付费（推荐）：$5/1000 分钟存储 + $1/1000 分钟播放
   - 无免费额度，但前 1000 分钟免费试用

### 上传视频/音频
```bash
# 直接上传（小文件）
curl -X POST "https://api.cloudflare.com/client/v4/accounts/{account_id}/stream" \
  -H "Authorization: Bearer {api_token}" \
  -F "file=@song.mp3"

# 通过 Workers 上传（推荐）
const formData = new FormData();
formData.append("file", audioFile);

const response = await fetch(
  `https://api.cloudflare.com/client/v4/accounts/${env.STREAM_ACCOUNT_ID}/stream`,
  {
    method: "POST",
    headers: { Authorization: `Bearer ${env.STREAM_API_TOKEN}` },
    body: formData,
  }
);
```

### 获取播放 URL
```typescript
// Workers 端签名
const streamId = "xxxx-xxxx-xxxx-xxxx";
const signedUrl = await env.STREAM.sign({
  id: streamId,
  exp: Math.floor(Date.now() / 1000) + 600, // 10 分钟有效
  allowedOrigins: [corsOrigin],
});

return { url: signedUrl };
```

## 6. 部署 Pages（前端）

### 控制台操作
1. 进入 **Pages** 菜单
2. 点 **Create a project** → **Connect to Git**
3. 选择 GitHub 仓库
4. 配置构建：
   - Framework preset: **Next.js**
   - Build command: `cd apps/web && pnpm build`
   - Build output directory: `apps/web/.next`
   - Root directory: `/`
5. 环境变量：
   - `NEXT_PUBLIC_API_URL`: `https://music-worker.your-subdomain.workers.dev`
   - `NEXT_PUBLIC_CLOUDFLARE_STREAM_ID`: `your-stream-id`

### Wrangler CLI（备选）
```bash
# 部署 Pages
wrangler pages deploy apps/web/.next --project-name=music-website
```

## 7. 自定义域名（可选）

### 添加域名到 Cloudflare
1. 在 Cloudflare 控制台添加站点（免费计划）
2. 修改域名 NS 记录指向 Cloudflare
3. 等待生效（通常 24 小时内）

### 绑定域名
- **Pages**: Pages 项目 → **Custom domains** → 添加 `music.yourdomain.com`
- **Workers**: Workers 项目 → **Triggers** → **Custom domains** → 添加 `api.yourdomain.com`
- **R2**: R2 bucket → **Settings** → **Public access** → 绑定 `cdn.yourdomain.com`

### SSL/TLS
Cloudflare 自动提供免费 SSL 证书（Universal SSL），无需手动配置。

## 8. 本地开发环境

### 安装依赖
```bash
# 安装 Wrangler
npm install -g wrangler

# 登录
wrangler login

# 安装项目依赖
pnpm install
```

### 启动本地 Workers
```bash
cd apps/worker
wrangler dev --local
# 监听 http://localhost:8787
```

### 启动本地 Pages
```bash
cd apps/web
pnpm dev
# 监听 http://localhost:3000
```

### 本地 D1 数据库
```bash
# 创建本地数据库
wrangler d1 create music-db --local

# 运行迁移
wrangler d1 migrations apply music-db --local

# 打开 shell
wrangler d1 execute music-db --local --command="SELECT * FROM tracks;"
```

### 本地 R2（模拟）
Wrangler 会自动创建本地 R2 模拟（`/tmp/r2-...`），无需配置。

## 9. 监控 & 日志

### Workers 日志
```bash
# 实时日志
wrangler tail music-worker

# 查看历史日志
# 控制台 → Workers → 你的 Worker → Logs
```

### Stream 分析
控制台 → Stream → **Analytics**
- 播放次数
- 带宽使用
- 错误率

### D1 查询日志
控制台 → D1 → 你的数据库 → **Query Log**

## 10. 成本优化

### 免费额度内
- Workers: 10 万次/天
- D1: 5GB 存储
- R2: 10GB 存储 + 零出口费（**杀手锏**）
- KV: 1GB 存储 + 10 万次读取/天
- Pages: 无限站点

### 超出后计费
- Workers: $0.50/百万次请求
- D1: $5/月（无限制）
- R2: $0.015/GB/月存储 + 零出口费
- Stream: $5/1000 分钟存储 + $1/1000 分钟播放

### 省钱技巧
1. **R2 零出口费** → 音频存 R2，别用 AWS S3
2. **KV 缓存热门数据** → 减少 D1 读取次数
3. **Stream 签名 URL 短时效** → 防止盗链
4. **用 D1 FTS5 做搜索** → 不买 Algolia/Meilisearch
5. **免费配额用 Workers Cron** → 定时任务免费（每天 15 次）

---

## 🚀 快速启动检查清单

- [ ] Cloudflare 账号已注册
- [ ] R2 bucket 已创建（`music-audio`, `music-covers`, `music-waveforms`）
- [ ] D1 数据库已创建（`music-db`）
- [ ] Workers 项目已初始化（`wrangler.toml` 配置好）
- [ ] Stream 已开通（测试用前 1000 分钟免费）
- [ ] KV 命名空间已创建（用于缓存）
- [ ] 本地开发环境能跑通（`wrangler dev` + `pnpm dev`）
- [ ] 域名已绑定（可选，先用 `*.workers.dev` 测试）
- [ ] Secrets 已设置（Better Auth 密钥、OAuth ID/Secret）
