"""Сценарий целиком на фейковых провайдере и Telegram: статусы, очистка, лимиты."""

import asyncio
import shutil
import threading
import time
from pathlib import Path

import pytest

from bot import texts
from bot.config import Settings
from bot.file_store import FileStore
from bot.limits import UserLimiter
from bot.links import Platform
from bot.net_guard import HopResponse
from bot.pipeline import Pipeline, SendTooLarge
from bot.providers.base import DownloadedVideo, DownloadResult, ErrorCode, ProviderError, Watermark

MB = 1024 * 1024
TT_URL = "https://www.tiktok.com/@u/video/7500000000000000000"


class FakeStatus:
    def __init__(self, log):
        self.log = log

    async def update(self, text):
        self.log.append(("update", text))

    async def finish(self, text, source_url=None):
        self.log.append(("finish", text, source_url))


class FakeSender:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail

    async def send_video(self, video, caption, token, source_url):
        assert video.path.is_file()
        if self.fail:
            raise self.fail
        self.sent.append((video.path, caption, token, source_url))


class FakeProvider:
    name = "fake"

    def __init__(self, behaviour, sample=None):
        self.behaviour = behaviour
        self.sample = sample
        self.workdirs = []
        self.calls = 0

    def supports(self, link):
        return True

    def download(self, link, workdir, cancel):
        self.calls += 1
        self.workdirs.append(workdir)
        return self.behaviour(self, link, workdir, cancel)


def ok_behaviour(n=1, watermark=Watermark.CLEAN):
    def run(self, link, workdir, cancel):
        videos = []
        for i in range(n):
            p = workdir / f"v{i}.mp4"
            shutil.copyfile(self.sample, p)
            (workdir / f"v{i}.thumb.jpg").write_bytes(b"x")
            videos.append(DownloadedVideo(p, p.stat().st_size, 360, 640, 2.0, watermark=watermark))
        return DownloadResult(platform=link.platform, videos=videos)
    return run


def make_pipeline(tmp_path, provider, **overrides):
    settings = Settings(bot_token="x", work_dir=tmp_path / "data", **overrides)
    store = FileStore(settings.cache_dir, 600, 100 * MB)

    async def fetch(url):
        return HopResponse(301, TT_URL)

    async def allow(host, port):
        return None

    p = Pipeline(settings=settings, providers=[provider], store=store,
                 limiter=UserLimiter(settings.max_tasks_per_user), fetch=fetch, check_host=allow)
    p.prepare_dirs()
    return p


async def run(p, text, sender=None, user=1):
    log = []

    async def open_status(initial):
        log.append(("open", initial))
        return FakeStatus(log)

    reply = await p.handle(user, text, open_status, sender or FakeSender())
    return reply, log


def jobs_empty(p):
    return not any(p.settings.jobs_dir.iterdir())


async def test_success_flow_single_status_and_cleanup(tmp_path, sample_mp4):
    provider = FakeProvider(ok_behaviour(), sample_mp4)
    p = make_pipeline(tmp_path, provider)
    sender = FakeSender()
    reply, log = await run(p, "https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/", sender)
    assert reply is None
    assert log == [("open", texts.CHECKING), ("update", texts.DOWNLOADING),
                   ("update", texts.SENDING), ("finish", None, None)]
    assert len(sender.sent) == 1
    path, caption, token, source = sender.sent[0]
    assert "✨ Без водяного знака" in caption and "360×640" in caption
    assert source == "https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/"
    # Временная папка задачи удалена, файл для кнопки «📁» лежит в кэше.
    assert jobs_empty(p)
    assert not provider.workdirs[0].exists()
    stored = p.store.get(token)
    assert stored is not None and stored.path.is_file()
    assert p.limiter.active(1) == 0


async def test_provider_error_cleans_up_and_shows_message(tmp_path):
    def fail(self, link, workdir, cancel):
        (workdir / "partial.part").write_bytes(b"123")
        raise ProviderError(ErrorCode.NOT_FOUND)

    provider = FakeProvider(fail)
    p = make_pipeline(tmp_path, provider)
    reply, log = await run(p, TT_URL)
    assert log[-1][0] == "finish" and "недоступно" in log[-1][1]
    assert "сторис" in log[-1][1]  # не утверждаем, что это обычное видео
    assert jobs_empty(p) and len(p.store) == 0
    assert p.limiter.active(1) == 0


