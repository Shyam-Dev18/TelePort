# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class DownloadRequest(BaseModel):
    url: HttpUrl
    headers: dict[str, str] | None = None
    resolution: int | None = None
    format_id: str | None = None
    audio_format_id: str | None = None
    upload_destination: Literal["telegram", "google_drive"] | None = None
    file_name: str | None = None
    thumbnail_url: HttpUrl | None = None

    @field_validator("headers")
    @classmethod
    def limit_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value
        if len(value) > 20:
            raise ValueError("Too many custom headers")
        normalized = {k.strip(): v.strip() for k, v in value.items() if k.strip() and v.strip()}
        return normalized or None

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 144 or value > 4320:
            raise ValueError("Resolution must be between 144 and 4320")
        return value

    @field_validator("format_id", "audio_format_id")
    @classmethod
    def validate_format_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if len(trimmed) > 40:
            raise ValueError("Format identifier is too long")
        return trimmed

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if len(trimmed) > 180:
            raise ValueError("File name is too long")
        return trimmed

    @field_validator("thumbnail_url")
    @classmethod
    def validate_thumbnail_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is None:
            return None
        scheme = value.scheme.lower()
        if scheme not in {"http", "https"}:
            raise ValueError("Thumbnail URL must use http or https")
        return value


class DownloadCreateResponse(BaseModel):
    job_id: str
    status: str


class StatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "downloading", "uploading", "completed", "failed", "canceled"]
    progress: int = Field(ge=0, le=100)
    stage: str
    detail: str | None = None
    result_url: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class MediaInfoRequest(BaseModel):
    url: HttpUrl
    headers: dict[str, str] | None = None


class MediaInfoResponse(BaseModel):
    title: str
    is_youtube: bool


class FormatOption(BaseModel):
    display_index: int
    format_id: str
    audio_format_id: str | None = None
    resolution: str
    height: int
    vcodec_short: str
    acodec_short: str
    container: str
    filesize: int | None = None
    filesize_str: str
    codec_pair: str


class FormatsResponse(BaseModel):
    title: str
    is_youtube: bool
    formats: list[FormatOption]
