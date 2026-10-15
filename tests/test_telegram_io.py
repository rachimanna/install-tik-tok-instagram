import pytest
from aiogram.enums import ButtonStyle
from aiogram.exceptions import TelegramEntityTooLarge, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendVideo

from bot import keyboards, texts
from bot.pipeline import SendFailed, SendTooLarge
from bot.telegram_io import with_retries

METHOD = SendVideo(chat_id=1, video="x")


async def no_sleep(monkeypatch):
    import asyncio
    real = asyncio.sleep

    async def fast(_d, *a, **k):
        await real(0)
    monkeypatch.setattr(asyncio, "sleep", fast)


async def test_retry_after_then_success(monkeypatch):
    await no_sleep(monkeypatch)
    calls = []

    async def call():
        calls.append(1)
        if len(calls) == 1:
            raise TelegramRetryAfter(METHOD, "flood", retry_after=3)
        return "ok"

    assert await with_retries(call) == "ok" and len(calls) == 2


async def test_entity_too_large_not_retried():
    calls = []

    async def call():
        calls.append(1)
        raise TelegramEntityTooLarge(METHOD, "Request Entity Too Large")

    with pytest.raises(SendTooLarge):
        await with_retries(call)
    assert len(calls) == 1


async def test_network_errors_bounded(monkeypatch):
    await no_sleep(monkeypatch)
    calls = []

    async def call():
        calls.append(1)
        raise TelegramNetworkError(METHOD, "boom")

    with pytest.raises(SendFailed):
        await with_retries(call)
    assert len(calls) == 3


def test_video_keyboard_buttons():
    kb = keyboards.video_keyboard("TOKEN", "https://vm.tiktok.com/x/")
    doc, src = kb.inline_keyboard[0][0], kb.inline_keyboard[1][0]
    assert doc.text == texts.BTN_DOCUMENT and doc.callback_data == "doc:TOKEN"
    assert doc.style == ButtonStyle.SUCCESS
    assert src.text == texts.BTN_SOURCE and src.url == "https://vm.tiktok.com/x/"
    assert src.style == ButtonStyle.PRIMARY
    assert len(doc.callback_data.encode()) <= 64
