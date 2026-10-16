"""Вход в служебный аккаунт Instagram через instagrapi (Client подменён — сеть не нужна)."""

import asyncio
import datetime
import http.cookiejar
import stat
import threading

import instagrapi
import pytest
from instagrapi.exceptions import BadPassword, ChallengeRequired, PleaseWaitFewMinutes, TwoFactorRequired
from instagrapi.mixins.challenge import ChallengeChoice

from bot import texts
from bot.config import Settings
from bot.file_store import FileStore
from bot.instagram_auth import (LOGIN_COOLDOWN, InstagramAuth, InstagramLoginError, LoginProblem,
                                login_to_cookies_file)
from bot.limits import UserLimiter
from bot.pipeline import Pipeline
from bot.providers.base import ErrorCode, ProviderError

MB = 1024 * 1024
SESSION = "4242424242%3AabcdefGHIJKLmnop%3A12%3AAYdQwerty"


def fake_client(monkeypatch, *, error=None, challenge=None, expected_code="123456", two_step=False):
    """challenge: None | ChallengeChoice — Instagram просит код по этому каналу.
    two_step — новый вход Instagram: TwoFactorRequired, код передаётся вторым login()."""
    state = {"logins": [], "loaded": [], "dumped": []}

    class FakeClient:
        challenge_code_handler = None

        def __init__(self):
            self._sessionid = None

        def load_settings(self, path):
            state["loaded"].append(path)

        def dump_settings(self, path):
            path.write_text("{}")
            state["dumped"].append(path)

        def login(self, username, password, verification_code=""):
            state["logins"].append((username, password, verification_code))
            if error:
                raise error
            if two_step:
                if not verification_code:
                    raise TwoFactorRequired("Instagram returned a Bloks two-factor context from the CAA "
                                            "login flow; provide verification_code for login")
                if verification_code != expected_code:
                    raise TwoFactorRequired("Invalid verification code")
            if challenge is not None:
                code = self.challenge_code_handler(username, challenge)
                print(f'Code entered "{code}" for {username}')  # как делает instagrapi
                if not code:
                    raise ChallengeRequired("Challenge code required. Provide it via challenge_code_handler")
                if code != expected_code:
                    raise ChallengeRequired("Wrong security code")
            self._sessionid = SESSION
            return True

        @property
        def sessionid(self):
            return self._sessionid

    monkeypatch.setattr(instagrapi, "Client", FakeClient)
    return state


def test_login_writes_cookies_for_ytdlp(tmp_path, monkeypatch, capsys):
    state = fake_client(monkeypatch)
    target = tmp_path / "s" / "c.txt"
    login_to_cookies_file("botacc", "secret-pass", target)
    assert state["logins"] == [("botacc", "secret-pass", "")]
    jar = http.cookiejar.MozillaCookieJar(str(target))
    jar.load(ignore_discard=True, ignore_expires=True)
    assert {c.name: c.value for c in jar}["sessionid"] == SESSION
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    device = target.with_name("instagram_device.json")
    assert device.is_file() and stat.S_IMODE(device.stat().st_mode) == 0o600
    assert "secret-pass" not in target.read_text()

    import yt_dlp
    with yt_dlp.YoutubeDL({"cookiefile": str(target), "quiet": True}) as ydl:
        assert ydl.get_info_extractor("InstagramStory")._is_logged_in

    # Второй вход использует тот же «телефон».
    login_to_cookies_file("botacc", "secret-pass", target)
    assert state["loaded"] == [device]


def test_email_code_is_passed_and_not_printed(tmp_path, monkeypatch, capsys):
    fake_client(monkeypatch, challenge=ChallengeChoice.EMAIL)
    channels = []

    def provider(channel):
        channels.append(channel)
        return " 123456 "

    login_to_cookies_file("u", "p", tmp_path / "c.txt", provider)
    assert channels == ["email"]
    assert "123456" not in capsys.readouterr().out


