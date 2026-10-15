"""Инлайн-кнопки. Цвет задаётся полем style (Bot API 9.4+): success — зелёный,
primary — синий. Старые клиенты Telegram просто показывают обычные кнопки."""

from __future__ import annotations

from aiogram.enums import ButtonStyle
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import texts

DOC_PREFIX = "doc:"
HELP_DATA = "help"


def video_keyboard(token: str | None, source_url: str) -> InlineKeyboardMarkup:
    rows = []
    if token:
        rows.append([InlineKeyboardButton(text=texts.BTN_DOCUMENT, callback_data=DOC_PREFIX + token,
                                          style=ButtonStyle.SUCCESS)])
    rows.append([InlineKeyboardButton(text=texts.BTN_SOURCE, url=source_url,
                                      style=ButtonStyle.PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def source_keyboard(source_url: str) -> InlineKeyboardMarkup:
    return video_keyboard(None, source_url)


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BTN_HELP, callback_data=HELP_DATA, style=ButtonStyle.PRIMARY),
    ]])
