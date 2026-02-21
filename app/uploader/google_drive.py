# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Callable

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from app.downloader import DownloadCancelledError


LOGGER = logging.getLogger("tele_port.uploader.google_drive")

# Must be a multiple of 256KB for resumable uploads.
CHUNK_SIZE = 8 * 1024 * 1024
MAX_RESUMABLE_RETRIES = 8
MAX_BACKOFF_SECONDS = 30.0


class GoogleDriveUploader:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._credentials: Credentials | None = None

    async def upload(
        self,
        file_path: Path,
        mime_type: str = "application/octet-stream",
        progress_cb: Callable[[int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> str:
        return await asyncio.to_thread(self._upload_sync, file_path, mime_type, progress_cb, is_cancelled)

    def _build_credentials(self) -> Credentials:
        if self._credentials is None:
            self._credentials = Credentials(
                token=None,
                refresh_token=self.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=["https://www.googleapis.com/auth/drive.file"],
            )

        if not self._credentials.valid:
            self._credentials.refresh(Request())

        return self._credentials

    def _is_retriable_http_error(self, exc: HttpError) -> bool:
        status = getattr(exc.resp, "status", None)
        return status in {403, 408, 409, 429, 500, 502, 503, 504}

    def _backoff_sleep(self, retry_count: int) -> None:
        sleep_for = min((2**retry_count) + random.random(), MAX_BACKOFF_SECONDS)
        time.sleep(sleep_for)

    def _upload_sync(
        self,
        file_path: Path,
        mime_type: str,
        progress_cb: Callable[[int, str], None] | None,
        is_cancelled: Callable[[], bool] | None,
    ) -> str:
        creds = self._build_credentials()
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        metadata = {"name": file_path.name}
        media = MediaFileUpload(
            str(file_path),
            mimetype=mime_type,
            resumable=True,
            chunksize=CHUNK_SIZE,
        )

        request = service.files().create(
            body=metadata,
            media_body=media,
            fields="id, webViewLink, webContentLink",
        )

        created: dict | None = None
        retries = 0
        last_progress_emit = 0.0
        while created is None:
            if is_cancelled and is_cancelled():
                raise DownloadCancelledError("Upload canceled by user")
            try:
                status, response = request.next_chunk()
                if status is not None:
                    now = time.time()
                    if now - last_progress_emit >= 1.0:
                        last_progress_emit = now
                        progress = float(status.progress() or 0.0) * 100.0
                        progress_int = max(0, min(100, int(progress)))
                        LOGGER.info(
                            "Drive upload progress file=%s pct=%.1f",
                            file_path.name,
                            progress,
                        )
                        if progress_cb:
                            progress_cb(progress_int, f"Drive upload {progress:.1f}%")
                if response is not None:
                    created = response
                    break
            except HttpError as exc:
                if not self._is_retriable_http_error(exc) or retries >= MAX_RESUMABLE_RETRIES:
                    raise
                retries += 1
                LOGGER.warning(
                    "Retriable Drive upload HTTP error (attempt %s/%s): %s",
                    retries,
                    MAX_RESUMABLE_RETRIES,
                    exc,
                )
                self._backoff_sleep(retries)
            except (httplib2.HttpLib2Error, OSError, TimeoutError, ConnectionError) as exc:
                if retries >= MAX_RESUMABLE_RETRIES:
                    raise
                retries += 1
                LOGGER.warning(
                    "Retriable Drive upload transport error (attempt %s/%s): %s",
                    retries,
                    MAX_RESUMABLE_RETRIES,
                    exc,
                )
                self._backoff_sleep(retries)

        if not created:
            raise RuntimeError("Google Drive upload did not return a file response")

        file_id = created["id"]
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute(num_retries=3)

        return (
            created.get("webViewLink")
            or created.get("webContentLink")
            or f"https://drive.google.com/file/d/{file_id}/view"
        )
