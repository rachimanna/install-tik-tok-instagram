"""Хранилище для кнопки «📁 Скачать оригинальным файлом».

После скачивания файлы переносятся в кэш с ограниченным сроком хранения. Под одной
кнопкой может быть один файл или весь альбом. При первом нажатии файлы уходят
документами, и Telegram file_id каждого сохраняется — повторные нажатия используют
file_id, а локальный файл удаляется сразу. Когда срок истёк, кнопка отвечает, что
ссылку нужно прислать заново.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class StoredPart:
    path: Path | None
    filename: str
    size: int
    document_file_id: str | None = None

    @property
    def available(self) -> bool:
        return self.document_file_id is not None or self.path is not None


@dataclass
class StoredFile:
    token: str
    expires_at: float
    parts: list[StoredPart] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.parts) and all(p.available for p in self.parts)


class FileStore:
    def __init__(self, cache_dir: Path, ttl_seconds: int, max_bytes: int, clock=time.monotonic) -> None:
        self.cache_dir = cache_dir
        self.ttl = ttl_seconds
        self.max_bytes = max_bytes
        self._clock = clock
        self._items: dict[str, StoredFile] = {}

    def reset_dir(self) -> None:
        """При запуске: удалить файлы, оставшиеся от прошлого процесса."""
        shutil.rmtree(self.cache_dir, ignore_errors=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def put(self, files: list[tuple[Path, str]]) -> StoredFile:
        """files: [(путь, имя файла для пользователя)]. Файлы переносятся в кэш."""
        token = secrets.token_urlsafe(9)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        item = StoredFile(token, self._clock() + self.ttl)
        for index, (source, filename) in enumerate(files):
            target = self.cache_dir / f"{token}_{index:02d}{source.suffix}"
            os.replace(source, target)  # тот же диск: мгновенно и без копирования
            item.parts.append(StoredPart(target, filename, target.stat().st_size))
        self._items[token] = item
        self._enforce_size(keep=token)
        return item

    def get(self, token: str) -> StoredFile | None:
        item = self._items.get(token)
        if item is None:
            return None
        if self._clock() >= item.expires_at:
            self._drop(token)
            return None
        return item

    def remember_documents(self, token: str, file_ids: list[str | None]) -> None:
        item = self._items.get(token)
        if item is None:
            return
        for part, file_id in zip(item.parts, file_ids):
            if file_id:
                part.document_file_id = file_id
                self._remove_file(part)

    def discard(self, token: str) -> None:
        self._drop(token)

    def sweep(self) -> int:
        now = self._clock()
        expired = [t for t, i in self._items.items() if now >= i.expires_at]
        for token in expired:
            self._drop(token)
        return len(expired)

    def total_bytes(self) -> int:
        return sum(p.size for i in self._items.values() for p in i.parts if p.path is not None)

    def _enforce_size(self, keep: str) -> None:
        # Сначала удаляем самые старые записи; уже отправленные документом остаются по file_id.
        for token, item in sorted(self._items.items(), key=lambda kv: kv[1].expires_at):
            if self.total_bytes() <= self.max_bytes:
                break
            if token == keep:
                continue
            if all(p.document_file_id for p in item.parts):
                for part in item.parts:
                    self._remove_file(part)
            else:
                self._drop(token)

    def _drop(self, token: str) -> None:
        item = self._items.pop(token, None)
        if item is not None:
            for part in item.parts:
                self._remove_file(part)

    @staticmethod
    def _remove_file(part: StoredPart) -> None:
        if part.path is not None:
            try:
                part.path.unlink(missing_ok=True)
            except OSError:
                log.warning("Не удалось удалить файл из кэша")
            part.path = None

    def __len__(self) -> int:
        return len(self._items)
