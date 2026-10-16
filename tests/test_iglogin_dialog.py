"""Диалог /iglogin на настоящем aiogram: бот просит код, владелец присылает его сообщением."""

import asyncio
import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Update

from bot import texts
from bot.config import Settings
from bot.file_store import FileStore
from bot.handlers import build_router
from bot.instagram_auth import InstagramAuth
from bot.limits import UserLimiter
from bot.pipeline import Pipeline

TOKEN = "123456789:" + "A" * 35
SESSION = "4242424242%3AabcdefGHIJKLmnop%3A12%3AAYdQwerty"


class Session(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[str] = []
        self.event = asyncio.Event()

    async def make_request(self, bot, method, timeout=None):
        assert isinstance(method, SendMessage), type(method)
        self.sent.append(method.text)
        self.event.set()
        return method.__returning__(message_id=len(self.sent), date=datetime.datetime.now(),
                                    chat={"id": method.chat_id, "type": "private"}, text=method.text)

    async def stream_content(self, *a, **k):
        raise NotImplementedError

    async def close(self):
        pass


def update(uid, text, n):
    return Update.model_validate({"update_id": n, "message": {
        "message_id": n, "date": int(datetime.datetime.now().timestamp()),
        "chat": {"id": uid, "type": "private"}, "from": {"id": uid, "is_bot": False, "first_name": "u"},
        "text": text}})


async def wait_for(session, predicate, timeout=5):
    async def loop():
        while not any(predicate(t) for t in session.sent):
            session.event.clear()
            await session.event.wait()
    await asyncio.wait_for(loop(), timeout)


def make(tmp_path, login, admin_ids=frozenset()):
    settings = Settings(bot_token=TOKEN, work_dir=tmp_path, instagram_username="u",
                        instagram_password="p", admin_ids=admin_ids)
    auth = InstagramAuth("u", "p", tmp_path, login_func=login)
    pipeline = Pipeline(settings=settings, providers=[], store=FileStore(tmp_path / "c", 60, 10**6),
                        limiter=UserLimiter(1), fetch=None, instagram_auth=auth)
    dp = Dispatcher()
    dp.include_router(build_router(pipeline, pipeline.store, settings))
    session = Session()
    return dp, Bot(TOKEN, session=session), session, auth


async def test_owner_sends_code_in_chat(tmp_path):
    received = []

    def login(user, password, target, provider):
        received.append(provider("email"))  # Instagram просит код из почты
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\t{SESSION}\n")
        return target

    dp, bot, session, auth = make(tmp_path, login)
    task = asyncio.create_task(dp.feed_update(bot, update(7, "/iglogin", 1)))
    await wait_for(session, lambda t: "код подтверждения" in t)
    await dp.feed_update(bot, update(7, " 654321 ", 2))
    await asyncio.wait_for(task, 5)
    assert received == ["654321"]
    assert texts.IGLOGIN_CODE_RECEIVED in session.sent
    assert any(f"sessionid={SESSION}" in t and "INSTAGRAM_COOKIES" in t for t in session.sent)
    assert auth.current_path is not None


async def test_non_admin_cannot_login(tmp_path):
    def login(*a):
        raise AssertionError("не должен входить")

    dp, bot, session, _ = make(tmp_path, login, admin_ids=frozenset({1}))
    await dp.feed_update(bot, update(7, "/iglogin", 1))
    assert session.sent == [texts.IGLOGIN_FORBIDDEN]


async def test_digits_without_pending_login_are_not_a_code(tmp_path):
    dp, bot, session, _ = make(tmp_path, lambda *a: None)
    await dp.feed_update(bot, update(7, "123456", 1))
    assert session.sent == [texts.NO_LINK]
