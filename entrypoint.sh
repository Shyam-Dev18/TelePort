#!/usr/bin/env sh
set -eu

# Note: yt-dlp version is pinned in requirements.txt
# To upgrade, update requirements.txt and redeploy container
# Do NOT upgrade on every startup as it wastes resources

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}"
