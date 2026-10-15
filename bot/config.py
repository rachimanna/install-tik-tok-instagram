"""Настройки из переменных окружения."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

MB = 1024 * 1024


class ConfigError(RuntimeError):
    pass


def _int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом") from exc
    if value < minimum:
        raise ConfigError(f"{name} должно быть не меньше {minimum}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "да"}


def _opt(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    bot_token: str
    telegram_api_url: str | None = None
    max_upload_bytes: int = 50 * MB
    max_concurrent_downloads: int = 3
    max_tasks_per_user: int = 1
    download_timeout: int = 180
    send_timeout: int = 300
    max_items_per_post: int = 20
    file_ttl_seconds: int = 3600
    cache_max_bytes: int = 2000 * MB
    work_dir: Path = Path("./data")
    tiktok_allow_watermark_fallback: bool = True
    ytdlp_cookies_file: Path | None = None
    ytdlp_proxy: str | None = None
    log_level: str = "INFO"
    send_audio: bool = True
    mode: str = "polling"  # polling | webhook
    webhook_base_url: str | None = None
    webhook_secret: str | None = None
    port: int = 8080

    @property
    def webhook_path(self) -> str:
        return "/telegram/webhook"

    @property
    def webhook_secret_token(self) -> str:
        """Telegram принимает в secret_token только A-Z, a-z, 0-9, _ и - (до 256 символов)."""
        seed = self.webhook_secret or self.bot_token
        return hashlib.sha256(("webhook:" + seed).encode()).hexdigest()

    @property
    def jobs_dir(self) -> Path:
        return self.work_dir / "jobs"

    @property
    def cache_dir(self) -> Path:
        return self.work_dir / "cache"

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise ConfigError("Не задан BOT_TOKEN (см. .env.example)")

        api_url = _opt("TELEGRAM_API_URL")
        # Облачный Bot API принимает файлы до 50 МБ; локальный сервер — до 2000 МБ.
        upload_cap = 2000 if api_url else 50
        upload_mb = min(_int("MAX_UPLOAD_MB", 50), upload_cap)

        cookies = _opt("YTDLP_COOKIES_FILE")
        cookies_path = Path(cookies).expanduser() if cookies else None
        if cookies_path and not cookies_path.is_file():
            raise ConfigError("YTDLP_COOKIES_FILE указывает на несуществующий файл")

        mode = os.getenv("BOT_MODE", "polling").strip().lower() or "polling"
        if mode not in ("polling", "webhook"):
            raise ConfigError("BOT_MODE должно быть polling или webhook")
        # На Render адрес сервиса задаётся автоматически в RENDER_EXTERNAL_URL.
        base_url = _opt("WEBHOOK_BASE_URL") or _opt("RENDER_EXTERNAL_URL")
        if mode == "webhook":
            if not base_url:
                raise ConfigError("Для BOT_MODE=webhook задайте WEBHOOK_BASE_URL")
            if not base_url.startswith("https://"):
                raise ConfigError("WEBHOOK_BASE_URL должен начинаться с https://")

        return cls(
            mode=mode,
            webhook_base_url=base_url.rstrip("/") if base_url else None,
            webhook_secret=_opt("WEBHOOK_SECRET"),
            port=_int("PORT", 8080),
            bot_token=token,
            telegram_api_url=api_url,
            max_upload_bytes=upload_mb * MB,
            max_concurrent_downloads=_int("MAX_CONCURRENT_DOWNLOADS", 3),
            max_tasks_per_user=_int("MAX_TASKS_PER_USER", 1),
            download_timeout=_int("DOWNLOAD_TIMEOUT_SECONDS", 180, minimum=10),
            send_timeout=_int("SEND_TIMEOUT_SECONDS", 300, minimum=10),
            max_items_per_post=_int("MAX_ITEMS_PER_POST", 20),
            file_ttl_seconds=_int("FILE_TTL_MINUTES", 60) * 60,
            cache_max_bytes=_int("CACHE_MAX_MB", 2000) * MB,
            work_dir=Path(os.getenv("WORK_DIR", "./data")).expanduser().resolve(),
            tiktok_allow_watermark_fallback=_bool("TIKTOK_ALLOW_WATERMARK_FALLBACK", True),
            ytdlp_cookies_file=cookies_path,
            ytdlp_proxy=_opt("YTDLP_PROXY"),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            send_audio=_bool("SEND_AUDIO", True),
        )
