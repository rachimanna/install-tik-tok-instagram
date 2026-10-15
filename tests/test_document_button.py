"""Кнопка «📁 Скачать оригинальным файлом» на настоящем aiogram с подменённой сессией API."""

import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendDocument, SendVideo
from aiogram.types import Chat, Document, InputFile, Message, Video

from bot.file_store import FileStore
from bot.providers.base import DownloadedVideo
from bot.telegram_io import TelegramSender, send_original

TOKEN = "123456789:" + "A" * 35


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        chat = Chat(id=1, type="private")
        base = dict(message_id=len(self.requests), date=datetime.datetime.now(), chat=chat)
        if isinstance(method, SendVideo):
            video = Video(file_id="VID", file_unique_id="v", width=360, height=640, duration=2)
            return method.__returning__(**base, video=video)
        if isinstance(method, SendDocument):
            doc = Document(file_id="DOC_FILE_ID", file_unique_id="d")
            return method.__returning__(**base, document=doc)
        raise AssertionError(type(method))

    async def stream_content(self, *a, **k):
        raise NotImplementedError

    async def close(self):
        pass


async def test_video_then_document_button(tmp_path, sample_mp4):
    session = FakeSession()
    bot = Bot(TOKEN, session=session)
    store = FileStore(tmp_path / "cache", 600, 10**9)
    store.reset_dir()
    src = tmp_path / "v.mp4"
    src.write_bytes(sample_mp4.read_bytes())
    item = store.put(src, "tiktok_v.mp4")

    video = DownloadedVideo(item.path, item.size, 360, 640, 2.0)
    await TelegramSender(bot, 1, 5, 30).send_video(video, "cap", item.token, "https://vm.tiktok.com/x/")
    sent = session.requests[0]
    assert isinstance(sent, SendVideo) and sent.supports_streaming
    assert isinstance(sent.video, InputFile)
    assert sent.reply_markup.inline_keyboard[0][0].callback_data == f"doc:{item.token}"

    # Первое нажатие: файл уходит документом как есть, без определения типа.
    assert await send_original(bot, store, item.token, 1, 30) == "ok"
    doc_req = session.requests[1]
    assert isinstance(doc_req, SendDocument) and isinstance(doc_req.document, InputFile)
    assert doc_req.disable_content_type_detection is True
    assert doc_req.document.filename == "tiktok_v.mp4"
    assert not Path(item.path or tmp_path / "gone").exists()  # локальный файл удалён

    # Повторное нажатие: используется сохранённый file_id.
    assert await send_original(bot, store, item.token, 1, 30) == "ok"
    assert session.requests[2].document == "DOC_FILE_ID"


async def test_expired_button(tmp_path):
    bot = Bot(TOKEN, session=FakeSession())
    store = FileStore(tmp_path / "cache", 600, 10**9)
    assert await send_original(bot, store, "missing", 1, 30) == "expired"
