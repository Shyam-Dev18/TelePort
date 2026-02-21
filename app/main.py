# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.downloader import DownloadCancelledError, DownloadFailedError, DownloadTooLargeError, Downloader
from app.schemas import (
    DownloadCreateResponse,
    DownloadRequest,
    MediaInfoRequest,
    MediaInfoResponse,
    StatusResponse,
)
from app.thumbnail import download_and_validate_thumbnail
from app.uploader.google_drive import GoogleDriveUploader
from app.uploader.telegram import TelegramUploader
from app.utils import resolve_and_check_hostname, safe_filename, setup_logging, validate_external_url


setup_logging()
SETTINGS = get_settings()
APP_LOGGER = logging.getLogger("tele_port.app")

app = FastAPI(title=SETTINGS.app_name)
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
async def startup_banner() -> None:
    print("--- TelePort initialized by Shyam ---")


@dataclass(slots=True)
class JobState:
    job_id: str
    status: str = "queued"
    progress: int = 0
    stage: str = "queued"
    detail: str | None = None
    result_url: str | None = None
    error: str | None = None
    cancel_requested: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


jobs: dict[str, JobState] = {}
jobs_lock = asyncio.Lock()
downloader = Downloader(SETTINGS)
google_uploader: GoogleDriveUploader | None = None
telegram_uploader: TelegramUploader | None = None


def make_uploader(destination: str | None = None) -> GoogleDriveUploader | TelegramUploader:
    global google_uploader
    global telegram_uploader

    destination = (destination or SETTINGS.upload_destination).lower().strip()
    if destination == "google_drive":
        if not SETTINGS.is_google_enabled:
            raise RuntimeError("Google Drive uploader is not configured")
        if google_uploader is None:
            google_uploader = GoogleDriveUploader(
                client_id=SETTINGS.google_client_id or "",
                client_secret=SETTINGS.google_client_secret or "",
                refresh_token=SETTINGS.google_refresh_token or "",
            )
        return google_uploader

    if destination == "telegram":
        if not SETTINGS.is_telegram_enabled:
            raise RuntimeError("Telegram MTProto uploader is not configured")
        if telegram_uploader is None:
            telegram_uploader = TelegramUploader(
                api_id=int(SETTINGS.telegram_api_id or 0),
                api_hash=SETTINGS.telegram_api_hash or "",
                bot_token=SETTINGS.telegram_bot_token or "",
                target_chat=SETTINGS.telegram_target_chat,
            )
        return telegram_uploader

    raise RuntimeError("UPLOAD_DESTINATION must be 'telegram' or 'google_drive'")


async def set_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    stage: str | None = None,
    detail: str | None = None,
    result_url: str | None = None,
    error: str | None = None,
    cancel_requested: bool | None = None,
) -> None:
    async with jobs_lock:
        state = jobs[job_id]
        if status is not None:
            state.status = status
        if progress is not None:
            state.progress = max(0, min(100, progress))
        if stage is not None:
            state.stage = stage
        if detail is not None:
            state.detail = detail
        if result_url is not None:
            state.result_url = result_url
        if error is not None:
            state.error = error
        if cancel_requested is not None:
            state.cancel_requested = cancel_requested
        state.touch()


async def is_job_cancelled(job_id: str) -> bool:
    async with jobs_lock:
        state = jobs.get(job_id)
        return bool(state and state.cancel_requested)


def apply_filename_override(file_path: Path, file_name: str | None) -> Path:
    if not file_name:
        return file_path
    desired = safe_filename(Path(file_name).stem or file_name)
    target = file_path.with_name(f"{desired}{file_path.suffix}")
    if target == file_path:
        return file_path
    if target.exists():
        target = file_path.with_name(f"{desired}_{uuid.uuid4().hex[:6]}{file_path.suffix}")
    file_path.rename(target)
    return target


def check_dns_ssrf(url: str, *, allow_private: bool = False) -> None:
    from urllib.parse import urlparse

    host = urlparse(url).hostname
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL")
    resolve_and_check_hostname(host, allow_private=allow_private)


