# TelePort

TelePort is a production-focused FastAPI service for downloading remote media with `yt-dlp` and forwarding it to Telegram or Google Drive.

## Features

- SSRF protection with URL scheme checks, hostname denylist, DNS resolution, and private/local IP blocking before any `yt-dlp` activity.
- Background download pipeline with progress tracking, cancellation, and file-size enforcement.
- Robust `yt-dlp` error handling with controlled API failures and fallback logic for direct media URLs.
- Auto-updating `yt-dlp` at container startup via `entrypoint.sh`.
- FFmpeg-enabled media processing for muxing and format compatibility.
- Upload support for Telegram (Pyrogram MTProto) and Google Drive.

## Quick Start

### 1. Build

```bash
docker build -t teleport:latest .
```

### 2. Configure

Create `.env` from `.env.example` and set your credentials.

### 3. Run

```bash
docker run --rm -p 8000:8000 --env-file .env --name teleport teleport:latest
```

### 4. Health Check

```bash
curl http://localhost:8000/health
```

## Architecture

- **FastAPI**: HTTP API and in-memory job orchestration.
- **yt-dlp + ffmpeg**: media extraction, merging, and download workflows.
- **Docker**: reproducible container runtime with startup-time `yt-dlp` upgrade.
- **Uploader Layer**: pluggable targets for Telegram and Google Drive.

## API Endpoints

- `GET /` - web UI
- `POST /media-info` - source inspection and YouTube detection
- `POST /download` - enqueue download + upload job
- `GET /status/{job_id}` - progress and result lookup
- `POST /cancel/{job_id}` - cancel running job
- `GET /health` - readiness endpoint

## Security Notes

- Only `http` and `https` URLs are accepted.
- Local/private/internal hosts are blocked before probe/download.
- DNS resolution is validated to prevent SSRF via hostname tricks.

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
