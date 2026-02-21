from __future__ import annotations

import uuid
from pathlib import Path
from urllib.request import Request, urlopen


MAX_THUMBNAIL_BYTES = 512 * 1024
MAX_THUMBNAIL_DIMENSION = 320


def _jpeg_dimensions(image_bytes: bytes) -> tuple[int, int]:
    if len(image_bytes) < 4 or image_bytes[0:2] != b"\xFF\xD8":
        raise ValueError("Thumbnail must be a JPEG image")

    i = 2
    while i + 1 < len(image_bytes):
        while i < len(image_bytes) and image_bytes[i] != 0xFF:
            i += 1
        while i < len(image_bytes) and image_bytes[i] == 0xFF:
            i += 1
        if i >= len(image_bytes):
            break

        marker = image_bytes[i]
        i += 1

        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue

        if i + 1 >= len(image_bytes):
            break

        seg_len = (image_bytes[i] << 8) + image_bytes[i + 1]
        if seg_len < 2:
            raise ValueError("Invalid JPEG thumbnail")

        segment_start = i + 2
        segment_end = i + seg_len
        if segment_end > len(image_bytes):
            raise ValueError("Corrupt JPEG thumbnail")

        if marker in {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF
        }:
            if segment_start + 4 >= segment_end:
                raise ValueError("Invalid JPEG thumbnail dimensions")
            height = (image_bytes[segment_start + 1] << 8) + image_bytes[segment_start + 2]
            width = (image_bytes[segment_start + 3] << 8) + image_bytes[segment_start + 4]
            if width <= 0 or height <= 0:
                raise ValueError("Invalid JPEG thumbnail dimensions")
            return width, height

        i = segment_end

    raise ValueError("Unable to read JPEG thumbnail dimensions")


def download_and_validate_thumbnail(
    thumbnail_url: str,
    *,
    tmp_dir: Path,
    timeout_seconds: float = 20.0,
) -> Path:
    request = Request(
        thumbnail_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
            ),
            "Accept": "image/jpeg,image/jpg;q=0.9,*/*;q=0.1",
        },
    )

    with urlopen(request, timeout=timeout_seconds) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "jpeg" not in content_type and "jpg" not in content_type:
            raise ValueError("Thumbnail URL must return a JPEG image")

        raw = response.read(MAX_THUMBNAIL_BYTES + 1)
        if len(raw) > MAX_THUMBNAIL_BYTES:
            raise ValueError("Thumbnail image is too large")

    width, height = _jpeg_dimensions(raw)
    if width > MAX_THUMBNAIL_DIMENSION or height > MAX_THUMBNAIL_DIMENSION:
        raise ValueError("Thumbnail must be 320px or less in both width and height")

    thumb_path = tmp_dir / f"thumb_{uuid.uuid4().hex}.jpg"
    thumb_path.write_bytes(raw)
    return thumb_path