async def guard_url_for_ssrf(url: str) -> None:
    cfg = get_settings()
    validate_external_url(url, allow_private=cfg.allow_private_ips)
    await asyncio.to_thread(check_dns_ssrf, url, allow_private=cfg.allow_private_ips)


async def process_job(job_id: str, payload: DownloadRequest) -> None:
    file_path: Path | None = None
    thumbnail_path: Path | None = None
    try:
        if await is_job_cancelled(job_id):
            raise DownloadCancelledError("Job canceled by user")

        await set_job(job_id, status="downloading", stage="downloading", detail="Starting download")
        loop = asyncio.get_running_loop()

        async with jobs_lock:
            state = jobs.get(job_id)
            if state is None:
                raise RuntimeError("Job state not found")
            cancel_event = state.cancel_event

        def is_cancelled_sync() -> bool:
            return cancel_event.is_set()

        def progress_cb(progress: int, detail: str) -> None:
            if is_cancelled_sync():
                return
            loop.call_soon_threadsafe(
                asyncio.create_task,
                set_job(
                    job_id=job_id,
                    status="downloading",
                    progress=progress,
                    stage="downloading",
                    detail=detail,
                )
            )

        result = await downloader.download(
            url=str(payload.url),
            job_id=job_id,
            headers=payload.headers,
            resolution=payload.resolution,
            progress_cb=progress_cb,
            is_cancelled=is_cancelled_sync,
        )
        file_path = result.file_path
        file_path = apply_filename_override(file_path, payload.file_name)

        if await is_job_cancelled(job_id):
            raise DownloadCancelledError("Job canceled by user")

        await set_job(
            job_id,
            status="uploading",
            progress=96,
            stage="uploading",
            detail="Uploading to destination",
        )
        uploader = make_uploader(payload.upload_destination)
        destination = (payload.upload_destination or SETTINGS.upload_destination).lower().strip()

        def upload_progress_cb(progress: int, detail: str) -> None:
            if is_cancelled_sync():
                return
            upload_pct = max(0, min(100, progress))
            mapped = 96 + int((upload_pct * 3) / 100)
            loop.call_soon_threadsafe(
                asyncio.create_task,
                set_job(
                    job_id=job_id,
                    status="uploading",
                    progress=min(99, mapped),
                    stage="uploading",
                    detail=detail,
                )
            )

        if await is_job_cancelled(job_id):
            raise DownloadCancelledError("Job canceled by user")
        telegram_m3u8_mode = destination == "telegram" and _is_m3u8_source(url=str(payload.url))
        if telegram_m3u8_mode:
            if payload.thumbnail_url is None:
                raise RuntimeError(
                    "thumbnail_url is required for Telegram uploads when URL is m3u8"
                )

            thumbnail_raw_url = str(payload.thumbnail_url)
            await guard_url_for_ssrf(thumbnail_raw_url)
            await set_job(
                job_id,
                status="uploading",
                progress=96,
                stage="uploading",
                detail="Downloading and validating thumbnail",
            )
            thumbnail_path = await asyncio.to_thread(
                download_and_validate_thumbnail,
                thumbnail_raw_url,
                tmp_dir=SETTINGS.tmp_dir,
                timeout_seconds=SETTINGS.request_timeout_seconds,
            )

        if destination == "telegram" and isinstance(uploader, TelegramUploader):
            upload_file_name = file_path.name
            force_document = False
            if telegram_m3u8_mode and file_path.suffix.lower() == ".mp4":
                upload_file_name = f"{file_path.stem}.mkv"
                force_document = True

            uploaded_url = await uploader.upload(
                file_path,
                progress_cb=upload_progress_cb,
                is_cancelled=is_cancelled_sync,
                thumb_path=thumbnail_path,
                force_document=force_document,
                upload_file_name=upload_file_name,
                use_filename_as_caption=not telegram_m3u8_mode,
            )
        else:
            uploaded_url = await uploader.upload(
                file_path,
                progress_cb=upload_progress_cb,
                is_cancelled=is_cancelled_sync,
            )

        await set_job(
            job_id,
            status="completed",
            progress=100,
            stage="completed",
            detail="Upload completed",
            result_url=uploaded_url,
        )
    except DownloadCancelledError as exc:
        APP_LOGGER.info("job canceled", extra={"job_id": job_id})
        await set_job(
            job_id,
            status="canceled",
            stage="canceled",
            progress=0,
            error=str(exc),
            detail="Canceled by user",
        )
    except DownloadTooLargeError as exc:
        APP_LOGGER.warning("job failed size limit", extra={"job_id": job_id, "error": str(exc)})
        await set_job(
            job_id,
            status="failed",
            stage="failed",
            progress=0,
            error=f"File exceeds max size of {SETTINGS.max_file_size_mb}MB",
            detail=str(exc),
        )
    except (DownloadFailedError, RuntimeError, HTTPException) as exc:
        APP_LOGGER.exception("job failed", extra={"job_id": job_id})
        await set_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(exc),
            detail="Processing failed",
        )
    except Exception as exc:
        APP_LOGGER.exception("unexpected job failure", extra={"job_id": job_id})
        await set_job(
            job_id,
            status="failed",
            stage="failed",
            error="Unexpected server error",
            detail=str(exc),
        )
    finally:
        if thumbnail_path and thumbnail_path.exists():
            try:
                thumbnail_path.unlink(missing_ok=True)
            except Exception:
                APP_LOGGER.warning("Failed cleaning temp thumbnail", extra={"path": str(thumbnail_path)})
        if file_path and file_path.exists():
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                APP_LOGGER.warning("Failed cleaning temp file", extra={"path": str(file_path)})


