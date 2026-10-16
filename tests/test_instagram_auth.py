"""Вход в служебный аккаунт Instagram (instaloader подменён — сеть не нужна)."""

import asyncio
import http.cookiejar
import stat
import threading

import instaloader
import pytest
import requests
from instaloader.exceptions import (BadCredentialsException, LoginException,
                                    TwoFactorAuthRequiredException)

from bot import texts
from bot.config import Settings
from bot.file_store import FileStore
from bot.instagram_auth import (LOGIN_COOLDOWN, InstagramAuth, InstagramLoginError, LoginProblem,
                                login_to_cookies_file)
from bot.limits import UserLimiter
from bot.links import Platform
from bot.pipeline import Pipeline
from bot.providers.base import DownloadResult, ErrorCode, ProviderError

MB = 1024 * 1024


def fake_instaloader(monkeypatch, error=None, with_session=True):
    calls = []

    class FakeContext:
        def __init__(self):
            self._session = requests.Session()

    class FakeLoader:
        def __init__(self, **kwargs):
            self.context = FakeContext()

        def login(self, user, password):
            calls.append((user, password))
            if error:
                raise error
            jar = self.context._session.cookies
            if with_session:
                jar.set("sessionid", "42%3Aabc%3A7", domain=".instagram.com", path="/")
            jar.set("csrftoken", "tok", domain=".instagram.com", path="/")

        def close(self):
            pass

    monkeypatch.setattr(instaloader, "Instaloader", FakeLoader)
    return calls


def test_login_writes_cookies_for_ytdlp(tmp_path, monkeypatch):
    calls = fake_instaloader(monkeypatch)
    path = login_to_cookies_file("botacc", "secret-pass", tmp_path / "s" / "c.txt")
    assert calls == [("botacc", "secret-pass")]
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    assert {c.name: c.value for c in jar}["sessionid"] == "42%3Aabc%3A7"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "secret-pass" not in path.read_text()

    import yt_dlp
    with yt_dlp.YoutubeDL({"cookiefile": str(path), "quiet": True}) as ydl:
        assert ydl.get_info_extractor("InstagramStory")._is_logged_in


@pytest.mark.parametrize("error, problem", [
    (BadCredentialsException("Login error: Wrong password."), LoginProblem.BAD_CREDENTIALS),
    (TwoFactorAuthRequiredException("Login error: two-factor authentication required."), LoginProblem.TWO_FACTOR),
    (LoginException("Login: Checkpoint required. Point your browser to https://www.instagram.com/challenge/x"),
     LoginProblem.CHECKPOINT),
    (LoginException("Login error: Unexpected response, this might indicate a blocked IP."), LoginProblem.BLOCKED),
    (LoginException("Login error: User nobody does not exist."), LoginProblem.BAD_CREDENTIALS),
])
def test_login_errors_classified(tmp_path, monkeypatch, error, problem):
    fake_instaloader(monkeypatch, error=error)
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "p", tmp_path / "c.txt")
    assert err.value.problem is problem
    assert not (tmp_path / "c.txt").exists()