def test_two_step_code_passed_to_second_login(tmp_path, monkeypatch, caplog):
    state = fake_client(monkeypatch, two_step=True)
    asked = []

    def provider(channel):
        asked.append(channel)
        return "123456"

    login_to_cookies_file("u", "p", tmp_path / "c.txt", provider)
    assert asked == ["2fa"]
    assert [v for _, _, v in state["logins"]] == ["", "123456"]


def test_two_step_without_owner_needs_iglogin(tmp_path, monkeypatch):
    fake_client(monkeypatch, two_step=True)
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "p", tmp_path / "c.txt")
    assert err.value.problem is LoginProblem.CHECKPOINT


def test_two_step_wrong_code(tmp_path, monkeypatch, caplog):
    fake_client(monkeypatch, two_step=True)
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "secret-pass", tmp_path / "c.txt", lambda c: "000000")
    assert err.value.problem is LoginProblem.TWO_FACTOR
    # Точная причина видна в логе, а пароль и код — нет.
    assert "TwoFactorRequired: Invalid verification code" in caplog.text
    assert "secret-pass" not in caplog.text and "000000" not in caplog.text


def test_two_step_code_timeout(tmp_path, monkeypatch):
    fake_client(monkeypatch, two_step=True)
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "p", tmp_path / "c.txt", lambda c: None)
    assert err.value.problem is LoginProblem.CODE_TIMEOUT


def test_needs_upgrade_is_unsupported(tmp_path, monkeypatch):
    from instagrapi.exceptions import UnknownError
    fake_client(monkeypatch, error=UnknownError(
        "Your version of Instagram is out of date. Please upgrade your app to log in to Instagram."))
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "p", tmp_path / "c.txt", lambda c: "1")
    assert err.value.problem is LoginProblem.UNSUPPORTED
    assert "INSTAGRAM_COOKIES" in texts.IGLOGIN_FAILED["unsupported"]


@pytest.mark.parametrize("error, problem", [
    (BadPassword("The password you entered is incorrect."), LoginProblem.BAD_CREDENTIALS),
    (TwoFactorRequired("Two-factor authentication required"), LoginProblem.CHECKPOINT),
    (PleaseWaitFewMinutes("Please wait a few minutes before you try again."), LoginProblem.BLOCKED),
])
def test_login_errors_classified(tmp_path, monkeypatch, error, problem):
    fake_client(monkeypatch, error=error)
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "p", tmp_path / "c.txt")
    assert err.value.problem is problem
    assert not (tmp_path / "c.txt").exists()


def test_challenge_without_owner_is_checkpoint(tmp_path, monkeypatch):
    fake_client(monkeypatch, challenge=ChallengeChoice.EMAIL)
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "p", tmp_path / "c.txt")  # автоматический вход — без кода
    assert err.value.problem is LoginProblem.CHECKPOINT


def test_code_not_sent_in_time(tmp_path, monkeypatch):
    fake_client(monkeypatch, challenge=ChallengeChoice.SMS)
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "p", tmp_path / "c.txt", lambda channel: None)
    assert err.value.problem is LoginProblem.CODE_TIMEOUT


