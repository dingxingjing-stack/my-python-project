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

---

## 会话记录 (2026-07-27)

### 已完成工作

#### 1. 查询仓库 `gustavonline/pi-desktop` 根目录结构
- **结果**: 根目录**不存在** `backend` 文件夹
- **项目类型确认**: 该仓库是 **Tauri 桌面应用**（src-tauri/ + vite.config.ts + package.json），不含 Python FastAPI 后端代码
- **结论**: 不适合直接部署到 Render Web Service，部署方式应为生成安装包分发

### 待办/未完成
1. **Render 服务绑定的仓库问题**: Render 服务 `ai-music-backend` 当前绑定的是 `dingxingjing-stack/music-video-platform`（含 `backend/`），而非 `my-python-project`。需用户明确目标：
   - 如果要将 `my-python-project` 部署到 Render，需在 Dashboard 切换 Source 仓库
   - 如果要部署 `gustavonline/pi-desktop`（Tauri 项目），Render 不适用
2. **Render 部署修复** (延续 07-26): 仍被阻塞，需用户确认下一步操作

---

## 会话记录 (2026-07-28)

### 已完成工作

#### 1. 修复 Render 部署 `ModuleNotFoundError: No module named 'main'`
- **问题**: Gunicorn 启动命令 `gunicorn main:app` 找不到模块，因为 FastAPI 入口在 `ai-service/app/main.py`
- **根本原因**: `get_app()` 工厂模式在 `ai-service/app/main.py` 末尾导出 `app = get_app()`，模块路径应为 `ai-service.app.main:app`
- **解决**:
  - 分析 `main.py:299-300` 定位 `app = get_app()` 导出位置
  - 确认正确 gunicorn 模块路径为 `ai-service.app.main:app`
  - 向用户提供 Render Start Command 修正方案

#### 2. 后端依赖补全 (`ai-service/requirements.txt`)
- 新增 `gunicorn==20.1.0` — 解决 `gunicorn: command not found`
- 新增 `setuptools==68.0.0` — 解决 `ModuleNotFoundError: No module named 'pkg_resources'`（需在 gunicorn 之前安装）
- 构建命令使用 `pip install --only-binary=:all: -r requirements.txt` 跳过编译

#### 3. 前端 `ai-music-beta/` 构建验证
- `npm run dev` 正常启动，无报错
- `npm run build` 成功，vendor 分包（echarts/vue等独立 chunk）、sourcemap 关闭

### 当前 Git 状态
- 最新提交: au 需用户确认后推送
- 工作目录包含未提交的 `requirements.txt` 变更

### 待办/未完成
1. **Render 启动命令更新** — 用户需在 Render Dashboard 将 Start Command 改为：
   ```
   gunicorn -k uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:$PORT ai-service.app.main:app
   ```
   然后点击 Manual Deploy → Deploy latest commit
2. **Render 仓库绑定确认** — 需用户检查 `ai-music-backend` 服务绑定的仓库是否为 `dingxingjing-stack/my-python-project`
3. **`requirements.txt` 推送** — 用户需手动 `git add`、`git commit`、`git push` 以同步变更

---

## 会话记录 (2026-07-29)

### 已完成工作

#### 1. 修复 4 个 AI 路由 JSON body 解析失败 (422 错误)
- **问题**: `/api/v1/ai/lyrics`、`/cover`、`/mv`（2个路由）在 Render 部署后全部返回 422，前端无法使用
- **根本原因**: 4 个路由的 handler 写成 `async def func(req: dict, request: Request):`，FastAPI 无法正确解析 `req: dict` 参数
- **解决**: 统一改为 `async def func(request: Request):` + `req = await request.json()`，手动从请求体中解析 JSON
- **涉及文件**:
  - `ai-service/app/routes/ai/lyrics.py:17`
  - `ai-service/app/routes/ai/cover.py:17`
  - `ai-service/app/routes/ai/mv.py:32,85`
  - (此行已修复但提交有误，本次重新修复)

#### 2. `/api/v1/ai/generate` 改为异步任务队列
- `generate_music` 改为立即返回 `{"job_id": job_id}`，不再阻塞等待生成完成
- 新增简易内存 `_job_store` 存储任务状态
- 新增 `asyncio.create_task(_run_generation(...))` 后台异步执行完整生成链路
- 新增 `GET /api/v1/ai/job/{job_id}` 轮询端点，返回 `{status, progress, result, error}`
- 移除冗余的 `response_model=GenerateResponse` 避免返回类型不匹配

