"""Удаление секретов из логов: токен бота, cookies, подписанные ссылки."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

_TOKEN_RE = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_COOKIE_RE = re.compile(r"(?i)\b(cookie|set-cookie|sessionid|csrftoken|sid_tt|sessionid_ss|authorization)\b\s*[:=]\s*\S+")
# CDN-хосты, у которых подпись может быть и в пути — путь скрываем целиком.
_SIGNED_HOST_HINTS = ("tiktokcdn", "tiktokv", "byteoversea", "ibytedtos", "muscdn",
                      "cdninstagram", "fbcdn", "akamaized", "bytecdn", "ttwstatic")


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url>"
    host = (parts.hostname or "").lower()
    if any(h in host for h in _SIGNED_HOST_HINTS):
        return f"{parts.scheme}://{host}/…"
    suffix = "?…" if parts.query else ""
    return f"{parts.scheme}://{host}{parts.path}{suffix}"


def redact(text: str) -> str:
    text = _TOKEN_RE.sub("<bot-token>", text)
    text = _COOKIE_RE.sub(lambda m: f"{m.group(1)}=<hidden>", text)
    return _URL_RE.sub(lambda m: redact_url(m.group(0)), text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — не роняем логирование из-за кривого формата
            return True
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # aiogram при DEBUG пишет тела запросов — держим его на INFO.
    logging.getLogger("aiogram").setLevel(max(logging.INFO, root.level))
