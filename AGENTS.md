# AGENTS.md

## 项目定位

大型音乐网站（类似 Suno + Cabesa），支持 AI 生成原创音乐、声音克隆、MV 生成、二创/Remix。商业产品目标。

## 技术栈

- **全栈框架**：FastAPI + Jinja2 + HTMX + Alpine.js
- **数据库**：SQLite + aiosqlite
- **文件存储**：Cloudflare R2 (S3 兼容) 或 MinIO
- **音频处理**：FFmpeg 本地转码
- **鉴权**：FastAPI Users + JWT
- **AI 服务**：双服务商（SiliconFlow + OpenRouter）内建于同一 FastAPI 进程

## 工作约束

1. **降频调用**：任务拆解为细小步骤，逐项执行，一个步骤完成后再启动下一个，严禁批量高频发送请求。
2. **自主纠错**：运行报错时自主排查并修复，不频繁向AI上报问题。
3. **自我学习**：复盘过往运行记录，持续优化运行方案，持续减少AI交互次数。
4. **管控磁盘读写**：缩减磁盘读取、写入次数，降低硬件损耗。
5. **内存优先**：数据优先存放于内存缓存，只在关键节点执行硬盘写入，杜绝反复读写硬盘。
6. **本地优先**：全部运算、校验、代码调试优先在本地运行；仅当本地算力与程序无法完成时，才调用其他AI接口。

## 项目规范

参照 `SKILL.md` 和 `references/` 下的参考文档。

### 必须遵守
- 不依赖 Cloudflare Workers/Pages/Stream/KV/Durable Objects
- 不引入 Node.js/npm/pnpm/TypeScript
- 所有代码用 Python + Jinja2 模板
- 前端交互用 HTMX + Alpine.js，零构建步骤
- FFmpeg 由后端 subprocess 调用，不传到前端
- 签名 URL 必须由后端生成
- 数据库查询走索引，用 cursor 分页
- 密码用 bcrypt 哈希

---

## 会话记录 (2026-07-25)

### 已完成工作

#### 1. 双服务商模型分发系统 (config.py + ai_scheduler.py + usage_tracker.py)
- `app/config.py`: 添加 OpenRouter 16 款免费模型池（8 分组: long/chat/code/multimodal/embedding/safety/rerank/other）
- `app/config.py`: 新增 `openrouter_text_fallback` (lagoon/laguna-m.1:free) 兜底模型
- `app/config.py`: 新增 `daily_siliconflow_calls` / `daily_openrouter_calls` 独立日限额
- `app/services/ai_scheduler.py`: `dispatch()` 通过 `_call_chain()` 实现降级链
  - TEXT → 硅基 Qwen2.5-7B-Instruct → OpenRouter lagoon 兜底
  - CODE → 硅基 Qwen2.5-Coder-7B-Instruct → OpenRouter command-r-code 兜底
  - LONG / CODE_ALT / VISION → 仅 OpenRouter（无降级）
- `app/services/ai_scheduler.py`: 新增 `AIResult.fallback_used` 标记、`QuotaExceededError`、`AllProvidersFailedError`
- `app/services/usage_tracker.py`: 新增 `provider_daily_usage` 表 + `check_provider_daily_limits()` / `record_provider_usage()`

#### 2. 修复旧版 llm_client.py 配置引用
- `app/services/llm_client.py`: 重写，移除废弃的 relay/deepseek/nvidia/glm 字段引用
- 改用 Settings 中的 `siliconflow_api_key` / `openrouter_api_key` 构建客户端
- 默认 provider 改为 `siliconflow`，base_url 自动拼接 `/v1`

#### 3. AI 音乐路由 8 项生产级缺陷修复 (music.py)
- `_try_hf_fallback` 移除未使用的 `style` 入参
- 新增 `MOCK_FALLBACK` 环境变量开关 Mock 兜底
- `httpx.AsyncClient` / `CDNUploader` 全局单例复用
- HF payload 通过 `max_new_tokens = duration * 50` 控制音频时长
- `task_id` 增加 `uuid.uuid4().hex[:6]` 随机后缀避免哈希碰撞
- HF 错误日志截断至 200 字符
- 降级日志统一标注 `[generate] 第 N 层` + 失败时 `→ 降级到第 N+1 层`
- 全链路 `prompt` / `final_prompt` / `generated_lyrics` 统一 `strip()`

