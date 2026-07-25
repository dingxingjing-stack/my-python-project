# AGENTS.md

## 项目定位

大型音乐网站（类似 Suno + Cabesa），支持 AI 生成原创音乐、声音克隆、MV 生成、二创/Remix。商业产品目标。

## 技术栈

- **全栈框架**：FastAPI + Jinja2 + HTMX + Alpine.js
- **数据库**：SQLite + aiosqlite
- **文件存储**：Cloudflare R2 (S3 兼容) 或 MinIO
- **音频处理**：FFmpeg 本地转码
- **鉴权**：FastAPI Users + JWT
- **AI 服务**：内建于同一 FastAPI 进程

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