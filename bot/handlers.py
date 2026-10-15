"""Обработчики Telegram. Вся логика скачивания — в pipeline.py и providers/."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from . import keyboards, texts
from .config import Settings
from .file_store import FileStore
from .pipeline import Pipeline
from .telegram_io import TelegramSender, TelegramStatus, send_original

log = logging.getLogger(__name__)


def build_router(pipeline: Pipeline, store: FileStore, settings: Settings) -> Router:
    router = Router(name="main")
    help_text = texts.help_text(settings.max_upload_bytes, settings.file_ttl_seconds)
    # Повторное нажатие на ту же кнопку во время отправки не запускает вторую отправку.
    in_flight: set[str] = set()

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(texts.START, reply_markup=keyboards.start_keyboard())

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(help_text)

    @router.callback_query(F.data == keyboards.HELP_DATA)
    async def help_cb(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.message:
            await callback.message.answer(help_text)

    @router.message(F.text | F.caption)
    async def on_link(message: Message, bot: Bot) -> None:
        if message.from_user is None:
            return
        text = message.text or message.caption

        async def open_status(initial: str) -> TelegramStatus:
            return await TelegramStatus.open(message, initial)

        sender = TelegramSender(bot, message.chat.id, message.message_id, settings.send_timeout)
        reply = await pipeline.handle(message.from_user.id, text, open_status, sender)
        if reply:
            await message.answer(reply, disable_web_page_preview=True)

    @router.callback_query(F.data.startswith(keyboards.DOC_PREFIX))
    async def on_document(callback: CallbackQuery, bot: Bot) -> None:
        token = (callback.data or "")[len(keyboards.DOC_PREFIX):]
        chat = callback.message.chat.id if callback.message else callback.from_user.id
        if token in in_flight:
            await callback.answer(texts.DOC_SENDING)
            return
        in_flight.add(token)
        try:
            await callback.answer(texts.DOC_SENDING)
            outcome = await send_original(bot, store, token, chat, settings.send_timeout)
        finally:
            in_flight.discard(token)
        if outcome == "expired":
            await bot.send_message(chat, texts.DOC_EXPIRED)
        elif outcome == "too_large":
            await bot.send_message(chat, texts.DOC_TOO_LARGE)
        elif outcome == "failed":
            await bot.send_message(chat, texts.DOC_FAILED)

    return router


async def sweep_forever(store: FileStore, interval: int = 60) -> None:
    while True:
        await asyncio.sleep(interval)
        removed = store.sweep()
        if removed:
            log.info("Removed %d expired cached files", removed)