def _is_m3u8_source(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".m3u8")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "max_size_mb": SETTINGS.max_file_size_mb,
            "upload_destination": SETTINGS.upload_destination,
        },
    )


@app.post("/media-info", response_model=MediaInfoResponse)
async def media_info(payload: MediaInfoRequest) -> MediaInfoResponse:
    url = str(payload.url)
    await guard_url_for_ssrf(url)
    try:
        title, is_youtube = await downloader.probe_media(url=url, headers=payload.headers)
    except DownloadFailedError as exc:
        raise HTTPException(status_code=422, detail=f"Failed to inspect media: {exc}") from exc
    return MediaInfoResponse(title=title, is_youtube=is_youtube)


@app.post("/download", response_model=DownloadCreateResponse)
async def create_download(payload: DownloadRequest) -> DownloadCreateResponse:
    url = str(payload.url)
    await guard_url_for_ssrf(url)
    try:
        make_uploader(payload.upload_destination)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex
    async with jobs_lock:
        jobs[job_id] = JobState(job_id=job_id)

    asyncio.create_task(process_job(job_id, payload))
    return DownloadCreateResponse(job_id=job_id, status="queued")


@app.get("/status/{job_id}", response_model=StatusResponse)
async def status(job_id: str) -> StatusResponse:
    async with jobs_lock:
        state = jobs.get(job_id)
        if not state:
            raise HTTPException(status_code=404, detail="Job not found")

        return StatusResponse(
            job_id=state.job_id,
            status=state.status,
            progress=state.progress,
            stage=state.stage,
            detail=state.detail,
            result_url=state.result_url,
            error=state.error,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )


@app.post("/cancel/{job_id}")
async def cancel_job(job_id: str) -> dict[str, str]:
    async with jobs_lock:
        state = jobs.get(job_id)
        if not state:
            raise HTTPException(status_code=404, detail="Job not found")
        if state.status in {"completed", "failed", "canceled"}:
            return {"job_id": job_id, "status": state.status}
        state.cancel_requested = True
        state.cancel_event.set()
        state.touch()
    return {"job_id": job_id, "status": "cancel_requested"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": SETTINGS.app_name}