#### 3. 语法错误修复
- `music.py:269` 发现多余的独立 `)` 行（`_run_generation` 函数体后），导致 `SyntaxError: unmatched ')'`
- `music.py:297` 移除多余的 `import json`（已全局导入）

#### 4. 代码编译验证
- 全部 4 个修改文件通过 `python -m py_compile` 无报错

### 当前 Git 状态
- 最新提交: `e1d82da` - "fix: fix 4 AI routes JSON body parsing + async task queue for /generate + job polling endpoint"
- 已推送至 `origin/main`
- 工作目录干净

### 待办/未完成
1. **Render 重新部署** — 用户需在 `ai-music-backend-v2` 服务 → Manual Deploy → Deploy latest commit
2. **配置环境变量** — 用户需在 Render Dashboard 确认已设置 `SILICONFLOW_API_KEY`、`MUREKA_API_KEY` 等，否则生成会走 Mock
3. **部署后验证** — 访问 `/create` 页，输入提示词→生成歌词→生成音乐，检查有无报错

---

## 会话记录 (2026-07-30)

### 已完成工作

#### 1. 修复 `mv.py` 缺少 `Request` 导入 (commit `9f756bd`)
- **问题**: Render 启动报 `NameError: name 'Request' is not defined`
- **原因**: 07-29 修复 `req: dict` → `request: Request` 时，`mv.py` 漏导入了 `from fastapi import Request`
- **修复**: `mv.py:20` 补上 `Request` 导入

#### 2. 修复 `@require_feature` 装饰器破坏 FastAPI 参数解析 (commit `c286edc` → `563c53d`)
- **问题**: `/api/v1/ai/generate` 等受装饰器保护的路由持续返回 422 `{"loc":["query","request"]}`
- **根本原因**: 装饰器手动覆盖 `wrapper.__signature__ = sig`，FastAPI 据此生成的参数列表丢失了 `Request`/Pydantic 类型信息，把所有参数当成 query 参数
- **第一次尝试 (`c286edc`)**: 在覆盖签名时过滤掉 `Request` 类型的参数 —— 部分生效（lyrics 不再 422 但变 500），但 Pydantic 模型参数仍被错误处理
- **最终解决 (`563c53d`)**: **完全移除对 `__signature__` 的覆盖**，仅靠 `@wraps(func)` 的 `__wrapped__` 机制让 FastAPI 自动找到原始函数签名。这样 FastAPI 能正确识别 `Request` 注入和 Pydantic body 解析

#### 3. 歌词生成添加 Mock 兜底 (commit `8b3289e`)
- 当 AI scheduler（硅基/OpenRouter）全部失败时，不再抛 500
- 自动返回模板中文/英文歌词片段，让前端交互流程可以跑通
- 受 `MOCK_FALLBACK` 环境变量控制（默认 `true`，与音乐生成保持一致）
- 返回歌词带 `[Mock]` 前缀方便区分

#### 4. 部署验证标记 (commit `a1a9e1a`)
- `/health` 端点新增 `"build":"2026-07-30-v3"` 字段，用于确认 Render 是否拉到了最新代码

#### 5. 代码编译验证
- `mv.py`, `feature_flags.py`, `lyrics.py`, `main.py` 全部通过 `python -m py_compile`

### 关键发现
- **Render 部署不会自动拉最新代码**，每次 push 后都需在 Dashboard 手动点 **Manual Deploy → Deploy latest commit**
- **`@require_feature` 装饰器不能覆盖 `__signature__`**: FastAPI 严重依赖原始签名来识别参数类型（Request/Path/Body/Query），任何手动覆盖都会破坏这个机制

### 当前 Git 状态
- 最新提交: `563c53d` - "fix: use functools.wraps __wrapped__ instead of overriding __signature__"
- 已推送至 `origin/main`
- 工作目录包含未提交的 AGENTS.md 变更

### 阻塞项
- **Render 还没有部署 commit `563c53d`**（`__signature__` 最终修复）。目前运行的是 13:21 部署的 `a1a9e1a`（旧装饰器 + mock 兜底），因此：
  - `/api/v1/ai/generate` 仍返回 422（装饰器问题）
  - `/api/v1/ai/lyrics` 返回 500（mock 路径可能触发了 `scheduler` 其他异常）
