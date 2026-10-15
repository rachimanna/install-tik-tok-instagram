"""Кнопка «📁 Скачать оригинальным файлом» на настоящем aiogram с подменённой сессией API."""

import datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendAudio, SendDocument, SendMediaGroup, SendMessage, SendPhoto, SendVideo
from aiogram.types import Audio, Chat, Document, InputFile, Message, PhotoSize, Video

from bot.file_store import FileStore
from bot.links import Platform
from bot.providers.base import AudioTrack, DownloadedMedia, MediaKind
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
        if isinstance(method, SendPhoto):
            return method.__returning__(**base, photo=[PhotoSize(file_id="P", file_unique_id="p",
                                                                 width=1, height=1)])
        if isinstance(method, SendMediaGroup):
            out = []
            for i, m in enumerate(method.media):
                if m.type == "document":
                    extra = {"document": Document(file_id=f"DOC{i}", file_unique_id=f"d{i}")}
                elif m.type == "audio":
                    extra = {"audio": Audio(file_id=f"A{i}", file_unique_id=f"a{i}", duration=2)}
                else:
                    extra = {"photo": [PhotoSize(file_id="P", file_unique_id="p", width=1, height=1)]}
                out.append(Message(message_id=100 + i, date=datetime.datetime.now(), chat=chat, **extra))
            return out
        if isinstance(method, SendAudio):
            return method.__returning__(**base, audio=Audio(file_id="A", file_unique_id="a", duration=2))
        if isinstance(method, SendMessage):
            return method.__returning__(**base, text=method.text)
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
    item = store.put([(src, "tiktok_v.mp4")])
    part = item.parts[0]

    video = DownloadedMedia(part.path, part.size, MediaKind.VIDEO, 360, 640, 2.0)
    await TelegramSender(bot, 1, 5, 30).send_single(video, "cap", item.token, "https://vm.tiktok.com/x/")
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
    assert part.path is None  # локальный файл удалён

    # Повторное нажатие: используется сохранённый file_id.
    assert await send_original(bot, store, item.token, 1, 30) == "ok"
    assert session.requests[2].document == "DOC_FILE_ID"


async def test_expired_button(tmp_path):
    bot = Bot(TOKEN, session=FakeSession())
    store = FileStore(tmp_path / "cache", 600, 10**9)
    assert await send_original(bot, store, "missing", 1, 30) == "expired"


async def test_album_and_documents_group(tmp_path, sample_jpg, sample_mp4):
    session = FakeSession()
    bot = Bot(TOKEN, session=session)
    store = FileStore(tmp_path / "cache", 600, 10**9)
    store.reset_dir()
    sources = []
    for i in range(12):
        src = tmp_path / f"{i}.jpg"
        src.write_bytes(sample_jpg.read_bytes())
        sources.append((src, f"tiktok_{i}.jpg"))
    item = store.put(sources)
    photos = [DownloadedMedia(p.path, p.size, MediaKind.PHOTO, 1080, 1350, image_codec="mjpeg")
              for p in item.parts]
    # Одно фото в WebP — Telegram не примет его как фото, уйдёт файлом.
    photos[5].image_codec = "webp"

    sender = TelegramSender(bot, 1, None, 30)
    await sender.send_album(photos)
    await sender.send_summary("📦 12 фото", item.token, "https://www.tiktok.com/@u/photo/1")
    kinds = [type(r).__name__ for r in session.requests]
    # 11 фото: альбом из 10 + одиночное фото, затем WebP документом, затем сводка с кнопками
    assert kinds == ["SendMediaGroup", "SendPhoto", "SendDocument", "SendMessage"]
    assert len(session.requests[0].media) == 10
    summary = session.requests[3]
    assert summary.reply_markup.inline_keyboard[0][0].text == "📁 Скачать оригиналы файлами"

    session.requests.clear()
    assert await send_original(bot, store, item.token, 1, 30) == "ok"
    assert [type(r).__name__ for r in session.requests] == ["SendMediaGroup", "SendMediaGroup"]
    assert [len(r.media) for r in session.requests] == [10, 2]
    assert all(m.type == "document" and m.disable_content_type_detection for r in session.requests
               for m in r.media)
    assert all(p.path is None and p.document_file_id for p in item.parts)


async def test_send_audios_single_and_group(tmp_path, sample_mp4):
    from bot import media
    session = FakeSession()
    sender = TelegramSender(Bot(TOKEN, session=session), 1, 7, 30)
    items = []
    for n in range(3):
        audio = media.extract_audio(sample_mp4, tmp_path / f"a{n}")
        item = DownloadedMedia(sample_mp4, 1, MediaKind.VIDEO, duration=2.0, title="Песня", author="автор")
        item.audio = AudioTrack(audio, audio.stat().st_size, 2.0, f"tiktok_{n}.m4a")
        items.append(item)

    await sender.send_audios(Platform.TIKTOK, items[:1])
    single = session.requests[-1]
    assert isinstance(single, SendAudio)
    assert (single.title, single.performer, single.duration) == ("Песня", "автор", 2)
    assert single.audio.filename == "tiktok_0.m4a"

    await sender.send_audios(Platform.TIKTOK, items)
    group = session.requests[-1]
    assert isinstance(group, SendMediaGroup) and [m.type for m in group.media] == ["audio"] * 3
