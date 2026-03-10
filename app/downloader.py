# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import yt_dlp

from app.config import Settings
from app.format_selector import build_format_options
from app.utils import safe_filename


LOGGER = logging.getLogger("tele_port.downloader")

AUDIO_EXTS = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "webm"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "avif"}
DIRECT_MEDIA_EXTS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".m3u8",
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".flac",
}
SOCIAL_EXTRACTORS = {
    "facebook",
    "twitter",
    "tiktok",
    "instagram",
    "threads",
}
SOCIAL_DOMAINS = {
    "facebook.com",
    "x.com",
    "twitter.com",
    "tiktok.com",
    "instagram.com",
    "threads.net",
}


class DownloadTooLargeError(Exception):
    pass


class DownloadFailedError(Exception):
    pass


class DownloadCancelledError(Exception):
    pass


@dataclass(slots=True)
class DownloadResult:
    file_path: Path
    title: str
    extractor: str | None


@dataclass(slots=True)
class SourceProfile:
    is_youtube: bool
    is_social_non_youtube: bool
    is_embed_or_cdn: bool
    is_audio: bool
    is_image: bool


class Downloader:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def download(
        self,
        url: str,
        job_id: str,
        headers: dict[str, str] | None,
        resolution: int | None,
        format_id: str | None,
        audio_format_id: str | None,
        progress_cb: Callable[[int, str], None],
        is_cancelled: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        return await asyncio.to_thread(
            self._download_sync,
            url,
            job_id,
            headers,
            resolution,
            format_id,
            audio_format_id,
            progress_cb,
            is_cancelled,
        )

    async def probe_media(self, url: str, headers: dict[str, str] | None = None) -> tuple[str, bool]:
        return await asyncio.to_thread(self._probe_media_sync, url, headers)

    async def extract_formats(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> list[dict[str, str | int | None]]:
        return await asyncio.to_thread(self._extract_formats_sync, url, headers)

    def _download_sync(
        self,
        url: str,
        job_id: str,
        headers: dict[str, str] | None,
        resolution: int | None,
        format_id: str | None,
        audio_format_id: str | None,
        progress_cb: Callable[[int, str], None],
        is_cancelled: Callable[[], bool] | None,
    ) -> DownloadResult:
        http_headers = self._build_headers(url, headers)

        try:
            return self._download_with_ytdlp(
                url=url,
                job_id=job_id,
                http_headers=http_headers,
                requested_resolution=resolution,
                selected_format_id=format_id,
                selected_audio_format_id=audio_format_id,
                progress_cb=progress_cb,
                is_cancelled=is_cancelled,
            )
        except DownloadCancelledError:
            raise
        except DownloadTooLargeError:
            raise
        except yt_dlp.utils.DownloadError as exc:
            if not self._should_fallback_to_raw(exc):
                raise DownloadFailedError(str(exc)) from exc
            LOGGER.warning("yt-dlp extractor failed, switching to raw HTTP fallback: %s", exc)
            return self._download_with_raw_http(url, job_id, http_headers, progress_cb, is_cancelled)
        except DownloadFailedError:
            raise
        except Exception as exc:
            LOGGER.exception("Unexpected downloader error")
            raise DownloadFailedError(str(exc)) from exc

    def _probe_media_sync(self, url: str, headers: dict[str, str] | None) -> tuple[str, bool]:
        http_headers = self._build_headers(url, headers)
        info = self._extract_single_info(url, self._build_probe_opts(http_headers, force_generic=False))
        if info is None:
            raise DownloadFailedError("Could not fetch media info")
        profile = self._classify_source(url, info)
        title = (info.get("title") or "media_file").strip()
        return title, profile.is_youtube

    def _extract_formats_sync(
        self,
        url: str,
        headers: dict[str, str] | None,
    ) -> list[dict[str, str | int | None]]:
        http_headers = self._build_headers(url, headers)
        info = self._extract_single_info(url, self._build_probe_opts(http_headers, force_generic=False))
        if info is None:
            raise DownloadFailedError("Could not fetch media info")

        raw_formats: list[dict] = info.get("formats") or []
        if not raw_formats:
            return []

        return build_format_options(raw_formats)

    def _build_headers(self, url: str, custom_headers: dict[str, str] | None) -> dict[str, str]:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
            referer = f"{origin}/"
        else:
            origin = "https://www.google.com"
            referer = "https://www.google.com/"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "DNT": "1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Upgrade-Insecure-Requests": "1",
            "Origin": origin,
            "Referer": referer,
        }
        if custom_headers:
            headers.update(custom_headers)
        if not headers.get("Referer"):
            headers["Referer"] = referer
        if not headers.get("Origin"):
            headers["Origin"] = origin
        return headers

    def _download_with_ytdlp(
        self,
        url: str,
        job_id: str,
        http_headers: dict[str, str],
        requested_resolution: int | None,
        selected_format_id: str | None,
        selected_audio_format_id: str | None,
        progress_cb: Callable[[int, str], None],
        is_cancelled: Callable[[], bool] | None,
    ) -> DownloadResult:
        outtmpl = str(self.settings.tmp_dir / "%(title).180B.%(ext)s")
        emit_state = {"last_emit": 0.0}

        def progress_hook(data: dict) -> None:
            if is_cancelled and is_cancelled():
                raise DownloadCancelledError("Download canceled by user")

            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                downloaded = int(data.get("downloaded_bytes") or 0)
                speed = float(data.get("speed") or 0.0)
                eta = int(data.get("eta") or 0)

                if downloaded > self.settings.max_file_size_bytes:
                    raise DownloadTooLargeError(
                        f"File exceeds {self.settings.max_file_size_mb}MB during download"
                    )

                percent = int((downloaded / total) * 100) if total else 0
                capped = min(95, max(0, percent))
                detail = (
                    f"{capped}% | speed={speed / (1024 * 1024):.2f} MB/s | "
                    f"ETA={self._format_eta(eta)} | size={(total / (1024 * 1024)):.2f} MB"
                    if total
                    else f"{capped}% | speed={speed / (1024 * 1024):.2f} MB/s"
                )

                now = time.time()
                if now - emit_state["last_emit"] >= 0.8:
                    emit_state["last_emit"] = now
                    progress_cb(capped, detail)

            elif status == "finished":
                progress_cb(95, "Download completed, preparing upload")

        probe_opts = self._build_probe_opts(http_headers, force_generic=False)
        info = self._extract_single_info(url, probe_opts)
        if info is None:
            raise DownloadFailedError("Could not extract media information")

        profile = self._classify_source(url, info)
        if profile.is_embed_or_cdn:
            info = self._extract_single_info(
                url,
                self._build_probe_opts(http_headers, force_generic=True),
            ) or info
            profile = self._classify_source(url, info)

        explicit_selector = self._format_selector_from_choice(
            format_id=selected_format_id,
            audio_format_id=selected_audio_format_id,
        )

        if self._is_m3u8_url(url):
            return self._download_m3u8(
                url=url,
                info=info,
                selected_format_selector=explicit_selector,
                outtmpl=outtmpl,
                http_headers=http_headers,
                progress_hook=progress_hook,
                is_cancelled=is_cancelled,
                profile=profile,
                job_id=job_id,
            )

        if profile.is_youtube and explicit_selector is None:
            resolutions = self._available_resolutions(info)
            if not resolutions:
                raise DownloadFailedError("No downloadable YouTube video resolutions were found")
            if requested_resolution is None:
                resolution_msg = ", ".join(str(res) for res in resolutions[:16])
                raise DownloadFailedError(
                    f"YouTube resolution is required. Available resolutions: {resolution_msg}. "
                    f"Retry with 'resolution' in request body."
                )
            highest_at_or_below = next((res for res in resolutions if res <= requested_resolution), None)
            selected_resolution = highest_at_or_below or resolutions[-1]
            progress_cb(2, f"YouTube detected: downloading video+audio at {selected_resolution}p")
            requested_resolution = selected_resolution
        elif profile.is_youtube and explicit_selector is not None:
            progress_cb(2, "YouTube detected: downloading selected format")

        force_mp4_output = profile.is_youtube and explicit_selector is None

        self._assert_size_within_limit(info)
        if explicit_selector:
            format_candidates = [explicit_selector]
        else:
            format_candidates = self._format_candidates(profile, requested_resolution=requested_resolution)

        final_info: dict | None = None
        prepared_path: Path | None = None
        last_download_error: Exception | None = None

        for idx, format_selector in enumerate(format_candidates, start=1):
            if is_cancelled and is_cancelled():
                raise DownloadCancelledError("Download canceled by user")
            progress_cb(3, f"Selecting format strategy {idx}/{len(format_candidates)}")
            ydl_opts = self._build_download_opts(
                outtmpl=outtmpl,
                http_headers=http_headers,
                progress_hook=progress_hook,
                format_selector=format_selector,
                audio_only=profile.is_audio,
                force_generic=profile.is_embed_or_cdn,
                force_mp4=force_mp4_output,
            )
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    downloaded_info = ydl.extract_info(url, download=True)
                    final_info = self._pick_primary_entry(downloaded_info)
                    prepared_path = self._path_from_download_info(ydl, final_info or info)
                if prepared_path:
                    break
            except DownloadTooLargeError:
                raise
            except yt_dlp.utils.DownloadError as exc:
                LOGGER.warning("Download attempt failed (%s): %s", format_selector, exc)
                last_download_error = exc

        if prepared_path is None:
            raise DownloadFailedError(str(last_download_error or "Unable to download media"))

        candidate = self._resolve_downloaded_file(prepared_path)
        metadata = final_info or info
        title = (metadata.get("title") or f"media_{safe_filename(job_id)}").strip()
        candidate = self._rename_to_title(candidate, title)
        self._assert_final_size(candidate)

        extractor = metadata.get("extractor")
        return DownloadResult(file_path=candidate, title=title, extractor=extractor)

    def _download_m3u8(
        self,
        url: str,
        info: dict,
        selected_format_selector: str | None,
        outtmpl: str,
        http_headers: dict[str, str],
        progress_hook: Callable[[dict], None],
        is_cancelled: Callable[[], bool] | None,
        profile: SourceProfile,
        job_id: str,
    ) -> DownloadResult:
        if is_cancelled and is_cancelled():
            raise DownloadCancelledError("Download canceled by user")

        ydl_opts = self._build_download_opts(
            outtmpl=outtmpl,
            http_headers=http_headers,
            progress_hook=progress_hook,
            format_selector=selected_format_selector or "bestvideo*+bestaudio/best",
            audio_only=profile.is_audio,
            force_generic=profile.is_embed_or_cdn,
            force_mp4=False,
        )
        ydl_opts.update(
            {
                "hls_prefer_native": False,
                "hls_use_mpegts": True,
                "external_downloader": "ffmpeg",
                "external_downloader_args": {
                    "ffmpeg_i": ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
                },
            }
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                downloaded_info = ydl.extract_info(url, download=True)
                final_info = self._pick_primary_entry(downloaded_info) or info
                prepared_path = self._path_from_download_info(ydl, final_info)
        except DownloadTooLargeError:
            raise
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadFailedError(f"m3u8 download failed: {exc}") from exc

        candidate = self._resolve_downloaded_file(prepared_path)
        title = (final_info.get("title") or f"media_{safe_filename(job_id)}").strip()
        candidate = self._rename_to_title(candidate, title)
        self._assert_final_size(candidate)

        return DownloadResult(file_path=candidate, title=title, extractor=final_info.get("extractor"))

    def _build_probe_opts(self, http_headers: dict[str, str], force_generic: bool) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "http_headers": http_headers,
            "socket_timeout": self.settings.request_timeout_seconds,
            "extractor_retries": self.settings.download_retry_count,
            "cachedir": False,
        }
        if force_generic:
            opts["force_generic_extractor"] = True
        return opts

    def _extract_single_info(self, url: str, opts: dict) -> dict | None:
        try:
            with yt_dlp.YoutubeDL(opts) as probe_ydl:
                info = probe_ydl.extract_info(url, download=False)
            return self._pick_primary_entry(info)
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadFailedError(str(exc)) from exc
        except Exception as exc:
            raise DownloadFailedError(f"yt-dlp probe failed: {exc}") from exc

    def _pick_primary_entry(self, info: dict | None) -> dict | None:
        if info is None:
            return None
        entries = info.get("entries")
        if isinstance(entries, list) and entries:
            for entry in entries:
                if entry:
                    return entry
            return None
        return info

    def _build_download_opts(
        self,
        outtmpl: str,
        http_headers: dict[str, str],
        progress_hook: Callable[[dict], None],
        format_selector: str,
        audio_only: bool,
        force_generic: bool,
        force_mp4: bool,
    ) -> dict:
        opts: dict = {
            "format": format_selector,
            "outtmpl": outtmpl,
            "paths": {"home": str(self.settings.tmp_dir)},
            "noplaylist": True,
            "retries": self.settings.download_retry_count,
            "fragment_retries": self.settings.download_retry_count,
            "continuedl": True,
            "concurrent_fragment_downloads": 3,
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": True,
            "windowsfilenames": True,
            "progress_hooks": [progress_hook],
            "http_headers": http_headers,
            "socket_timeout": self.settings.request_timeout_seconds,
            "extractor_retries": self.settings.download_retry_count,
            "overwrites": False,
            "nopart": False,
            "cachedir": False,
            "max_filesize": self.settings.max_file_size_bytes,
            "prefer_ffmpeg": True,
            "postprocessor_args": {"Merger": ["-c:v", "copy", "-c:a", "copy"]},
        }
        if force_generic:
            opts["force_generic_extractor"] = True
        if force_mp4:
            opts["merge_output_format"] = "mp4"
        return opts

    def _is_m3u8_url(self, url: str) -> bool:
        return urlparse(url).path.lower().endswith(".m3u8")

    def _classify_source(self, url: str, info: dict) -> SourceProfile:
        extractor = (info.get("extractor") or "").lower()
        ext = (info.get("ext") or "").lower()
        url_lower = url.lower()
        domain = (info.get("webpage_url_domain") or urlparse(url).netloc or "").lower()
        path = urlparse(url).path.lower()

        is_youtube = "youtube" in extractor or "youtu" in domain
        is_social_non_youtube = (not is_youtube) and (
            any(name in extractor for name in SOCIAL_EXTRACTORS)
            or any(site in domain for site in SOCIAL_DOMAINS)
        )
        is_embed_or_cdn = (
            any(token in url_lower for token in ("iframe", "embed", "/e/", "/v/", "player"))
            or any(token in domain for token in ("cdn", "akamaized", "cloudfront", "fastly"))
            or any(path.endswith(ext_hint) for ext_hint in DIRECT_MEDIA_EXTS)
        )
        is_audio = ext in AUDIO_EXTS or info.get("vcodec") == "none"
        is_image = ext in IMAGE_EXTS or info.get("_type") == "image"

        return SourceProfile(
            is_youtube=is_youtube,
            is_social_non_youtube=is_social_non_youtube,
            is_embed_or_cdn=is_embed_or_cdn,
            is_audio=is_audio,
            is_image=is_image,
        )

    def _format_candidates(self, profile: SourceProfile, requested_resolution: int | None) -> list[str]:
        if profile.is_image:
            return ["best"]
        if profile.is_youtube:
            if requested_resolution is None:
                raise DownloadFailedError("YouTube resolution is required")
            return [
                (
                    f"bestvideo[height<={requested_resolution}][vcodec!=none]"
                    f"+bestaudio[acodec!=none]/best[height<={requested_resolution}]/best"
                ),
                f"bestvideo*[height<={requested_resolution}]+bestaudio/best[height<={requested_resolution}]",
                "bestvideo*+bestaudio/best",
            ]
        if profile.is_audio:
            return ["bestaudio/best"]
        if profile.is_social_non_youtube:
            return [
                "bestvideo*+bestaudio/bestvideo+bestaudio",
                "best",
            ]
        if profile.is_embed_or_cdn:
            return [
                "bestvideo*+bestaudio/bestvideo+bestaudio/best",
                "best",
            ]
        return [
            "bestvideo*+bestaudio/bestvideo+bestaudio/best",
            "best",
        ]

    def _format_selector_from_choice(self, format_id: str | None, audio_format_id: str | None) -> str | None:
        if not format_id:
            return None
        clean_video_id = format_id.strip()
        if not clean_video_id:
            return None
        clean_audio_id = (audio_format_id or "").strip()
        if clean_audio_id:
            return f"{clean_video_id}+{clean_audio_id}"
        return clean_video_id

    def _available_resolutions(self, info: dict) -> list[int]:
        formats = info.get("formats") or []
        heights = {
            int(fmt.get("height"))
            for fmt in formats
            if fmt.get("height")
            and fmt.get("vcodec") not in (None, "none")
            and str(fmt.get("height")).isdigit()
        }
        return sorted(heights, reverse=True)

    def _assert_size_within_limit(self, info: dict) -> None:
        size_candidates = [info.get("filesize"), info.get("filesize_approx")]
        formats = info.get("formats") or []
        size_candidates.extend(f.get("filesize") or f.get("filesize_approx") for f in formats)
        known_sizes = [s for s in size_candidates if isinstance(s, int) and s > 0]
        if known_sizes and min(known_sizes) > self.settings.max_file_size_bytes:
            raise DownloadTooLargeError(f"Media is larger than {self.settings.max_file_size_mb}MB")

    def _path_from_download_info(self, ydl: yt_dlp.YoutubeDL, info: dict) -> Path:
        final_filepath = info.get("filepath")
        if final_filepath:
            return Path(str(final_filepath))
        requested = info.get("requested_downloads")
        if isinstance(requested, list) and len(requested) == 1:
            filepath = requested[0].get("filepath")
            if filepath:
                return Path(filepath)
        if info.get("_filename"):
            return Path(str(info["_filename"]))
        return Path(ydl.prepare_filename(info))

    def _resolve_downloaded_file(self, prepared_path: Path) -> Path:
        if prepared_path.exists():
            return prepared_path
        stem = prepared_path.with_suffix("")
        matches = list(stem.parent.glob(f"{stem.name}.*"))
        if not matches:
            raise DownloadFailedError("Downloaded file not found")
        non_temp = [
            p for p in matches
            if p.suffix.lower() not in {".part", ".ytdl", ".tmp"}
            and ".f" not in p.stem
        ]
        candidates = non_temp or matches
        if prepared_path.suffix:
            preferred_ext = prepared_path.suffix.lower()
            exact_ext = [p for p in candidates if p.suffix.lower() == preferred_ext]
            if exact_ext:
                candidates = exact_ext
        return max(candidates, key=lambda p: p.stat().st_mtime)

    def _rename_to_title(self, file_path: Path, title: str) -> Path:
        safe_stem = safe_filename(title or file_path.stem)
        safe_name = f"{safe_stem}{file_path.suffix}"
        if safe_name == file_path.name:
            return file_path

        target = file_path.with_name(safe_name)
        if target.exists():
            target = file_path.with_name(f"{safe_stem}_{uuid.uuid4().hex[:6]}{file_path.suffix}")
        file_path.rename(target)
        return target

    def _assert_final_size(self, file_path: Path) -> None:
        final_size = file_path.stat().st_size
        if final_size > self.settings.max_file_size_bytes:
            file_path.unlink(missing_ok=True)
            raise DownloadTooLargeError(f"Final file exceeds {self.settings.max_file_size_mb}MB")

    def _download_with_raw_http(
        self,
        url: str,
        job_id: str,
        http_headers: dict[str, str],
        progress_cb: Callable[[int, str], None],
        is_cancelled: Callable[[], bool] | None,
    ) -> DownloadResult:
        request = Request(url, headers=http_headers, method="GET")
        start = time.time()
        emit_state = {"last_emit": 0.0}

        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as resp:
                content_length_raw = resp.headers.get("Content-Length")
                total = int(content_length_raw) if content_length_raw and content_length_raw.isdigit() else 0
                if total and total > self.settings.max_file_size_bytes:
                    raise DownloadTooLargeError(
                        f"File exceeds max size of {self.settings.max_file_size_mb}MB"
                    )

                filename = self._derive_filename_from_response(url, resp.headers, job_id)
                file_path = self.settings.tmp_dir / filename
                downloaded = 0
                chunk_size = 1024 * 1024

                with file_path.open("wb") as out:
                    while True:
                        if is_cancelled and is_cancelled():
                            out.close()
                            file_path.unlink(missing_ok=True)
                            raise DownloadCancelledError("Download canceled by user")
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)

                        if downloaded > self.settings.max_file_size_bytes:
                            out.close()
                            file_path.unlink(missing_ok=True)
                            raise DownloadTooLargeError(
                                f"File exceeds max size of {self.settings.max_file_size_mb}MB"
                            )

                        now = time.time()
                        if now - emit_state["last_emit"] < 0.8:
                            continue
                        emit_state["last_emit"] = now

                        elapsed = max(now - start, 0.001)
                        speed = downloaded / elapsed
                        eta = int((total - downloaded) / speed) if total and speed > 0 else 0
                        percent = int((downloaded / total) * 100) if total else 0
                        capped = min(95, max(0, percent))
                        detail = (
                            f"{capped}% | speed={speed / (1024 * 1024):.2f} MB/s | "
                            f"ETA={self._format_eta(eta)} | size={total / (1024 * 1024):.2f} MB"
                            if total
                            else f"Downloading raw file | speed={speed / (1024 * 1024):.2f} MB/s"
                        )
                        progress_cb(capped, detail)

                progress_cb(95, "Raw HTTP download completed, preparing upload")
                self._assert_final_size(file_path)
                return DownloadResult(
                    file_path=file_path,
                    title=file_path.stem,
                    extractor="raw_http",
                )
        except (DownloadTooLargeError, DownloadCancelledError):
            raise
        except Exception as exc:
            raise DownloadFailedError(f"Raw HTTP fallback failed: {exc}") from exc

    def _derive_filename_from_response(self, url: str, headers: dict, job_id: str) -> str:
        disposition = headers.get("Content-Disposition", "")
        quoted = re.search(r"filename\*=UTF-8''([^;]+)", disposition)
        plain = re.search(r'filename="?([^";]+)"?', disposition)

        if quoted:
            base_name = unquote(quoted.group(1))
        elif plain:
            base_name = plain.group(1)
        else:
            path_name = Path(urlparse(url).path).name
            base_name = path_name or f"media_{job_id}"

        ext = Path(base_name).suffix
        stem = safe_filename(Path(base_name).stem or f"media_{job_id}")
        if not ext:
            mime = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext_map = {
                "video/mp4": ".mp4",
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }
            ext = ext_map.get(mime, ".bin")

        return f"{stem}{ext}"

    def _format_eta(self, eta_seconds: int) -> str:
        if eta_seconds <= 0:
            return "--"
        mins, secs = divmod(eta_seconds, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            return f"{hours:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

    def _should_fallback_to_raw(self, exc: Exception) -> bool:
        message = str(exc).lower()
        patterns = (
            "unsupported url",
            "no suitable extractor",
            "unable to download webpage",
            "url could be a direct",
            "not a valid url",
        )
        return any(p in message for p in patterns)