- **明天的工作流**:
  1. Render Dashboard → **Manual Deploy** → **Deploy latest commit** (`563c53d`)
  2. 访问 `/health` 确认 `"build":"2026-07-30-v3"` 存在
  3. 测试 `/create` 页的歌词生成、音乐生成流程
  4. 如果仍有问题，在浏览器打开 F12 Console 截图给我

### 环境变量（供明天排查参考）
- Render 上需配置: `SILICONFLOW_API_KEY`, `OPENROUTER_API_KEY`, `MOCK_FALLBACK=true`
- 如果 AI key 未配置，`MOCK_FALLBACK=true` 后歌词和音乐都会走 Mock 兜底

---

## 会话记录 (2026-07-31)

### 已完成工作

#### 1. 定位并修复 Render 服务绑错代码的问题
- **问题**: 部署日志显示 `Inference Service API`、`POST /api/v1/music/run`、`music_platform.db`，完全不是我们的项目代码
- **原因**: Render 服务 Root Directory 没填 `ai-service`，或绑定了错误仓库/入口文件
- **解决**: 修正 Render Settings：
  - Root Directory: `ai-service`
  - Start Command: `gunicorn app.main:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT`
  - Runtime: Python 3
  - 更新 `render.yaml` (commit `5faa107`)

#### 2. `@require_feature` 装饰器 422 问题彻底修复（4 天排查最终确定根因）
- **问题**: `/api/v1/ai/generate`、`/lyrics` 等受保护路由持续 422 `{"loc":["query","request"]}`
- **根本原因**（真正元凶）: 装饰器创建的 wrapper 函数 `__globals__` 是 `feature_flags.py` 模块的 globals，**不包含**原函数模块中定义的任何自定义类型（如 `GenerateRequest`、`Request` 等）。FastAPI 的 `get_typed_annotation` 用 `wrapper.__globals__` 解析 forward ref 字符串注解失败 → 参数被当成普通 query 字符串 → 422
- **修复链路**:
  - `c286edc`: 过滤 Request 类型参数（部分生效）
  - `563c53d`: 完全移除 `__signature__` 覆盖（歌词不再 422 但 generate 仍 422）
  - `89f3a9b`: 在 feature_flags.py 导入 Request（歌词全通）
  - `a15208d` (**最终修复**): `wrapper.__globals__.update(func.__globals__)`，让 wrapper 能访问原函数模块的所有符号
- **本地 + 线上验证**: 所有 AI 端点全部 200，不再 422

#### 3. 线上全量测试通过 (2026-07-31 下午)
- 服务 URL: `https://ai-music-backend-db6h.onrender.com`
- 服务名: `ai-music-backend`，Runtime Python 3，Free plan
- `/health` → 200 (build v4)
- `/` 首页 → 200 (23KB)
- `/create` → 200 (53KB)
- `/console` → 200 (23KB)
- `/docs` Swagger → 200
- `/api/v1/features` → 200 (stage=1 开放 6 项)
- `/api/v1/ai/lyrics` → 200 (Mock 兜底歌词，因 AI key 未配置)
- `/api/v1/ai/generate` → 200 返回 job_id
- `/api/v1/ai/job/{id}` → 200 任务轮询
- `/api/v1/ai/cover/generate` → 503 (feature gate 关闭，stage=1 正常行为)
- `/api/v1/ai/mv/generate` → 503 (feature gate 关闭，stage=1 正常行为)

#### 4. 性能优化与 bug 修复（未完成，进行中）
- **发现 `_try_hf_fallback` 严重 bug**: 函数是同步 `def`，但 `_get_http_client()` 返回 `httpx.AsyncClient`，`client.post()` 没加 `await` → 永远拿不到响应，HF 兜底**从未真正工作过**
- 已修复: `_try_hf_fallback` 改为 `async def` + `await client.post()`，调用处加 `await`（**未提交**，工作目录有改动）

### 当前 Git 状态
- 最新提交: `a15208d` - "fix: merge func.__globals__ into wrapper.__globals__..."
- 已推送至 `origin/main`
- **工作目录有未提交改动**: `music.py` 的 HF fallback async 修复

