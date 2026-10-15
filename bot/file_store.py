"""Хранилище для кнопки «📁 Скачать оригинальным файлом».

После скачивания файл переносится в кэш с ограниченным сроком хранения. При первом
нажатии файл отправляется документом, и Telegram file_id сохраняется — повторные
нажатия используют file_id, а локальный файл удаляется сразу. Когда срок истёк,
кнопка отвечает, что ссылку нужно прислать заново.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class StoredFile:
    token: str
    path: Path | None
    filename: str
    size: int
    expires_at: float
    document_file_id: str | None = None


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

    def put(self, source: Path, filename: str) -> StoredFile:
        token = secrets.token_urlsafe(9)
        target = self.cache_dir / f"{token}{source.suffix}"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)  # тот же диск: мгновенно и без копирования
        item = StoredFile(token, target, filename, target.stat().st_size, self._clock() + self.ttl)
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

    def remember_document(self, token: str, file_id: str) -> None:
        item = self._items.get(token)
        if item is None:
            return
        item.document_file_id = file_id
        self._remove_file(item)

    def discard(self, token: str) -> None:
        self._drop(token)

    def sweep(self) -> int:
        now = self._clock()
        expired = [t for t, i in self._items.items() if now >= i.expires_at]
        for token in expired:
            self._drop(token)
        return len(expired)

    def total_bytes(self) -> int:
        return sum(i.size for i in self._items.values() if i.path is not None)

    def _enforce_size(self, keep: str) -> None:
        # Сначала удаляем самые старые файлы (file_id остаётся рабочим).
        for token, item in sorted(self._items.items(), key=lambda kv: kv[1].expires_at):
            if self.total_bytes() <= self.max_bytes:
                break
            if token != keep and item.path is not None:
                if item.document_file_id:
                    self._remove_file(item)
                else:
                    self._drop(token)

    def _drop(self, token: str) -> None:
        item = self._items.pop(token, None)
        if item is not None:
            self._remove_file(item)

    @staticmethod
    def _remove_file(item: StoredFile) -> None:
        if item.path is not None:
            try:
                item.path.unlink(missing_ok=True)
            except OSError:
                log.warning("Не удалось удалить файл из кэша")
            item.path = None

    def __len__(self) -> int:
        return len(self._items)
