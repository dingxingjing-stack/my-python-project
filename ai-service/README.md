# Python AI Service (ai-service)

独立部署的 AI 推理服务，被 Cloudflare Workers 通过内部 HTTP 调用。

## 功能

- **音乐生成**：调用 Suno 社区 API 生成完整歌曲
- **声音克隆**：通过 GPT-SoVITS 推理合成歌声
- **歌词生成**：DeepSeek / OpenAI 生成 LRC 歌词
- **MV 生成**：调用 Runway Gen-4.5 API 生成视频片段
- **二创/Remix**：Demucs 分离 + 风格重混音
- **R2 上传**：所有 AI 结果统一上传到 Cloudflare R2

## 快速开始

### 1. 安装依赖

```bash
cd ai-service
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # Linux/Mac
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入真实 key
```

### 3. 启动开发服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. 健康检查

```bash
curl http://localhost:8000/health
```

## 鉴权

Workers 调用本服务时必须携带：

```
Authorization: Bearer <INTERNAL_API_TOKEN>
```

否则返回 401。

## 异步任务模型

所有 AI 操作均异步处理：

1. Workers POST 请求 → 本服务立即返回 `{ "job_id": "..." }`
2. 后台任务执行 AI 调用
3. Workers 通过 `GET /internal/ai/task/{job_id}` 轮询状态
4. 完成后结果 URL 写入响应

## 部署

```bash
# Docker
docker build -t music-ai-service .
docker run -p 8000:8000 --env-file .env music-ai-service
```

## 目录结构

```
ai-service/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 环境变量配置
│   ├── routes/              # 路由模块
│   │   ├── music.py         # 音乐生成
│   │   ├── voice.py         # 声音克隆
│   │   └── lyrics.py        # 歌词生成
│   ├── services/            # 业务封装
│   │   ├── suno_client.py   # Suno API
│   │   ├── deepseek_client.py
│   │   ├── sovits_engine.py
│   │   └── r2_uploader.py
│   └── models/
│       └── schemas.py       # Pydantic 模型
├── requirements.txt
├── Dockerfile
└── .env.example
```