#### 4. 三项线上部署优化 (main.py + start.sh + render.yaml)
- 根路径 `/` 与 `/health` 新增 HEAD 请求处理器，消除外部扫描 405 冗余日志
- `start.sh`: 监听端口改为 `PORT`(Render 平台优先) -> `SERVICE_PORT` -> `10000` 兜底
- `main.py`: 新增 `HealthAndScanFilter` 过滤 uvicorn.access 日志，屏蔽 `/health` 巡检与 HEAD 扫描噪声
- `render.yaml`: 移除硬编码 `SERVICE_PORT: "10000"`

### 当前 Git 状态
- 远程仓库: `https://github.com/dingxingjing-stack/my-python-project`
- 最新提交: `a030161` - "chore(deploy): 三项线上部署优化"
- 工作目录干净，无未暂存变更

### 服务商密钥
- 密钥已配置在 `.env` 文件中（已从版本控制中移除）

### 已知注意事项
- `llm_client.py` 是旧版客户端，仍有其他路由引用但已重写适配新配置
- 启动 server 需指定 `--log-level info` 以使用日志过滤器
- `data/*.db` / `*.db-wal` / `*.db-shm` 已加入 `.gitignore`

---

## 会话记录 (2026-07-26)

### 已完成工作

#### 1. Render 部署修复 — 根路由返回 HTML 而非 JSON
- **问题**: Render 部署返回 `"Inference Service API v3.0.0"` JSON（完全不同的旧代码），非项目代码
- **根本原因**: `render.yaml` 放在 `ai-service/` 子目录，Render 找不到它，导致使用了错误的构建配置
- **解决**:
  - 将 `render.yaml` 从 `ai-service/` 移到仓库根目录
  - 添加 `rootDir: ai-service` 确保构建在正确子目录下运行
  - `main.py:262` 根路由改用 `with open("static/home.html")` 渲染 HTML
  - 新增 `/console`、`/login`、`/register` 路由读取对应静态 HTML
  - 移除 `pages.py` 中重复的根路由定义
- **状态**: Render 仍返回旧代码 JSON，需用户在 Render Dashboard 检查仓库绑定和手动触发部署

#### 2. 创建独立前端 `ai-music-beta/` — Vue3 + Vite + Tailwind CSS
- 公测专用 AI 音乐生成单页网站，适配电脑+手机
- **项目位置**: `C:\Users\dingx\Desktop\music-website-skill-backup\ai-music-beta\`
- **6 个组件**:
  - `NavBar.vue` — 悬浮导航 + 公测横幅 + 滚动磨砂玻璃效果
  - `HeroSection.vue` — Canvas 粒子背景 + 打字机标题 + 渐变光晕
  - `GeneratorSection.vue` — 提示词输入(500字限制) + 10种曲风选择 + 时长滑块(15-180s) + 加载脉冲动画
  - `PlayerSection.vue` — Canvas 实时波形频谱 + 播放/暂停/进度条 + MP3 下载
  - `BetaInfoSection.vue` — 3 项额度统计卡 + 5 条使用须知 + 6 项 FAQ 折叠面板
  - `SiteFooter.vue` — Beta 0.1 版本号 + 版权 + 隐私/服务条款/API 文档链接
- **后端对接**: `POST /api/v1/ai/generate`（实际后端端点与用户提供的 `/api/v1/music/run` 不同，已验证修正）
- **构建产物**: JS 85KB / CSS 33KB（gzip ~39KB）
- **部署文档**: `CLOUDFLARE_DEPLOY.md` — Cloudflare Pages 部署步骤

#### 3. 后端静态文件补全
- 创建 `ai-service/static/navbar.html` 可复用导航栏组件
- 创建 `ai-service/static/js/app.js` 全局语言加载 + Alpine 上下文工厂
- `main.py` 挂载 `directory="static"`（相对路径指向 `ai-service/static/`）

### 待办/未完成
1. **Render 部署修复** — 已推送到 GitHub（`c29b0dc`），但 Render 仍显示旧代码。需用户：
   - 在 Render Dashboard 检查仓库绑定是否为 `dingxingjing-stack/my-python-project`
   - 手动触发 "Deploy latest commit"
2. **`ai-music-beta/` 部署** — 建议推送到 GitHub 后通过 Cloudflare Pages 部署
3. **已删除 `ai-service/render.yaml`**（移到仓库根目录）
