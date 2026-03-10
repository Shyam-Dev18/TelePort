from __future__ import annotations

from typing import Any

AUDIO_ONLY_EXTS = {"m4a", "mp3", "aac", "opus", "ogg", "flac", "wav", "weba", "mka"}


def _shorten_codec(codec: str | None) -> str:
    if not codec:
        return "-"
    value = codec.lower()
    if value in {"none", "unknown", ""}:
        return "-"
    if value.startswith("avc1") or value.startswith("h264"):
        return "h264"
    if value.startswith("hev1") or value.startswith("hvc1") or "hevc" in value:
        return "hevc"
    if value.startswith("vp09") or value.startswith("vp9"):
        return "vp9"
    if value.startswith("av01") or value.startswith("av1"):
        return "av1"
    if value.startswith("mp4a") or value == "aac":
        return "aac"
    if value == "opus":
        return "opus"
    return value.split(".")[0]


def _resolution_label(height: int | None) -> str:
    if not height:
        return "?"
    if height >= 4320:
        return "8K / 4320p"
    if height >= 2160:
        return "4K / 2160p"
    if height >= 1440:
        return "2K / 1440p"
    if height >= 1080:
        return "FHD / 1080p"
    if height >= 720:
        return "HD / 720p"
    if height >= 480:
        return "SD / 480p"
    return f"{height}p"


def _fmt_size_local(filesize: int | None) -> str:
    if filesize is None or filesize <= 0:
        return "~unknown"
    mb = filesize / (1024 * 1024)
    return f"{mb:.1f} MB"


def _is_video_format(raw: dict[str, Any]) -> bool:
    vcodec = (raw.get("vcodec") or "").lower()
    if vcodec and vcodec != "none":
        return True

    protocol = (raw.get("protocol") or "").lower()
    ext = (raw.get("ext") or "").lower()
    if "m3u8" in protocol or "dash" in protocol:
        if ext and ext not in AUDIO_ONLY_EXTS:
            return True
    return False


def _has_audio(raw: dict[str, Any]) -> bool:
    acodec = (raw.get("acodec") or "").lower()
    return bool(acodec) and acodec not in {"none", "unknown"}


def _codec_rank(vcodec_short: str) -> int:
    return {"h264": 0, "hevc": 1, "vp9": 2, "av1": 3}.get(vcodec_short, 99)


def _best_audio_for(video_raw: dict[str, Any], audio_raws: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not audio_raws:
        return None
    video_codec = _shorten_codec(video_raw.get("vcodec"))

    def _score(audio_format: dict[str, Any]) -> tuple[int, float]:
        audio_codec = _shorten_codec(audio_format.get("acodec"))
        bitrate = float(audio_format.get("abr") or audio_format.get("tbr") or 0)
        if video_codec == "h264":
            preference = 0 if audio_codec == "aac" else (1 if audio_codec == "opus" else 2)
        else:
            preference = 0 if audio_codec == "opus" else (1 if audio_codec == "aac" else 2)
        return (preference, -bitrate)

    return min(audio_raws, key=_score)


def _build_merged_entry(video_raw: dict[str, Any], audio_raw: dict[str, Any] | None) -> dict[str, Any]:
    height = int(video_raw.get("height") or 0)
    vcodec_raw = str(video_raw.get("vcodec") or "")

    video_has_audio = _has_audio(video_raw)
    if video_has_audio:
        acodec_raw = str(video_raw.get("acodec") or "")
        audio_format_id: str | None = None
        audio_size = 0
    elif audio_raw is not None:
        acodec_raw = str(audio_raw.get("acodec") or "")
        audio_format_id = str(audio_raw.get("format_id") or "") or None
        audio_size = int(audio_raw.get("filesize") or audio_raw.get("filesize_approx") or 0)
    else:
        acodec_raw = ""
        audio_format_id = None
        audio_size = 0

    vcodec_short = _shorten_codec(vcodec_raw)
    acodec_short = _shorten_codec(acodec_raw) if acodec_raw else "-"

    container = "mp4" if (vcodec_short == "h264" and acodec_short == "aac") else "mkv"

    video_size = int(video_raw.get("filesize") or video_raw.get("filesize_approx") or 0)
    total_size = (video_size + audio_size) or None

    return {
        "display_index": 0,
        "format_id": str(video_raw.get("format_id") or ""),
        "audio_format_id": audio_format_id,
        "resolution": _resolution_label(height),
        "height": height,
        "vcodec_short": vcodec_short,
        "acodec_short": acodec_short,
        "container": container,
        "filesize": total_size,
        "filesize_str": _fmt_size_local(total_size),
        "codec_pair": f"{vcodec_short} + {acodec_short}",
    }


def _dedup_formats(formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _entry_has_audio(format_entry: dict[str, Any]) -> bool:
        if format_entry.get("audio_format_id"):
            return True
        acs = str(format_entry.get("acodec_short") or "").strip()
        return acs not in {"", "-"}

    seen: dict[tuple[int, str], dict[str, Any]] = {}
    for format_entry in formats:
        key = (int(format_entry.get("height") or 0), str(format_entry.get("vcodec_short") or ""))
        if key not in seen:
            seen[key] = format_entry
            continue

        existing = seen[key]
        has_audio = _entry_has_audio(format_entry)
        existing_has_audio = _entry_has_audio(existing)

        if has_audio and not existing_has_audio:
            seen[key] = format_entry
            continue

        if has_audio == existing_has_audio:
            if int(format_entry.get("filesize") or 0) > int(existing.get("filesize") or 0):
                seen[key] = format_entry

    return list(seen.values())


def build_format_options(raw_formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    video_formats = [entry for entry in raw_formats if _is_video_format(entry)]
    audio_formats = [entry for entry in raw_formats if not _is_video_format(entry) and _has_audio(entry)]

    merged: list[dict[str, Any]] = []
    for video_format in video_formats:
        best_audio = None if _has_audio(video_format) else _best_audio_for(video_format, audio_formats)
        merged_entry = _build_merged_entry(video_format, best_audio)
        if merged_entry.get("format_id"):
            merged.append(merged_entry)

    merged = _dedup_formats(merged)
    merged.sort(key=lambda entry: (-int(entry["height"]), _codec_rank(str(entry["vcodec_short"]))))

    for idx, entry in enumerate(merged, start=1):
        entry["display_index"] = idx

    return merged
