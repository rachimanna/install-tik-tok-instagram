"""Сценарий обработки ссылки: проверка → скачивание → отправка.

Не зависит от aiogram напрямую (статус и отправка передаются как объекты),
поэтому его можно проверить тестами без Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import threading
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import media, texts
from .config import Settings
from .file_store import FileStore
from .limits import UserLimiter
from .links import LinkError, LinkKind, ParsedLink, extract_url, parse_link
from .net_guard import Fetcher, HostChecker, ResolveError, UnsafeAddressError, ensure_public_host, resolve_link
from .providers import (AudioTrack, DownloadedMedia, DownloadResult, ErrorCode, MediaKind, Provider,
                        ProviderError, pick_provider)
from .redact import redact_url

log = logging.getLogger(__name__)

NETWORK_RETRIES = 1  # дополнительная попытка при сетевой ошибке (у yt-dlp есть свои повторы)
CANCEL_GRACE_SECONDS = 30


class Status(Protocol):
    async def update(self, text: str) -> None: ...
    async def finish(self, text: str | None, source_url: str | None = None) -> None:
        """text=None — удалить сообщение статуса; иначе показать итоговый текст."""


class Sender(Protocol):
    async def send_single(self, item: DownloadedMedia, caption: str, token: str | None,
                          source_url: str) -> None:
        """Одно видео или фото с кнопками."""

    async def send_album(self, items: list[DownloadedMedia]) -> None:
        """Несколько элементов альбомами по порядку (кнопки к альбому прикрепить нельзя)."""

    async def send_summary(self, text: str, token: str | None, source_url: str) -> None:
        """Сообщение с описанием альбома и кнопками."""

    async def send_audios(self, platform, items: list[DownloadedMedia]) -> None:
        """Звук из видео аудиофайлами (у каждого элемента заполнено поле audio)."""


class SendTooLarge(Exception):
    pass


class SendFailed(Exception):
    pass


@dataclass
class Pipeline:
    settings: Settings
    providers: list[Provider]
    store: FileStore
    limiter: UserLimiter
    fetch: Fetcher
    check_host: HostChecker = ensure_public_host

    def __post_init__(self) -> None:
        self.semaphore = asyncio.Semaphore(self.settings.max_concurrent_downloads)
        self.executor = ThreadPoolExecutor(max_workers=self.settings.max_concurrent_downloads + 2,
                                           thread_name_prefix="download")

    def prepare_dirs(self) -> None:
        shutil.rmtree(self.settings.jobs_dir, ignore_errors=True)
        self.settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.store.reset_dir()

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    @asynccontextmanager
    async def job_dir(self) -> AsyncIterator[Path]:
        """Отдельная временная папка на каждую загрузку; удаляется всегда."""
        self.settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix="job_", dir=self.settings.jobs_dir))
        try:
            yield path
        finally:
            await asyncio.to_thread(shutil.rmtree, path, True)

    async def handle(self, user_id: int, text: str | None, open_status, sender: Sender) -> str | None:
        """Возвращает текст для ответа без статуса (ошибка ссылки) или None."""
        raw = extract_url(text)
        if raw is None:
            return texts.NO_LINK
        try:
            link = parse_link(raw)
        except LinkError as exc:
            log.info("Rejected link: %s", exc.code.value)
            return texts.LINK_ERRORS[exc.code]

        if link.kind is LinkKind.INSTAGRAM_STORY and not self.settings.ytdlp_cookies_file:
            return texts.INSTAGRAM_STORIES_DISABLED
        if not self.limiter.try_acquire(user_id):
            return texts.BUSY
        try:
            status: Status = await open_status(texts.CHECKING)
            await self._process(link, status, sender)
        finally:
            self.limiter.release(user_id)
        return None

    async def _process(self, link: ParsedLink, status: Status, sender: Sender) -> None:
        source = link.original
        try:
            link = await resolve_link(link, self.fetch, self.check_host)
            provider = pick_provider(self.providers, link)
            log.info("Download %s via %s", redact_url(link.url), provider.name)
            async with self.job_dir() as workdir:
                if self.semaphore.locked():
                    await status.update(texts.QUEUED)
                async with self.semaphore:
                    await status.update(texts.DOWNLOADING)
                    result = await self._download_with_retry(provider, link, workdir)
                await status.update(texts.SENDING)
                note = await self._send_all(result, sender, source)
            await status.finish(note, source if note else None)
        except LinkError as exc:
            await status.finish(texts.LINK_ERRORS[exc.code])
        except UnsafeAddressError:
            log.warning("Blocked non-public address for %s", redact_url(link.url))
            await status.finish(texts.UNSAFE_ADDRESS)
        except ResolveError as exc:
            await status.finish(texts.RESOLVE_ERRORS[exc.reason], source)
        except ProviderError as exc:
            log.info("Provider error %s for %s", exc.code.value, redact_url(link.url))
            await status.finish(texts.provider_error(
                exc.code, link.platform, self.settings.max_upload_bytes, exc.size,
                story=link.kind is LinkKind.INSTAGRAM_STORY), source)
        except SendTooLarge:
            await status.finish(texts.provider_error(ErrorCode.TOO_LARGE, link.platform,
                                                     self.settings.max_upload_bytes), source)
        except SendFailed:
            await status.finish(texts.SEND_FAILED, source)
        except Exception:  # noqa: BLE001 — пользователю показываем простой текст
            log.exception("Unexpected error for %s", redact_url(link.url))
            with suppress(Exception):
                await status.finish(texts.UNEXPECTED, source)

    async def _download_with_retry(self, provider: Provider, link: ParsedLink, workdir: Path) -> DownloadResult:
        for attempt in range(NETWORK_RETRIES + 1):
            try:
                return await self._run_blocking(provider, link, workdir)
            except ProviderError as exc:
                if exc.code is not ErrorCode.NETWORK or attempt == NETWORK_RETRIES:
                    raise
                log.info("Network error, retrying (%d)", attempt + 1)
                await asyncio.sleep(2)
        raise AssertionError("unreachable")

    async def _run_blocking(self, provider: Provider, link: ParsedLink, workdir: Path) -> DownloadResult:
        cancel = threading.Event()
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.executor, provider.download, link, workdir, cancel)
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.settings.download_timeout)
        except TimeoutError:
            cancel.set()
            # Даём потоку остановиться, чтобы не удалять папку из-под работающей загрузки.
            with suppress(BaseException):
                await asyncio.wait_for(future, CANCEL_GRACE_SECONDS)
            raise ProviderError(ErrorCode.TIMEOUT) from None
        except asyncio.CancelledError:
            cancel.set()
            raise

    async def _send_all(self, result: DownloadResult, sender: Sender, source: str) -> str | None:
        """Отправляет всё скачанное. Возвращает текст для статуса (заметки) или None."""
        items = result.items
        if not items:
            raise SendFailed()
        notes = texts.summary_notes(result.skipped_unsupported, result.skipped_too_large,
                                    result.truncated, self.settings.max_upload_bytes,
                                    self.settings.max_items_per_post)
        if self.settings.send_audio:
            await self._extract_audio(result)
        stored = self.store.put([(item.path, _filename(result, item, i))
                                 for i, item in enumerate(items, start=1)])
        for item, part in zip(items, stored.parts):
            item.path = part.path  # файлы теперь живут в кэше
        try:
            if len(items) == 1:
                caption = texts.single_caption(result.platform, items[0])
                await sender.send_single(items[0], caption, stored.token, source)
                status_text = notes
            else:
                await sender.send_album(items)
                summary = texts.album_caption(result.platform, items)
                if notes:
                    summary += "\n\n" + notes
                await sender.send_summary(summary, stored.token, source)
                status_text = None
        except BaseException:
            self.store.discard(stored.token)
            raise
        await self._send_audio(result, sender)
        return status_text

    async def _extract_audio(self, result: DownloadResult) -> None:
        limit = self.settings.max_upload_bytes
        for index, item in enumerate(result.items, start=1):
            if item.kind is not MediaKind.VIDEO:
                continue
            stem = item.path.with_name(f"audio_{index:02d}")
            path = await asyncio.to_thread(media.extract_audio, item.path, stem)
            if path is None:
                continue
            size = path.stat().st_size
            if size > limit:
                path.unlink(missing_ok=True)
                continue
            name = _filename(result, item, index)
            item.audio = AudioTrack(path, size, item.duration, Path(name).stem + path.suffix)

    async def _send_audio(self, result: DownloadResult, sender: Sender) -> None:
        """Звук — дополнение: если он не отправился, видео у пользователя уже есть."""
        with_audio = [i for i in result.items if i.audio is not None]
        if not with_audio:
            return
        try:
            await sender.send_audios(result.platform, with_audio)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("Audio sending failed", exc_info=True)


def _filename(result: DownloadResult, item: DownloadedMedia, index: int) -> str:
    suffix = item.path.suffix or ".mp4"
    base = f"{result.platform.value}_{item.path.stem.split('.')[0]}"
    if len(result.items) > 1:
        base += f"_{index}"
    return base[:80] + suffix
