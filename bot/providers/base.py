"""Общие типы для загрузчиков. Чтобы добавить провайдера, реализуйте Provider."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from ..links import ParsedLink, Platform


class MediaKind(str, Enum):
    VIDEO = "video"
    PHOTO = "photo"


class Watermark(str, Enum):
    CLEAN = "clean"  # загрузчик выбрал поток, который не помечен как водяной знак
    WATERMARKED = "watermarked"  # явно помечен как версия с водяным знаком
    UNKNOWN = "unknown"  # отсутствие водяного знака подтвердить нельзя
    NOT_APPLICABLE = "n/a"  # Instagram и фото: об водяном знаке ничего не утверждаем


class ErrorCode(str, Enum):
    PRIVATE = "private"
    LOGIN_REQUIRED = "login_required"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"
    NO_MEDIA = "no_media"
    NO_CLEAN_VERSION = "no_clean_version"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"
    NETWORK = "network"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    def __init__(self, code: ErrorCode, detail: str = "", *, size: int | None = None) -> None:
        super().__init__(f"{code.value}: {detail}" if detail else code.value)
        self.code = code
        self.size = size


@dataclass
class DownloadedMedia:
    path: Path
    size: int
    kind: MediaKind = MediaKind.VIDEO
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    thumbnail: Path | None = None
    watermark: Watermark = Watermark.NOT_APPLICABLE
    title: str | None = None
    image_codec: str | None = None  # для фото: mjpeg / png / webp …


@dataclass
class DownloadResult:
    platform: Platform
    items: list[DownloadedMedia] = field(default_factory=list)  # в порядке публикации
    skipped_unsupported: int = 0  # элементы без видео и фото (например, только звук)
    skipped_too_large: list[int | None] = field(default_factory=list)  # размеры, если известны
    truncated: bool = False  # в публикации больше элементов, чем MAX_ITEMS_PER_POST


class Provider(Protocol):
    name: str

    def supports(self, link: ParsedLink) -> bool: ...

    def download(self, link: ParsedLink, workdir: Path, cancel: threading.Event) -> DownloadResult:
        """Блокирующая загрузка. Вызывается в отдельном потоке.

        Должна периодически проверять cancel и прекращать работу, когда он выставлен.
        Все файлы — только внутри workdir.
        """
        ...