### 阻塞项/待办
1. **提交并推送 `music.py` HF async 修复**（明天第一件事）
2. **Render 手动部署**到 `ai-music-backend` (db6h)
3. **性能优化方案**（已分析未实施）:
   - Agnes/Mureka/HF 未配置 key 时已有快速降级检查，基本瞬时
   - MV 生成慢：SDXL 4 图串行 (80s) + Runway 4 视频串行 (480s)，可并发化 (`asyncio.gather`) 节省 75%
   - MV 默认 `num_scenes=4` 可降到 2 省一半时间
4. **MV 模板**: 项目已有 6 套内置模板（含 mv_prompt），但 `/create` 页未展示模板选择，且 mv 端点 stage=1 关闭
5. **建议配置**:
   - `SILICONFLOW_API_KEY` / `OPENROUTER_API_KEY` → 真 AI 歌词（现在走 Mock）
   - `FEATURE_STAGE=2` → 开放 MV/cover 高级功能
   - `MOCK_FALLBACK=true` → 兜底开关

### 关键经验教训
- **`wrapper.__globals__` 不含调用模块符号** → forward ref 解析失败 → 422。装饰器必须 `update(func.__globals__)`
- **同步 def 里调用 AsyncClient 不 await** → 拿到协程对象 → 静默失败。检查此类 bug 要看 `response = client.post(...)` 是否缺 `await`
- Render 部署后必须手动 **Manual Deploy → Deploy latest commit**

---

## 会话记录 (2026-08-02)

### 背景转折
- 部署平台已从 **Render 切换为 Modal**（`ai-service/modal_server.py` 为入口，App 名 `avireon-ai-music`）
- 线上 URL: `https://dingxingjing-stack--avireon-ai-music-web.modal.run`
- 部署命令（GBK 编码坑，必须 UTF-8 前缀）:
  `chcp 65001 >$null; $env:PYTHONIOENCODING='utf-8'; modal deploy modal_server.py`
- `/health` 返回 `{"status":"ok","build":"2026-07-31-v4"}`（build 版本号未随本次会话递增）

### 已完成工作

#### 1. 多语言支持扩展至 5 种语言 (`app/i18n.py` 重写 + 前端)
- **根因**: `i18n.py` 原 `SUPPORTED_LOCALES={"zh_CN","en"}`，navbar/settings 切换器只有 en/zh
- **i18n.py 重写**: `SUPPORTED_LOCALES={"zh_CN","en","ja_JP","ko_KR","es_ES"}`；内置 `_BUILTIN_JA/_KO/_ES` 翻译字典（各约 90 键）+ `_merged_translations()` 合并 gettext catalog；`detect_locale()` 新增 accept-language ja/ko/es 识别；`i18n_context()` 新增 `supported_locales`/`locale_names`；新增路由 `GET /api/v1/lang/current`、`POST /api/v1/lang/set`、`GET /api/v1/lang/translations`
- `navbar.html`: 语言切换器增加 日本語/한국어/Español + 按钮显示当前语言名称（`localeDisplay`）+ 新增 **Templates** 导航链接（桌面+移动端，此前模板库页 `/templates` 不可达）
- `base.html`: `app()` 新增 `localeDisplay()`/`localeNames`，`setLang` 支持任意语言
- `settings.html`: 语言下拉 + 默认歌词语言下拉增加 ja/ko/es
- `i18n.py` 补充 `No templates yet` 三语翻译

#### 2. MV 无法生成 bug 修复 (`create.html`)
- **根因**: 前端 MV 调用请求体只发 `{audio_url, prompt, style}`，后端 `mv.py:48` 要求 `lyrics` 字段 → 恒 400 "Missing lyrics"
- **修复**: `create.html` generateMV 请求体补齐 `lyrics`/`title`/`num_scenes`/`style`
- `mv.py` 此前已将 feature gate 从 `ai_mv_advanced` 改为 `ai_mv_simple`（stage=1 已开放）

#### 3. Modal secrets 挂载 (`modal_server.py`)
- `web()` secrets 列表新增 `modal.Secret.from_name("avireon-secrets")`
- 用 CLI 创建了占位密钥集 `avireon-secrets`（含 RUNWAY_API_KEY/AGNES_API_KEY 两个空键）:
  `modal secret create avireon-secrets RUNWAY_API_KEY="" AGNES_API_KEY=""`
