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
