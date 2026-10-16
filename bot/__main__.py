"""Точка входа: python -m bot

Режимы (BOT_MODE):
* polling — бот сам опрашивает Telegram. Для VPS, Codespaces, Render Background Worker.
* webhook — Telegram присылает обновления на HTTPS-адрес. Для Render Web Service
  (в том числе бесплатного: входящий вебхук будит уснувший сервис).
"""

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
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web
from dotenv import load_dotenv

from .config import ConfigError, Settings
from .file_store import FileStore
from .handlers import build_router, sweep_forever
from .instagram_auth import InstagramAuth
from .limits import UserLimiter
from .net_guard import aiohttp_fetcher, make_http_session
from .pipeline import Pipeline
from .providers import build_providers
from .redact import setup_logging

log = logging.getLogger("bot")


async def run_polling(bot: Bot, dp: Dispatcher) -> None:
    # Если раньше был включён вебхук, getUpdates не заработает — снимаем его.
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, handle_as_tasks=True)


def build_webhook_app(bot: Bot, dp: Dispatcher, settings: Settings) -> web.Application:
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.webhook_secret_token,
                         handle_in_background=True).register(app, path=settings.webhook_path)
    return app


async def run_webhook(bot: Bot, dp: Dispatcher, settings: Settings) -> None:
    runner = web.AppRunner(build_webhook_app(bot, dp, settings), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    url = settings.webhook_base_url + settings.webhook_path
    # Ожидающие обновления не сбрасываем: на Render именно они будят уснувший сервис.
    await bot.set_webhook(url, secret_token=settings.webhook_secret_token,
                          allowed_updates=dp.resolve_used_update_types(), drop_pending_updates=False)
    log.info("Webhook mode: listening on port %d, webhook %s", settings.port, url)
    await dp.emit_startup(bot=bot)
    try:
        await asyncio.Event().wait()
    finally:
        # Вебхук при остановке НЕ удаляем: иначе уснувший сервис больше не разбудить.
        await dp.emit_shutdown(bot=bot)
        await runner.cleanup()


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
    auth = None
    cookie_source = None
    if settings.instagram_login_enabled and not settings.ytdlp_cookies_file:
        auth = InstagramAuth(settings.instagram_username, settings.instagram_password, settings.work_dir)
        cookie_source = lambda: auth.current_path  # noqa: E731
    pipeline = Pipeline(settings=settings, providers=build_providers(settings, cookie_source), store=store,
                        limiter=UserLimiter(settings.max_tasks_per_user), fetch=aiohttp_fetcher(http),
                        instagram_auth=auth)
    pipeline.prepare_dirs()

    dp = Dispatcher()
    dp.include_router(build_router(pipeline, store, settings))
    sweeper = asyncio.create_task(sweep_forever(store))
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать"),
            BotCommand(command="help", description="Как пользоваться"),
            BotCommand(command="iglogin", description="Вход в Instagram для сторис (для администратора)"),
        ])
        me = await bot.get_me()
        log.info("Bot @%s started (%s), upload limit %d MB", me.username, settings.mode,
                 settings.max_upload_bytes // 2**20)
        if settings.mode == "webhook":
            await run_webhook(bot, dp, settings)
        else:
            await run_polling(bot, dp)
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