async def test_unexpected_exception_is_hidden_from_user(tmp_path):
    def boom(self, link, workdir, cancel):
        raise RuntimeError("secret internals https://cdn.tiktokcdn.com/x?sig=1")

    p = make_pipeline(tmp_path, FakeProvider(boom))
    _, log = await run(p, TT_URL)
    assert log[-1][1] == texts.UNEXPECTED
    assert jobs_empty(p)


async def test_network_error_retried_once(tmp_path, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    def flaky(self, link, workdir, cancel):
        raise ProviderError(ErrorCode.NETWORK)

    provider = FakeProvider(flaky)
    p = make_pipeline(tmp_path, provider)
    _, log = await run(p, TT_URL)
    assert provider.calls == 2
    assert "связаться" in log[-1][1]


async def test_timeout_sets_cancel_and_cleans(tmp_path):
    seen = {}

    def slow(self, link, workdir, cancel):
        seen["cancel"] = cancel
        (workdir / "partial").write_bytes(b"1")
        cancel.wait(10)
        raise ProviderError(ErrorCode.TIMEOUT)

    p = make_pipeline(tmp_path, FakeProvider(slow), download_timeout=1)
    started = time.monotonic()
    _, log = await run(p, TT_URL)
    assert time.monotonic() - started < 8
    assert seen["cancel"].is_set()
    assert "слишком много времени" in log[-1][1]
    assert jobs_empty(p)


async def test_send_too_large_does_not_loop(tmp_path, sample_mp4):
    sender = FakeSender(fail=SendTooLarge())
    p = make_pipeline(tmp_path, FakeProvider(ok_behaviour(), sample_mp4))
    _, log = await run(p, TT_URL, sender)
    assert "слишком большое" in log[-1][1]
    assert len(p.store) == 0 and jobs_empty(p)


async def test_carousel_partial_notes(tmp_path, sample_mp4):
    def run_(self, link, workdir, cancel):
        res = ok_behaviour(2)(self, link, workdir, cancel)
        res.skipped_non_video = 1
        return res

    sender = FakeSender()
    p = make_pipeline(tmp_path, FakeProvider(run_, sample_mp4))
    _, log = await run(p, "https://www.instagram.com/p/Cabc/", sender)
    assert [s[1].split("\n")[0].split(" · ")[1] for s in sender.sent] == ["1/2", "2/2"]
    assert "Пропущено фото: 1" in log[-1][1]


async def test_per_user_limit(tmp_path, sample_mp4):
    gate = threading.Event()

    def blocking(self, link, workdir, cancel):
        gate.wait(5)
        return ok_behaviour()(self, link, workdir, cancel)

    p = make_pipeline(tmp_path, FakeProvider(blocking, sample_mp4))
    first = asyncio.create_task(run(p, TT_URL, user=7))
    await asyncio.sleep(0.2)
    reply, log = await run(p, TT_URL, user=7)
    assert reply == texts.BUSY and log == []
    other_reply, _ = await asyncio.wait_for(_with_release(run(p, TT_URL, user=8), gate), 10)
    assert other_reply is None
    await first
    assert p.limiter.active(7) == 0


async def _with_release(coro, gate):
    asyncio.get_running_loop().call_later(0.3, gate.set)
    return await coro


async def test_bad_links_answered_without_status(tmp_path):
    p = make_pipeline(tmp_path, FakeProvider(None))
    assert (await run(p, "привет"))[0] == texts.NO_LINK
    assert (await run(p, "https://youtube.com/watch?v=1"))[0] == texts.LINK_ERRORS[
        __import__("bot.links", fromlist=["LinkErrorCode"]).LinkErrorCode.DOMAIN_NOT_ALLOWED]


async def test_redirect_to_private_network_is_blocked(tmp_path):
    p = make_pipeline(tmp_path, FakeProvider(None))

    async def evil_fetch(url):
        return HopResponse(302, "http://127.0.0.1:8080/")

    p.fetch = evil_fetch
    _, log = await run(p, "https://vm.tiktok.com/ZMevil/")
    assert log[-1][1] == texts.LINK_ERRORS[__import__("bot.links", fromlist=["x"]).LinkErrorCode.BAD_PORT]


_real_sleep = asyncio.sleep


async def _no_sleep(delay, *a, **k):
    await _real_sleep(0)
