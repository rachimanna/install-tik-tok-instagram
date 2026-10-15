"""Связка сценария с Telegram: сообщение-статус и отправка файлов с повторами."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TypeVar

from aiogram import Bot
from aiogram.exceptions import (TelegramBadRequest, TelegramEntityTooLarge, TelegramNetworkError,
                                TelegramRetryAfter)
from aiogram.types import (FSInputFile, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message,
                           ReplyParameters)

from . import keyboards
from .file_store import FileStore
from .pipeline import SendFailed, SendTooLarge
from .providers import DownloadedMedia, MediaKind

log = logging.getLogger(__name__)

T = TypeVar("T")
SEND_ATTEMPTS = 3
MAX_RETRY_AFTER = 60
ALBUM_SIZE = 10  # Telegram: в одной медиагруппе 2–10 элементов
# Ограничения sendPhoto: до 10 МБ, сумма сторон до 10000, соотношение сторон до 20.
PHOTO_MAX_BYTES = 10 * 1024 * 1024
PHOTO_CODECS = {"mjpeg", "jpg", "jpeg", "png"}


def photo_sendable(item: DownloadedMedia) -> bool:
    """Можно ли отправить фото как фото (иначе — документом, без перекодирования)."""
    if item.kind is not MediaKind.PHOTO or item.size > PHOTO_MAX_BYTES:
        return False
    if (item.image_codec or "").lower() not in PHOTO_CODECS:
        return False
    if item.width and item.height:
        if item.width + item.height > 10000:
            return False
        if max(item.width, item.height) / max(1, min(item.width, item.height)) > 20:
            return False
    return True


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


async def with_retries(call: Callable[[], Awaitable[T]], attempts: int = SEND_ATTEMPTS) -> T:
    """Ограниченные повторы: RetryAfter и сетевые сбои. Слишком большой файл — сразу ошибка."""
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except TelegramEntityTooLarge as exc:
            raise SendTooLarge() from exc
        except TelegramRetryAfter as exc:
            if attempt == attempts or exc.retry_after > MAX_RETRY_AFTER:
                raise SendFailed() from exc
            await asyncio.sleep(exc.retry_after)
        except TelegramNetworkError as exc:
            if attempt == attempts:
                raise SendFailed() from exc
            log.info("Telegram network error, retry %d: %s", attempt, type(exc).__name__)
            await asyncio.sleep(2 * attempt)
        except TelegramBadRequest as exc:
            if "too big" in str(exc).lower() or "too large" in str(exc).lower():
                raise SendTooLarge() from exc
            log.warning("Telegram rejected file: %s", exc.message)
            raise SendFailed() from exc
    raise SendFailed()


class TelegramStatus:
    def __init__(self, message: Message) -> None:
        self.message = message
        self._last = message.text

    @classmethod
    async def open(cls, origin: Message, text: str) -> "TelegramStatus":
        sent = await origin.answer(text)
        return cls(sent)

    async def update(self, text: str) -> None:
        if text == self._last:
            return
        self._last = text
        with suppress(TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
            await self.message.edit_text(text)

    async def finish(self, text: str | None, source_url: str | None = None) -> None:
        if text is None:
            with suppress(TelegramBadRequest, TelegramNetworkError):
                await self.message.delete()
            return
        markup = keyboards.source_keyboard(source_url) if source_url else None
        with suppress(TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter):
            await self.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


class TelegramSender:
    def __init__(self, bot: Bot, chat_id: int, reply_to: int | None, timeout: int) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.reply_to = reply_to
        self.timeout = timeout

    def _reply(self) -> ReplyParameters | None:
        if not self.reply_to:
            return None
        return ReplyParameters(message_id=self.reply_to, allow_sending_without_reply=True)

    async def send_single(self, item: DownloadedMedia, caption: str, token: str | None,
                          source_url: str) -> None:
        await self._send_one(item, caption, keyboards.video_keyboard(token, source_url))

    async def send_album(self, items: list[DownloadedMedia]) -> None:
        in_album = [i for i in items if i.kind is MediaKind.VIDEO or photo_sendable(i)]
        as_documents = [i for i in items if i not in in_album]
        for chunk in _chunks(in_album, ALBUM_SIZE):
            if len(chunk) == 1:
                await self._send_one(chunk[0], None, None)
                continue

            async def call(chunk=chunk) -> list[Message]:
                return await self.bot.send_media_group(
                    chat_id=self.chat_id, media=[_input_media(i) for i in chunk],
                    reply_parameters=self._reply(), request_timeout=self.timeout)

            await with_retries(call)
        for item in as_documents:
            await self._send_one(item, None, None)

    async def send_summary(self, text: str, token: str | None, source_url: str) -> None:
        markup = keyboards.video_keyboard(token, source_url, many=True)

        async def call() -> Message:
            return await self.bot.send_message(chat_id=self.chat_id, text=text, reply_markup=markup,
                                               disable_web_page_preview=True)

        await with_retries(call)

    async def _send_one(self, item: DownloadedMedia, caption: str | None, markup) -> None:
        common = dict(chat_id=self.chat_id, caption=caption, reply_markup=markup,
                      reply_parameters=self._reply(), request_timeout=self.timeout)

        async def call() -> Message:
            if item.kind is MediaKind.VIDEO:
                return await self.bot.send_video(
                    video=FSInputFile(item.path, filename=item.path.name),
                    width=item.width, height=item.height,
                    duration=int(round(item.duration)) if item.duration else None,
                    thumbnail=FSInputFile(item.thumbnail) if item.thumbnail else None,
                    supports_streaming=True, **common)
            if photo_sendable(item):
                return await self.bot.send_photo(photo=FSInputFile(item.path, filename=item.path.name),
                                                 **common)
            # Фото, которое Telegram не примет как фото (слишком большое, WebP/HEIC…), —
            # отправляем файлом как есть.
            return await self.bot.send_document(
                document=FSInputFile(item.path, filename=item.path.name),
                disable_content_type_detection=True, **common)

        await with_retries(call)


def _input_media(item: DownloadedMedia) -> InputMediaPhoto | InputMediaVideo:
    file = FSInputFile(item.path, filename=item.path.name)
    if item.kind is MediaKind.VIDEO:
        return InputMediaVideo(
            media=file, width=item.width, height=item.height,
            duration=int(round(item.duration)) if item.duration else None,
            thumbnail=FSInputFile(item.thumbnail) if item.thumbnail else None,
            supports_streaming=True)
    return InputMediaPhoto(media=file)


async def send_original(bot: Bot, store: FileStore, token: str, chat_id: int, timeout: int) -> str:
    """Отправить файлы документами как есть. Возвращает 'ok' | 'expired' | 'too_large' | 'failed'."""
    item = store.get(token)
    if item is None or not item.available:
        return "expired"

    def document(part):
        return part.document_file_id or FSInputFile(part.path, filename=part.filename)

    file_ids: list[str | None] = []
    try:
        for chunk in _chunks(item.parts, ALBUM_SIZE):
            if len(chunk) == 1:
                async def call_one(part=chunk[0]) -> Message:
                    return await bot.send_document(chat_id=chat_id, document=document(part),
                                                   disable_content_type_detection=True,
                                                   request_timeout=timeout)
                messages = [await with_retries(call_one)]
            else:
                async def call_group(chunk=chunk) -> list[Message]:
                    return await bot.send_media_group(
                        chat_id=chat_id,
                        media=[InputMediaDocument(media=document(p), disable_content_type_detection=True)
                               for p in chunk],
                        request_timeout=timeout)
                messages = await with_retries(call_group)
            file_ids.extend(m.document.file_id if m.document else None for m in messages)
    except SendTooLarge:
        return "too_large"
    except SendFailed:
        return "failed"
    finally:
        if file_ids:
            store.remember_documents(token, file_ids)
    return "ok"
