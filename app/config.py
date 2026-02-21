# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError


load_dotenv()


class Settings(BaseModel):
    app_name: str = "tele_port"

    telegram_api_id: int | None = Field(default=(int(os.getenv("TELEGRAM_API_ID")) if os.getenv("TELEGRAM_API_ID") else None))
    telegram_api_hash: str | None = Field(default=os.getenv("TELEGRAM_API_HASH"))
    telegram_bot_token: str | None = Field(default=os.getenv("TELEGRAM_BOT_TOKEN"))
    telegram_target_chat: str = Field(default=os.getenv("TELEGRAM_TARGET_CHAT", "me"))

    google_client_id: str | None = Field(default=os.getenv("GOOGLE_CLIENT_ID"))
    google_client_secret: str | None = Field(default=os.getenv("GOOGLE_CLIENT_SECRET"))
    google_refresh_token: str | None = Field(default=os.getenv("GOOGLE_REFRESH_TOKEN"))

    max_file_size_mb: int = Field(default=int(os.getenv("MAX_FILE_SIZE_MB", "700")), ge=1)
    upload_destination: str = Field(default=os.getenv("UPLOAD_DESTINATION", "telegram"))
    tmp_dir: Path = Field(default=Path(os.getenv("TMP_DIR", "/tmp")))

    request_timeout_seconds: float = Field(default=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")), gt=1)
    download_retry_count: int = Field(default=int(os.getenv("DOWNLOAD_RETRY_COUNT", "4")), ge=0)
    allow_private_ips: bool = Field(default=os.getenv("ALLOW_PRIVATE_IPS", "true").lower() in ("true", "1", "yes"))
    allowed_url_schemes: tuple[str, ...] = ("http", "https")

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def is_google_enabled(self) -> bool:
        return all([
            self.google_client_id,
            self.google_client_secret,
            self.google_refresh_token,
        ])

    @property
    def is_telegram_enabled(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash and self.telegram_bot_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        settings = Settings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc

    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    return settings
