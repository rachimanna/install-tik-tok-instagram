"""Загрузчики платформ. Порядок в списке — приоритет: первый подходящий провайдер скачивает."""

from __future__ import annotations

from ..config import Settings
from ..links import ParsedLink
from .base import (AudioTrack, DownloadedMedia, DownloadResult, ErrorCode, MediaKind, Provider,
                   ProviderError, Watermark)
from .ytdlp_provider import YtDlpProvider

__all__ = ["AudioTrack", "DownloadedMedia", "DownloadResult", "ErrorCode", "MediaKind", "Provider",
           "ProviderError", "Watermark", "YtDlpProvider", "build_providers", "pick_provider"]


def build_providers(settings: Settings, cookie_source=None) -> list[Provider]:
    """cookie_source — функция, возвращающая путь к cookies.txt (например, после входа
    в служебный аккаунт Instagram), или None — тогда берутся cookies из настроек."""
    # Чтобы сменить или добавить провайдера, создайте класс с интерфейсом Provider
    # (см. base.py) и поставьте его в этот список.
    return [
        YtDlpProvider(
            max_bytes=settings.max_upload_bytes,
            max_items=settings.max_items_per_post,
            allow_watermark_fallback=settings.tiktok_allow_watermark_fallback,
            cookies_file=cookie_source or settings.ytdlp_cookies_file,
            proxy=settings.ytdlp_proxy,
        ),
    ]


def pick_provider(providers: list[Provider], link: ParsedLink) -> Provider:
    for provider in providers:
        if provider.supports(link):
            return provider
    raise ProviderError(ErrorCode.UNSUPPORTED, "no provider")
