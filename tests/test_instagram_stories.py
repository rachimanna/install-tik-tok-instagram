"""Сторис Instagram: перехват всех элементов (включая фото), выбор нужной сторис, доступ."""

import io
import shutil
import threading
from pathlib import Path

import pytest
import yt_dlp
from yt_dlp.extractor.instagram import _pk_to_id

from bot import texts
from bot.config import ConfigError, Settings
from bot.links import Platform, parse_link
from bot.providers.base import ErrorCode, MediaKind, ProviderError
from bot.providers.ytdlp_provider import YtDlpProvider

MB = 1024 * 1024
VIDEO_PK, PHOTO_PK, OTHER_PK = "3412345678901234567", "3412345678901234568", "3412345678901234569"


class FakeResponse(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(data))}


class FakeStoryIE:
    """Как InstagramStoryIE: _extract_product для каждой сторис, в результат — только видео."""

    def _extract_product(self, raw):
        if raw["kind"] == "video":
            return {"id": _pk_to_id(raw["pk"]), "formats": [{"format_id": "1", "vcodec": "avc1"}],
                    "thumbnails": []}
        return {"id": _pk_to_id(raw["pk"]), "formats": [], "thumbnails": [
            {"url": f"https://scontent.cdninstagram.com/{raw['pk']}.jpg", "width": 1080, "height": 1920}]}


RAW = [{"pk": VIDEO_PK, "kind": "video"}, {"pk": PHOTO_PK, "kind": "photo"}, {"pk": OTHER_PK, "kind": "photo"}]


def make_fake(sample_mp4, jpg, raw=RAW, error=None, capture=True):
    class FakeYDL:
        opened, processed = [], []

        def __init__(self, params):
            self.params = params
            self.workdir = Path(params["paths"]["home"])
            self.ie = FakeStoryIE()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_info_extractor(self, key):
            if not capture:
                raise KeyError(key)
            assert key == "InstagramStory"
            return self.ie

        def extract_info(self, url, download=False, process=False):
            if error:
                raise error
            products = [self.ie._extract_product(r) for r in raw]
            videos = [p for p in products if p["formats"]]  # фото yt-dlp выбрасывает
            # Как настоящий yt-dlp: поля экстрактора добавляются к результату верхнего уровня.
            return {"_type": "playlist", "id": "x", "extractor": "instagram:story",
                    "extractor_key": "InstagramStory", "entries": videos} if videos else None

        def urlopen(self, request):
            FakeYDL.opened.append(request.url)
            return FakeResponse(jpg, request.url)

        def process_ie_result(self, entry, download=True):
            FakeYDL.processed.append(entry["id"])
            assert entry["extractor_key"] == "InstagramStory"
            target = self.workdir / f"{entry['id']}.mp4"
            shutil.copyfile(sample_mp4, target)
            return {**entry, "format_id": "1", "requested_downloads": [{"filepath": str(target)}]}

    return FakeYDL


@pytest.fixture
def run(tmp_path, sample_mp4, sample_jpg):
    def _run(url, **kw):
        fake = make_fake(sample_mp4, sample_jpg.read_bytes(), **kw)
        provider = YtDlpProvider(max_bytes=50 * MB, ydl_class=fake, url_checker=lambda u: None)
        work = tmp_path / "job"
        work.mkdir(exist_ok=True)
        return provider.download(parse_link(url), work, threading.Event()), fake
    return _run


def test_all_user_stories_including_photos(run):
    result, fake = run("https://www.instagram.com/stories/someone/")
    assert [i.kind for i in result.items] == [MediaKind.VIDEO, MediaKind.PHOTO, MediaKind.PHOTO]
    assert result.platform is Platform.INSTAGRAM


def test_single_photo_story_by_id(run):
    # Этот случай yt-dlp сам не отдаёт: фото-сторис выброшена из результата.
    result, fake = run(f"https://www.instagram.com/stories/someone/{PHOTO_PK}/")
    assert [i.kind for i in result.items] == [MediaKind.PHOTO]
    assert fake.opened == [f"https://scontent.cdninstagram.com/{PHOTO_PK}.jpg"]
    assert fake.processed == []


def test_single_video_story_by_id(run):
    result, fake = run(f"https://www.instagram.com/stories/someone/{VIDEO_PK}/")
    assert [i.kind for i in result.items] == [MediaKind.VIDEO]
    assert fake.processed == [_pk_to_id(VIDEO_PK)]


def test_expired_story(run):
    with pytest.raises(ProviderError) as err:
        run("https://www.instagram.com/stories/someone/3412345678900000000/")
    assert err.value.code is ErrorCode.NOT_FOUND


def test_login_required_error(run):
    with pytest.raises(ProviderError) as err:
        run("https://www.instagram.com/stories/someone/", error=yt_dlp.utils.ExtractorError(
            "This content is unreachable. Use --cookies-from-browser or --cookies for the authentication.",
            expected=True))
    assert err.value.code is ErrorCode.LOGIN_REQUIRED
    text = texts.provider_error(err.value.code, Platform.INSTAGRAM, MB, story=True)
    assert "аккаунт" in text


def test_without_capture_falls_back_to_videos(run):
    result, _ = run("https://www.instagram.com/stories/someone/", capture=False)
    assert [i.kind for i in result.items] == [MediaKind.VIDEO]


def test_real_ytdlp_story_extractor_is_allowed_and_hookable():
    from bot.providers.ytdlp_provider import ALLOWED_EXTRACTORS
    with yt_dlp.YoutubeDL({"allowed_extractors": ALLOWED_EXTRACTORS, "quiet": True}) as ydl:
        assert "InstagramStory" in ydl._ies
        assert callable(ydl.get_info_extractor("InstagramStory")._extract_product)


async def test_pipeline_explains_when_no_account(tmp_path):
    from bot.file_store import FileStore
    from bot.limits import UserLimiter
    from bot.pipeline import Pipeline

    settings = Settings(bot_token="x", work_dir=tmp_path)
    p = Pipeline(settings=settings, providers=[], store=FileStore(tmp_path / "c", 60, MB),
                 limiter=UserLimiter(1), fetch=None)

    async def open_status(_):
        raise AssertionError("статус не нужен")

    reply = await p.handle(1, "https://www.instagram.com/stories/someone/123/", open_status, None)
    assert reply == texts.INSTAGRAM_STORIES_DISABLED


def test_cookies_from_base64(tmp_path, monkeypatch):
    import base64
    import stat
    monkeypatch.setenv("BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("WORK_DIR", str(tmp_path))
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)
    content = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tSECRET\n"
    monkeypatch.setenv("YTDLP_COOKIES_B64", base64.b64encode(content.encode()).decode())
    path = Settings.from_env().ytdlp_cookies_file
    assert path.read_text() == content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    monkeypatch.setenv("YTDLP_COOKIES_B64", "не base64")
    with pytest.raises(ConfigError):
        Settings.from_env()
