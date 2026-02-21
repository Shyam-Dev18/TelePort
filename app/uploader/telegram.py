# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable

from pyrogram import Client

from app.downloader import DownloadCancelledError


LOGGER = logging.getLogger("tele_port.uploader.telegram")

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


class TelegramUploader:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        bot_token: str,
        target_chat: str,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        self.target_chat = target_chat
        self._client = Client(
            name="tele_port_worker",
            api_id=self.api_id,
            api_hash=self.api_hash,
            bot_token=self.bot_token,
            in_memory=True,
            no_updates=True,
        )
        self._start_lock = asyncio.Lock()
        self._started = False

        if isinstance(target_chat, str):
            if target_chat.startswith("-100") or target_chat.isdigit():
                self.target_chat = int(target_chat)
            else:
                self.target_chat = target_chat
        else:
            self.target_chat = target_chat

    async def _ensure_started(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if not self._started:
                await self._client.start()
                self._started = True

    def _progress_logger(
        self,
        media_name: str,
        progress_cb: Callable[[int, str], None] | None,
        is_cancelled: Callable[[], bool] | None,
    ):
        state = {"last_emit": 0.0}

        def callback(current: int, total: int) -> None:
            if is_cancelled and is_cancelled():
                raise DownloadCancelledError("Upload canceled by user")

            now = time.time()
            if now - state["last_emit"] < 1.0:
                return
            state["last_emit"] = now
            if total > 0:
                pct = (current / total) * 100
                LOGGER.info(
                    "Telegram upload progress media=%s current=%s total=%s pct=%.1f",
                    media_name,
                    current,
                    total,
                    pct,
                )
                if progress_cb:
                    progress_cb(max(0, min(100, int(pct))), f"Telegram upload {pct:.1f}%")
            else:
                LOGGER.info("Telegram upload progress media=%s current=%s", media_name, current)

        return callback

    async def close(self) -> None:
        async with self._start_lock:
            if self._started:
                await self._client.stop()
                self._started = False

    async def upload(
        self,
        file_path: Path,
        caption: str | None = None,
        progress_cb: Callable[[int, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        thumb_path: Path | None = None,
        force_document: bool = False,
        upload_file_name: str | None = None,
        use_filename_as_caption: bool = True,
    ) -> str:
        await self._ensure_started()
        if is_cancelled and is_cancelled():
            raise DownloadCancelledError("Upload canceled by user")

        suffix = file_path.suffix.lower()
        if caption is not None:
            send_caption: str | None = caption[:1024]
        elif use_filename_as_caption:
            send_caption = file_path.name[:1024]
        else:
            send_caption = None
        progress = self._progress_logger(file_path.name, progress_cb, is_cancelled)
        thumb_str = str(thumb_path) if thumb_path else None
        upload_name = upload_file_name or file_path.name

        if force_document:
            message = await self._client.send_document(
                chat_id=self.target_chat,
                document=str(file_path),
                thumb=thumb_str,
                file_name=upload_name,
                caption=send_caption,
                disable_notification=True,
                progress=progress,
            )
        elif suffix in PHOTO_EXTS:
            message = await self._client.send_photo(
                chat_id=self.target_chat,
                photo=str(file_path),
                caption=send_caption,
                disable_notification=True,
                progress=progress,
            )
        elif suffix in VIDEO_EXTS:
            try:
                message = await self._client.send_video(
                    chat_id=self.target_chat,
                    video=str(file_path),
                    thumb=thumb_str,
                    caption=send_caption,
                    disable_notification=True,
                    supports_streaming=True,
                    progress=progress,
                )
            except DownloadCancelledError:
                raise
            except Exception:
                LOGGER.exception("send_video failed, falling back to send_document")
                message = await self._client.send_document(
                    chat_id=self.target_chat,
                    document=str(file_path),
                    thumb=thumb_str,
                    file_name=upload_name,
                    caption=send_caption,
                    disable_notification=True,
                    progress=progress,
                )
        else:
            message = await self._client.send_document(
                chat_id=self.target_chat,
                document=str(file_path),
                thumb=thumb_str,
                file_name=upload_name,
                caption=send_caption,
                disable_notification=True,
                progress=progress,
            )

        chat = message.chat
        if chat and chat.username and message.id:
            return f"https://t.me/{chat.username}/{message.id}"

        if chat and chat.id and str(chat.id).startswith("-100") and message.id:
            return f"https://t.me/c/{str(chat.id)[4:]}/{message.id}"

        return f"telegram://message/{message.id or 'unknown'}"