def test_login_without_session_cookie_fails(tmp_path, monkeypatch):
    fake_instaloader(monkeypatch, with_session=False)
    with pytest.raises(InstagramLoginError):
        login_to_cookies_file("u", "p", tmp_path / "c.txt")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def test_auth_logs_in_once_and_respects_cooldown(tmp_path, caplog):
    attempts = []
    clock = Clock()

    def login(user, password, target):
        attempts.append(user)
        if len(attempts) == 1:
            raise InstagramLoginError(LoginProblem.CHECKPOINT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok")
        return target

    auth = InstagramAuth("botacc", "pw", tmp_path, clock=clock, login_func=login)
    with pytest.raises(InstagramLoginError):
        await auth.ensure()
    assert "Это был я" in caplog.text and "pw" not in caplog.text
    # Сразу повторно не входим — отдаём прошлую ошибку.
    with pytest.raises(InstagramLoginError):
        await auth.ensure()
    assert attempts == ["botacc"]
    clock.t += LOGIN_COOLDOWN + 1
    path = await auth.ensure()
    assert path.read_text() == "ok" and auth.current_path == path
    await auth.ensure()
    assert len(attempts) == 2  # уже вошли — повторно не входим


class StoryProvider:
    name = "fake"

    def __init__(self, auth, fail_first=False, clock=None, advance=0):
        self.auth = auth
        self.fail_first = fail_first
        self.clock = clock
        self.advance = advance
        self.calls = 0

    def supports(self, link):
        return True

    def download(self, link, workdir, cancel):
        self.calls += 1
        assert self.auth.current_path is not None  # к моменту скачивания вход выполнен
        if self.fail_first and self.calls == 1:
            if self.clock:
                self.clock.t += self.advance  # сессия «прожила» какое-то время
            raise ProviderError(ErrorCode.LOGIN_REQUIRED)
        raise ProviderError(ErrorCode.NOT_FOUND)


async def run_story(tmp_path, auth, provider):
    settings = Settings(bot_token="x", work_dir=tmp_path / "d", instagram_username="u",
                        instagram_password="p")
    p = Pipeline(settings=settings, providers=[provider], store=FileStore(tmp_path / "c", 60, MB),
                 limiter=UserLimiter(1), fetch=None, check_host=_allow, instagram_auth=auth)
    p.prepare_dirs()
    log = []

    class Status:
        async def update(self, text):
            log.append(text)

        async def finish(self, text, source_url=None):
            log.append(text)

    async def open_status(text):
        return Status()

    reply = await p.handle(1, "https://www.instagram.com/stories/someone/3412345678901234567/",
                           open_status, None)
    return reply, log


async def _allow(host, port):
    return None


def ok_login(user, password, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("ok")
    return target


async def test_pipeline_relogins_once_when_session_expired(tmp_path):
    logins = []

    def login(user, password, target):
        logins.append(1)
        return ok_login(user, password, target)

    clock = Clock()
    auth = InstagramAuth("u", "p", tmp_path, clock=clock, login_func=login)
    provider = StoryProvider(auth, fail_first=True, clock=clock, advance=LOGIN_COOLDOWN + 1)
    reply, log = await run_story(tmp_path, auth, provider)
    assert reply is None
    assert provider.calls == 2 and len(logins) == 2
    assert "Сторис не найдена" in log[-1]


async def test_no_relogin_right_after_login(tmp_path):
    logins = []

    def login(user, password, target):
        logins.append(1)
        return ok_login(user, password, target)

    clock = Clock()
    auth = InstagramAuth("u", "p", tmp_path, clock=clock, login_func=login)
    provider = StoryProvider(auth, fail_first=True, clock=clock, advance=5)
    reply, log = await run_story(tmp_path, auth, provider)
    assert provider.calls == 1 and len(logins) == 1  # не долбим Instagram входами
    assert "аккаунт" in log[-1]


async def test_pipeline_login_failure_message(tmp_path):
    def login(user, password, target):
        raise InstagramLoginError(LoginProblem.TWO_FACTOR)

    auth = InstagramAuth("u", "p", tmp_path, login_func=login)
    provider = StoryProvider(auth)
    reply, log = await run_story(tmp_path, auth, provider)
    assert log[-1] == texts.INSTAGRAM_LOGIN_FAILED and provider.calls == 0


def test_settings_enable_stories_with_login(monkeypatch, tmp_path):
    for k in ("YTDLP_COOKIES_FILE", "YTDLP_COOKIES_B64", "INSTAGRAM_COOKIES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("WORK_DIR", str(tmp_path))
    monkeypatch.setenv("INSTAGRAM_USERNAME", "botacc")
    monkeypatch.setenv("INSTAGRAM_PASSWORD", "very-secret")
    s = Settings.from_env()
    assert s.instagram_login_enabled and s.instagram_stories_enabled
    assert "very-secret" not in repr(s)


def test_provider_reads_cookies_lazily(tmp_path):
    from bot.links import parse_link
    from bot.providers.ytdlp_provider import YtDlpProvider
    holder = {"path": None}
    provider = YtDlpProvider(max_bytes=MB, cookies_file=lambda: holder["path"])
    work = tmp_path / "job"
    work.mkdir()
    link = parse_link("https://www.instagram.com/stories/someone/")
    assert "cookiefile" not in provider._options(link, work, threading.Event())
    cookies = tmp_path / "c.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    holder["path"] = cookies
    assert provider._options(link, work, threading.Event())["cookiefile"] == str(work / "cookies.txt")