- **注意**: Modal Secrets 改动不会自动生效，必须在控制台填入真实 key 后重新 `modal deploy`

#### 4. OpenRouter 模型池更新 (`config.py` + `ai_scheduler.py` 注释)
- 用户提供 OpenRouter 官方最新免费模型列表，先用 `curl openrouter.ai/api/v1/models` 拉取 337 个模型验证 slug，再更新
- chat 池新增 `google/gemma-4-31b-it:free`；修正 `google/gemma-4-26b-a4b-it:free`（补 `-it`）、`inclusionai/ling-3.0-flash:free`（原 `ling/...`）
- code 池移除已下架的 `qwen/qwen3-coder:free`，保留 `cohere/north-mini-code:free`
- multimodal 修正 `nvidia/nemotron-nano-12b-v2-vl:free`（原 `-12b-2-vl`）
- other 池移除失效 `lagoon/laguna-2.1:free`，修正为 `poolside/laguna-s-2.1:free`、`poolside/laguna-xs-2.1:free`
- rerank 升级为 `nvidia/llama-nemotron-rerank-vl-1b-v2:free`
- **结果**: 8 组 16 个唯一免费模型，无重复、无残留旧 slug
- **说明**: rerank VL 1B V2 / Embed VL 1B V2 当前不在 OpenRouter 可用免费列表（可能已下架），保留配置无碍（调用失败自动降级）

### 密钥
- `avireon-secrets`（Modal 控制台）: RUNWAY_API_KEY、AGNES_API_KEY 仍为占位/空值，**用户计划自行填入真实 key**，填后需重新部署生效
- OpenRouter key / SiliconFlow key: 配置于 `ai-service/.env`（已 gitignore），Modal 侧分别存于 secrets `openrouter-key` / `siliconflow-key`。**不要在任何可提交文件中写入真实 key**（GitHub push protection 会拦截）

### 待办/未完成
1. **提交并推送今天的改动**（当前工作目录有大量未提交改动，见下方清单）
2. **Modal 控制台填入 RUNWAY_API_KEY / AGNES_API_KEY** 到 `avireon-secrets` 并保存
3. **用户自行测试接口**（用户已明确表示部署完毕自己测）
4. 音乐生成慢（LLM 歌词生成 5-18s 属真实 AI 调用耗时，非 bug）
5. 模板/画面前端展示已可达（navbar 加了 Templates 入口），待用户实测

### 当前 Git 状态（未提交改动清单）
- modified: `ai-service/.env.example`、`app/config.py`、`app/i18n.py`、`app/main.py`、`app/routes/ai/lyrics.py`、`app/routes/ai/music.py`、`app/routes/ai/mv.py`、`app/services/agnes_music_service.py`、`app/services/ai_scheduler.py`、`app/templates/base.html`、`app/templates/components/navbar.html`、`app/templates/pages/create.html`、`app/templates/pages/settings.html`
- untracked: `ai-service/.modalignore`、`ai-service/app/core/`、`ai-service/app/services/ai/`、`ai-service/app/startup_check.py`、`ai-service/modal_server.py`
- `.env` 已 gitignore 不入库
- 最近 3 个已推送提交: `6e7ffb7`(OpenRouter models + Agnes fallback + Runway client)、`ab14616`(async concurrency)、`fd01834`(MV max_tokens 1500->800)

---

## 会话记录 (2026-08-03)

### 已完成工作

#### 1. 线上功能验证（全部通过）
- `/health` → 200 `{"status":"ok","build":"2026-07-31-v4"}`
- 模板库 `/api/v1/templates/list` → 6 套内置模板齐全（古风/流行/说唱/赛博朋克/治愈/短剧，含 SVG 封面/歌词模板/音乐/MV prompt）
- `/api/v1/features` → stage=1，6 项开放（ai_music/ai_lyrics/ai_tts/ai_mv_simple/health/docs）
- 5 语言翻译 `/api/v1/lang/translations` → 22.8KB，zh_CN/en/ja_JP/ko_KR/es_ES 全部在线
- 歌词生成 POST `/api/v1/ai/lyrics` → 200，**OpenRouter 真实 AI 生成**（model=`nvidia/nemotron-3-nano-30b-a3b:free`，38.1s），非 Mock，证明线上 secrets 已生效

