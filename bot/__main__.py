"""Точка входа: python -m bot"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from dotenv import load_dotenv

from .config import ConfigError, Settings
from .file_store import FileStore
from .handlers import build_router, sweep_forever
from .limits import UserLimiter
from .net_guard import aiohttp_fetcher, make_http_session
from .pipeline import Pipeline
from .providers import build_providers
from .redact import setup_logging

log = logging.getLogger("bot")


async def main() -> None:
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"Ошибка настройки: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    setup_logging(settings.log_level)

    session_kwargs = {"timeout": settings.send_timeout}
    if settings.telegram_api_url:
        session_kwargs["api"] = TelegramAPIServer.from_base(settings.telegram_api_url, is_local=True)
    bot = Bot(settings.bot_token, session=AiohttpSession(**session_kwargs),
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    store = FileStore(settings.cache_dir, settings.file_ttl_seconds, settings.cache_max_bytes)
    http = make_http_session()
    pipeline = Pipeline(settings=settings, providers=build_providers(settings), store=store,
                        limiter=UserLimiter(settings.max_tasks_per_user), fetch=aiohttp_fetcher(http))
    pipeline.prepare_dirs()

    dp = Dispatcher()
    dp.include_router(build_router(pipeline, store, settings))
    sweeper = asyncio.create_task(sweep_forever(store))
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать"),
            BotCommand(command="help", description="Как пользоваться"),
        ])
        me = await bot.get_me()
        log.info("Bot @%s started, upload limit %d MB", me.username, settings.max_upload_bytes // 2**20)
        await dp.start_polling(bot, handle_as_tasks=True)
    finally:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        await http.close()
        await bot.session.close()
        pipeline.shutdown()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
