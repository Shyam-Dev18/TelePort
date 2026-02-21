#!/usr/bin/env sh
set -eu

pip install --no-cache-dir --upgrade yt-dlp

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}"