def test_wrong_code(tmp_path, monkeypatch):
    fake_client(monkeypatch, challenge=ChallengeChoice.EMAIL)
    with pytest.raises(InstagramLoginError) as err:
        login_to_cookies_file("u", "p", tmp_path / "c.txt", lambda channel: "000000")
    assert err.value.problem is LoginProblem.CHECKPOINT


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def ok_login(user, password, target, provider=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\t{SESSION}\n")
    return target


async def test_auto_login_respects_cooldown(tmp_path, caplog):
    attempts = []
    clock = Clock()

    def login(user, password, target, provider=None):
        attempts.append(provider)
        if len(attempts) == 1:
            raise InstagramLoginError(LoginProblem.CHECKPOINT)
        return ok_login(user, password, target)

    auth = InstagramAuth("botacc", "pw", tmp_path, clock=clock, login_func=login)
    with pytest.raises(InstagramLoginError):
        await auth.ensure()
    assert "/iglogin" in caplog.text and "pw" not in caplog.text
    with pytest.raises(InstagramLoginError):
        await auth.ensure()  # сразу не повторяем
    assert len(attempts) == 1 and attempts[0] is None  # автоматический вход — без запроса кода
    clock.t += LOGIN_COOLDOWN + 1
    await auth.ensure()
    assert auth.current_path is not None


async def test_interactive_login_bridges_code_from_telegram(tmp_path, monkeypatch):
    fake_client(monkeypatch, challenge=ChallengeChoice.EMAIL)
    asked = []

    async def ask_code(channel):
        asked.append(channel)
        await asyncio.sleep(0.05)  # владелец набирает код
        return "123456"

    auth = InstagramAuth("botacc", "pw", tmp_path)
    sessionid = await auth.interactive_login(ask_code)
    assert sessionid == SESSION and asked == ["email"]
    assert auth.current_path is not None


async def test_interactive_login_ignores_cooldown(tmp_path):
    clock = Clock()
    calls = []

    def login(user, password, target, provider=None):
        calls.append(provider)
        if len(calls) == 1:
            raise InstagramLoginError(LoginProblem.CHECKPOINT)
        return ok_login(user, password, target)

    auth = InstagramAuth("u", "p", tmp_path, clock=clock, login_func=login)
    with pytest.raises(InstagramLoginError):
        await auth.ensure()

    async def ask_code(channel):
        return "1"

    assert await auth.interactive_login(ask_code) == SESSION  # владелец может войти сразу
    assert calls[1] is not None


# --- сценарий в боте --------------------------------------------------------

class StoryProvider:
    name = "fake"

    def __init__(self, auth, fail_first=False, clock=None, advance=0):
        self.auth, self.fail_first, self.clock, self.advance = auth, fail_first, clock, advance
        self.calls = 0

    def supports(self, link):
        return True

    def download(self, link, workdir, cancel):
        self.calls += 1
        assert self.auth.current_path is not None
        if self.fail_first and self.calls == 1:
            if self.clock:
                self.clock.t += self.advance
            raise ProviderError(ErrorCode.LOGIN_REQUIRED)
        raise ProviderError(ErrorCode.NOT_FOUND)


async def _allow(host, port):
    return None


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


async def test_pipeline_relogins_when_session_expired(tmp_path):
    logins = []

    def login(*args):
        logins.append(1)
        return ok_login(*args)

    clock = Clock()
    auth = InstagramAuth("u", "p", tmp_path, clock=clock, login_func=login)
    provider = StoryProvider(auth, fail_first=True, clock=clock, advance=LOGIN_COOLDOWN + 1)
    reply, log = await run_story(tmp_path, auth, provider)
    assert provider.calls == 2 and len(logins) == 2
    assert "Сторис не найдена" in log[-1]


async def test_no_relogin_right_after_login(tmp_path):
    logins = []

    def login(*args):
        logins.append(1)
        return ok_login(*args)

    clock = Clock()
    auth = InstagramAuth("u", "p", tmp_path, clock=clock, login_func=login)
    provider = StoryProvider(auth, fail_first=True, clock=clock, advance=5)
    reply, log = await run_story(tmp_path, auth, provider)
    assert provider.calls == 1 and len(logins) == 1


async def test_pipeline_login_needs_owner(tmp_path):
    def login(*args):
        raise InstagramLoginError(LoginProblem.CHECKPOINT)

    auth = InstagramAuth("u", "p", tmp_path, login_func=login)
    provider = StoryProvider(auth)
    reply, log = await run_story(tmp_path, auth, provider)
    assert log[-1] == texts.INSTAGRAM_LOGIN_FAILED and "/iglogin" in log[-1]
    assert provider.calls == 0


def test_settings(monkeypatch, tmp_path):
    for k in ("YTDLP_COOKIES_FILE", "YTDLP_COOKIES_B64", "INSTAGRAM_COOKIES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("WORK_DIR", str(tmp_path))
    monkeypatch.setenv("INSTAGRAM_USERNAME", "botacc")
    monkeypatch.setenv("INSTAGRAM_PASSWORD", "very-secret")
    monkeypatch.setenv("ADMIN_IDS", "111, 222")
    s = Settings.from_env()
    assert s.instagram_login_enabled and s.instagram_stories_enabled
    assert s.admin_ids == frozenset({111, 222})
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
