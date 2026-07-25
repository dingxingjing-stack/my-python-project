#!/usr/bin/env bash
set -e

echo "🚀 Avireon V2.0 — Starting AI Service..."

# Ensure data directory exists
mkdir -p data/uploads

# Run database migrations (built into init_db on startup)
echo "📦 Database migrations will run automatically on startup"

# ── 端口优先级：Render 平台 PORT > SERVICE_PORT 自定义 > 兜底 10000 ──
# Render 会注入 PORT 环境变量；本地开发可用 SERVICE_PORT 覆盖
LISTEN_PORT="${PORT:-${SERVICE_PORT:-10000}}"

echo "🌐 Listening on 0.0.0.0:${LISTEN_PORT}"

# Start uvicorn
exec uvicorn app.main:app \
  --host "${SERVICE_HOST:-0.0.0.0}" \
  --port "${LISTEN_PORT}" \
  --log-level info
