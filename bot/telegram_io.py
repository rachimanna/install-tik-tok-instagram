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
from aiogram.types import FSInputFile, Message, ReplyParameters

from . import keyboards
from .file_store import FileStore
from .pipeline import SendFailed, SendTooLarge
from .providers import DownloadedVideo

log = logging.getLogger(__name__)

T = TypeVar("T")
SEND_ATTEMPTS = 3
MAX_RETRY_AFTER = 60


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

    async def send_video(self, video: DownloadedVideo, caption: str, token: str | None,
                         source_url: str) -> None:
        markup = keyboards.video_keyboard(token, source_url)

        async def call() -> Message:
            return await self.bot.send_video(
                chat_id=self.chat_id,
                video=FSInputFile(video.path, filename=video.path.name),
                caption=caption,
                width=video.width,
                height=video.height,
                duration=int(round(video.duration)) if video.duration else None,
                thumbnail=FSInputFile(video.thumbnail) if video.thumbnail else None,
                supports_streaming=True,
                reply_markup=markup,
                reply_parameters=(ReplyParameters(message_id=self.reply_to,
                                                  allow_sending_without_reply=True)
                                  if self.reply_to else None),
                request_timeout=self.timeout,
            )

        await with_retries(call)


async def send_original(bot: Bot, store: FileStore, token: str, chat_id: int, timeout: int) -> str:
    """Отправить файл документом. Возвращает 'ok' | 'expired' | 'too_large' | 'failed'."""
    item = store.get(token)
    if item is None or (item.document_file_id is None and item.path is None):
        return "expired"

    async def call() -> Message:
        document = item.document_file_id or FSInputFile(item.path, filename=item.filename)
        return await bot.send_document(
            chat_id=chat_id,
            document=document,
            disable_content_type_detection=True,  # без обработки: файл как есть
            request_timeout=timeout,
        )

    try:
        message = await with_retries(call)
    except SendTooLarge:
        return "too_large"
    except SendFailed:
        return "failed"
    if message.document and not item.document_file_id:
        store.remember_document(token, message.document.file_id)
    return "ok"
