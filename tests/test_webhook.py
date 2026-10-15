"""Режим вебхука (Render Web Service): health-check, проверка секрета, разбор настроек."""

import asyncio
import datetime

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.filters import CommandStart
from aiohttp.test_utils import TestClient, TestServer

from bot.__main__ import build_webhook_app
from bot.config import ConfigError, Settings

TOKEN = "123456789:" + "A" * 35


class NullSession(BaseSession):
    async def make_request(self, bot, method, timeout=None):
        raise AssertionError("no network")

    async def stream_content(self, *a, **k):
        raise NotImplementedError

    async def close(self):
        pass


def update(text="/start"):
    return {"update_id": 1, "message": {
        "message_id": 1, "date": int(datetime.datetime.now().timestamp()),
        "chat": {"id": 1, "type": "private"}, "from": {"id": 1, "is_bot": False, "first_name": "u"},
        "text": text}}


@pytest.fixture
def env(monkeypatch):
    for key in ("BOT_MODE", "WEBHOOK_BASE_URL", "RENDER_EXTERNAL_URL", "WEBHOOK_SECRET", "PORT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    return monkeypatch


async def test_webhook_secret_and_health(env):
    env.setenv("BOT_MODE", "webhook")
    env.setenv("RENDER_EXTERNAL_URL", "https://my-bot.onrender.com")
    env.setenv("WEBHOOK_SECRET", "base64+/=value")
    settings = Settings.from_env()

    got = asyncio.Event()
    router = Router()

    @router.message(CommandStart())
    async def start(message):
        got.set()

    dp = Dispatcher()
    dp.include_router(router)
    app = build_webhook_app(Bot(TOKEN, session=NullSession()), dp, settings)

    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/health")).status == 200
        bad = await client.post(settings.webhook_path, json=update(),
                                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
        assert bad.status == 401
        assert not got.is_set()
        ok = await client.post(settings.webhook_path, json=update(),
                               headers={"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret_token})
        assert ok.status == 200
        await asyncio.wait_for(got.wait(), 5)


def test_secret_token_has_only_allowed_chars(env):
    env.setenv("WEBHOOK_SECRET", "a+b/c==")
    token = Settings.from_env().webhook_secret_token
    assert token.isalnum() and len(token) <= 256


def test_render_url_used_for_webhook(env):
    env.setenv("BOT_MODE", "webhook")
    env.setenv("RENDER_EXTERNAL_URL", "https://my-bot.onrender.com/")
    env.setenv("PORT", "10000")
    s = Settings.from_env()
    assert s.webhook_base_url == "https://my-bot.onrender.com" and s.port == 10000


def test_webhook_without_url_fails_clearly(env):
    env.setenv("BOT_MODE", "webhook")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_default_mode_is_polling(env):
    assert Settings.from_env().mode == "polling"
