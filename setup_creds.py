# Project: TelePort | Author: Shyam | Version: 1.0.0
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def env_or_prompt(key: str, label: str) -> str:
    value = os.getenv(key, "").strip()
    if value:
        return value
    return input(f"{label}: ").strip()


def generate_google_refresh_token(client_id: str, client_secret: str) -> str:
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    try:
        credentials = flow.run_local_server(
            host="localhost",
            port=8080,
            authorization_prompt_message="Open this URL and authorize access:\n{url}",
            success_message="Authorization complete. Return to terminal.",
            open_browser=True,
            access_type="offline",
            prompt="consent",
        )
    except OSError:
        credentials = flow.run_console(
            authorization_prompt_message="Open this URL and authorize access:\n{url}",
        )

    if not credentials.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Revoke app access and re-run with consent."
        )
    return credentials.refresh_token


def main() -> None:
    load_dotenv()

    print("== Google OAuth Refresh Token Setup ==")
    google_client_id = env_or_prompt("GOOGLE_CLIENT_ID", "Google Client ID")
    google_client_secret = env_or_prompt("GOOGLE_CLIENT_SECRET", "Google Client Secret")
    google_refresh_token = generate_google_refresh_token(google_client_id, google_client_secret)

    print("\n== Telegram Bot MTProto Config ==")
    telegram_api_id = int(env_or_prompt("TELEGRAM_API_ID", "Telegram API ID"))
    telegram_api_hash = env_or_prompt("TELEGRAM_API_HASH", "Telegram API Hash")
    telegram_bot_token = env_or_prompt("TELEGRAM_BOT_TOKEN", "Telegram Bot Token")

    payload = {
        "GOOGLE_CLIENT_ID": google_client_id,
        "GOOGLE_CLIENT_SECRET": google_client_secret,
        "GOOGLE_REFRESH_TOKEN": google_refresh_token,
        "TELEGRAM_API_ID": str(telegram_api_id),
        "TELEGRAM_API_HASH": telegram_api_hash,
        "TELEGRAM_BOT_TOKEN": telegram_bot_token,
        "TELEGRAM_TARGET_CHAT": os.getenv("TELEGRAM_TARGET_CHAT", "me"),
    }

    out_file = Path("generated_credentials.json")
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nCredentials generated.")
    print("Saved JSON:", out_file.resolve())
    print("\nSet these as Render/Koyeb environment variables:")
    for key, value in payload.items():
        masked = value if key.endswith("ID") or key == "TELEGRAM_TARGET_CHAT" else "<hidden>"
        print(f"- {key}={masked}")


if __name__ == "__main__":
    main()