#### 2. 修复 `local_storage.py` 路径错误（视频 404 根因）
- **根因**: `app/services/local_storage.py:22` 用 `parent.parent` 定位根目录，但该文件在 `app/services/` 下，`parent.parent` 只到 `ai-service/app`，文件保存到 `app/data/uploads/`；而 `main.py`（在 `app/` 下）用 `parent.parent` 正确到 `ai-service/`，StaticFiles 挂载 `data/uploads`。**两边路径不一致 → 生成文件永远 404**
- **修复**: `local_storage.py` 改为 `parent.parent.parent`，本地验证 MATCH=True

#### 3. Modal 无状态容器丢文件 → 挂载持久化卷
- **问题**: 本地文件存储在容器回收后丢失；视频 404 的另一因素
- **修复** (`modal_server.py`):
  - `data_volume = modal.Volume.from_name("avireon-data-v2", create_if_missing=True)`（新版 Modal 不再用 NetworkFileSystem，用 Volume）
  - `web()` 和 `doctor()` 都挂载 `volumes={"/root/ai-service/data": data_volume}`
  - `add_local_dir(..., ignore=["data/"])` 排除本地 data 目录（避免镜像非空目录无法挂卷，报 `cannot mount volume on non-empty path`）
  - **注意**: 本 SDK 的 `add_local_dir` 用的是 `ignore` 参数（非旧的 `condition`，旧参数已移除）
- **验证**: doctor 显示 video 文件已持久化到卷（`uploads/videos/xxx.mp4` 可见），下载从 404 → 200

#### 4. MV 无生图兜底：文字幻灯片 MV (`mv.py`)
- **根因链**: SiliconFlow key 线上 **403 Forbidden**（`/v1/chat/completions`）→ `generate_image_sdxl` 无降级失败 → scene_images 空 → FFmpeg 无输入 → 0 字节/坏链接
- **修复** (`_compose_mv` + 新增 `_compose_text_mv`):
  - 过滤空/无效图片 URL（仅保留 `/uploads/` 开头）
  - 图片合成失败（0 字节）自动回退 `_compose_text_mv`
  - `_compose_text_mv`: FFmpeg `color` + `drawtext`（DejaVuSans 字体）逐页生成歌词文字幻灯片 → concat 合成可播放 MV
  - drawtext 文本安全过滤（`_safe_text`），特殊字符转义
  - **本地验证**: 2 页 segment 生成 + concat 成功，输出 8457 字节可播放视频
- **注意**: MV 每日限流 `daily_heavy_feature_calls=3` 测试期间已耗尽，端到端在线验证需次日

### 关键诊断工具
- `modal app logs avireon-ai-music` — 查看线上日志（无 `modal logs` 命令，需用 `modal app logs`）
- `modal run modal_server.py::doctor` — doctor 函数已增强：检查 volume 目录/文件、ffmpeg 版本、字体路径、4 个密钥环境变量前缀/后缀（安全，不打印完整 key）

### 密钥状态（线上实测）
- `SILICONFLOW_API_KEY` = `sk-sgvf...zwocdg`（51字符）已注入但 **API 返回 403**（key 在平台侧无效/未实名/余额不足），需用户核实
- `OPENROUTER_API_KEY` = `sk-or-v1...be91cd` 正常（歌词生成走 OR 成功）
- `RUNWAY_API_KEY` / `AGNES_API_KEY` = **EMPTY**（avireon-secrets 占位未填）

### 阻塞项
1. **SiliconFlow key 403** → MV 无 AI 生成画面，只能出文字幻灯片版。需用户在 siliconflow.cn 核实 key 有效性（可能需实名/充值），填对后 Modal 控制台更新 `siliconflow-key` 并重新部署
2. **RUNWAY_API_KEY / AGNES_API_KEY** 未填 → MV 无动态镜头、无 Agnes 音乐优化
3. MV 每日 3 次限流已耗尽，端到端在线验证待次日

### 当前 Git 状态
- 最新提交: `ec390a2`（2026-08-02 多语言+MV修复+模型池）
- 待提交: `ai-service/.modalignore`、`app/routes/ai/mv.py`、`app/services/local_storage.py`、`modal_server.py`

---

## 会话记录 (2026-08-03 下午)

### 已完成工作（音乐生成链路 3 个根因修复，全链路验证通过）

