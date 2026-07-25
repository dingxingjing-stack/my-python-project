#!/usr/bin/env bash
set -e

echo "🚀 Avireon V2.0 — Starting AI Service..."

# Ensure data directory exists
mkdir -p data/uploads

# Run database migrations (built into init_db on startup)
echo "📦 Database migrations will run automatically on startup"

# Start uvicorn
exec uvicorn app.main:app \
  --host "${SERVICE_HOST:-0.0.0.0}" \
  --port "${SERVICE_PORT:-8000}" \
  --log-level info