#### 1. 修复 siliconflow 网络挂起导致任务卡死（commit `be40f0a`）
- **根因**: `llm_client.py` 的 `AsyncOpenAI` 未设 timeout（默认 600s），且 `@retry(3)` 指数退避 → siliconflow 连接挂起时单次调用可卡 30 分钟；`_call_siliconflow` 也是 `httpx.AsyncClient(timeout=60)` + retry(3)，最坏 3 分钟
- **修复**:
  - `llm_client.py`: AsyncOpenAI 加 `timeout=httpx.Timeout(60, connect=15)`；`chat()`/`chat_stream()` 内层加 `asyncio.wait_for(..., 45)`；retry 3→2
  - `ai_scheduler.py`: `_call_siliconflow` 超时收紧 `httpx.Timeout(45, connect=10)`；`_call_with_retry` retry 3→2
  - 线上日志确认: `[primary] siliconflow 调用失败: ConnectTimeout` 在 45s 触发，随后 `[fallback] openrouter 调用成功`

#### 2. 修复 Agnes 优化层无降级（`agnes_music_service.py`）
- **根因**: `_call_llm_fallback` 用 `llm_client.complete()` 固定 provider=siliconflow，失败不尝试 OpenRouter，且 AsyncOpenAI 挂起
- **修复**: 改为 `scheduler.dispatch(AITaskType.TEXT, ...)`（原生 httpx + 45s 超时 + siliconflow→openrouter 自动降级），外层 `asyncio.wait_for(180)`
- **验证**: `agnes_debug: success=True, opt_changed=yes`（真实 OpenRouter 生成优化提示词+歌词）

#### 3. 修复 TEXT/CODE 任务被误判重型限流（`feature_flags.py`）
- **根因**: `rate_tier()` 对字典外 key 默认返回 `"heavy"` → scheduler 内部 action（text/code/long/vision 等）全部计入 `daily_heavy_feature_calls` 配额 → 满 3 次后生成链路直接 429
- **修复**: `_FEATURE_RATE_TIER` 补充 `text/code/long/code_alt/vision: "light"`；`rate_tier()` 默认值改为 `"light"`（只有显式列出的重型功能才 heavy）
- **注意**: 这也解释了为什么歌词生成会隐性消耗重型配额

### 当前部署状态
- Modal 已部署最新代码（`be40f0a`），`/api/v1/ai/generate` 全链路可用
- 完整链路日志: siliconflow ConnectTimeout(45s) → OpenRouter 成功(38s) → Mureka 未配 key 降级 → HF 未配 token 跳过 → **Mock 音频兜底**（`ai_provider=agnes+mock`）
- 音频最终仍走 Mock 因 Mureka/HF key 未配置（见阻塞项）

### 阻塞项（需用户处理）
1. **SiliconFlow key 无效**（平台侧 403 + 网络挂起）→ 所有走 siliconflow 的请求需等 45s ConnectTimeout 才降级 OpenRouter，拖慢生成。建议用户核实 siliconflow key 或直接配置 `MUREKA_API_KEY`/`HF_TOKEN` 让音乐生成走真实音频
2. **Mureka/HF key 未配置** → 音乐生成只能出 Mock 示例音频
3. **RUNWAY/AGNES key 未填** → MV 无动态镜头

### 当前 Git 状态
- 最新提交: `be40f0a` - "fix(scheduler): llm timeout hardening + Agnes fallback via scheduler + rate_tier default light"（已推送）
- 工作目录干净

---

## 会话记录 (2026-08-03 深夜)

### 已完成工作

#### 1. 首页/控制台 5 语言切换器补全（commit `b13179b`）
- **问题**: 首页 `static/home.html`、控制台仅支持 en/zh_CN，缺 ja_JP/ko_KR/es_ES；导航缺 Templates 入口
- **修复**:
  - `static/home.html`: 语言切换器扩至 5 语言 + 按钮显示当前语言名称 + 桌面/移动导航加 Templates 链接
  - `app/templates/pages/console.html`: **线上实际生效的 console**（Jinja2 版，内联 translations），语言切换器扩至 5 语言
  - `static/navbar.html`: 可复用组件扩至 5 语言 + Templates 链接（供 fetch 注入的页面复用）
  - `static/console.html`: 同步修改（该文件实际是死代码，见下）
- **关键发现**: `/console` 路由被 pages.py:119（Jinja2 版 `app/templates/pages/console.html`）覆盖，main.py:277 的 `static/console.html` 路由是**死代码**（FastAPI 先注册先匹配，pages_router 先 include）。线上 console 一直渲染的是 Jinja2 版

#### 2. 清理 0 字节污染文件（`modal_server.py` doctor 增强）
- doctor 函数新增 cleanup: 扫描 volume uploads 目录，删除 0 字节残留文件（生成失败产生的空文件）
- 已清理 `uploads/videos/20260803_17219cf8b787.mp4`（0 字节）

### 当前线上状态
- 5 语言切换器覆盖: `/`（home）、`/create`、`/templates`、`/console` 全部上线（已验证 200 + 按钮存在）
- login/register 是纯静态页（无 i18n 上下文），未加语言切换器（收益低）

### 阻塞项（不变，需用户处理）
1. **SiliconFlow key 无效**（平台 403 + 网络挂起）→ 每请求等 45s 才降级 OpenRouter，拖慢生成
2. **Mureka/HF key 未配置** → 音乐只能出 Mock 音频
3. **RUNWAY/AGNES key 空** → MV 无动态镜头

### 当前 Git 状态
- 最新提交: `b13179b` - "feat(i18n): 5-language switcher on homepage + console; Templates nav on all pages"（已推送）
- 工作目录干净

---

## 会话记录 (2026-08-03 深夜 续)

### 已完成工作

#### 1. 全量线上验证通过（commit `beae795` 前）
- `/api/v1/features` → 200，stage=1 开放 6 项（ai_music/ai_lyrics/ai_tts/ai_mv_simple/health/docs）
- explore/library/settings/admin 页面全部 200
- `/api/v1/explore`、`/api/v1/admin/dashboard`、`/api/v1/admin/jobs` → 200 数据正常（1 用户、4 任务全 completed）
- 5 语言切换器覆盖全部核心页面（settings/explore 含 ja/ko/es 下拉选项）

#### 2. 服务商优先级开关 PRIMARY_PROVIDER（commit `beae795`）
- **背景**: SiliconFlow key 无效（403 + 网络挂起），每次 TEXT/CODE 任务白等 45s ConnectTimeout 才降级 OpenRouter，生成 133s
- **实现**:
  - `config.py` 新增 `primary_provider: str = "siliconflow"`（默认本地）
  - `ai_scheduler.py`: 根据 `primary_provider` 动态构建 TEXT/CODE 路由。`openrouter` 时 primary=OR、fallback=None（不再回退无效 siliconflow）；`siliconflow` 时保持原有 OR fallback
  - `modal_server.py`: 挂载新 secret `avireon-config`（含 `PRIMARY_PROVIDER=openrouter MOCK_FALLBACK=true FEATURE_STAGE=1`，用 `modal secret create avireon-config ...` 创建）
- **验证**: 线上生成链路从 133s → **52s**（`[primary] openrouter 调用成功`，直接走 OR，无 ConnectTimeout 等待）
- **本地验证**: 两种模式 route 构建正确（OR-first 和 SF-first）

### 密钥状态（用户授权配置但无新 key，沿用现有）
- `openrouter-key` → 有效（PRIMARY_PROVIDER=openrouter 已注入）
- `siliconflow-key` → 平台 403，已降级为不用
- `avireon-secrets` → RUNWAY/AGNES 仍空
- `avireon-config` → 新建，PRIMARY_PROVIDER=openrouter
- **注意**: Modal secret 无 update 命令，只能 delete+create 或新建。真实 key 仍待用户提供

### 当前线上状态
- 音乐生成: OpenRouter 真实歌词（52s）→ Mock 音频（Mureka/HF 无 key）
- 全部核心页面 5 语言 + Templates 导航

### 阻塞项（需用户提供真实 key）
1. **SiliconFlow key**（平台 403）→ 需用户在 siliconflow.cn 核实/更换，然后 update `siliconflow-key` 并把 PRIMARY_PROVIDER 改回 siliconflow
2. **Mureka/HF key** 未配置 → 音乐只能出 Mock 音频
3. **RUNWAY/AGNES key** 空 → MV 无动态镜头

### 当前 Git 状态
- 最新提交: `beae795` - "feat(scheduler): PRIMARY_PROVIDER env to use OpenRouter directly, skip broken SiliconFlow"（已推送）
- 工作目录干净
