"""Вход в служебный аккаунт Instagram по логину и паролю владельца бота (для сторис).

Используется, когда cookies нельзя выгрузить вручную (например, на iPhone Safari
скрывает HttpOnly-cookie sessionid). Логин и пароль задаёт владелец бота в переменных
окружения; у пользователей бот пароли не спрашивает.

Вход выполняет instagrapi (https://github.com/subzeroid/instagrapi, 3.0.25). С
сервера Instagram почти всегда просит подтвердить вход кодом из почты или SMS;
instagrapi в этот момент вызывает challenge_code_handler(username, choice) и ждёт код.
Поэтому вход бывает двух видов:
* автоматический (при ссылке на сторис) — без кода: если Instagram требует код,
  вход не удаётся, и бот просит владельца отправить /iglogin;
* по команде /iglogin — бот спрашивает код у владельца в Telegram и ждёт ответа.

После входа sessionid сохраняется в cookies.txt для yt-dlp. Автоматические попытки —
не чаще раза в LOGIN_COOLDOWN секунд, чтобы частые входы не привели к блокировке.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

LOGIN_COOLDOWN = 600
CODE_WAIT_SECONDS = 300


class LoginProblem(str, Enum):
    BAD_CREDENTIALS = "bad_credentials"
    TWO_FACTOR = "two_factor"
    CHECKPOINT = "checkpoint"  # Instagram требует код подтверждения
    CODE_TIMEOUT = "code_timeout"
    BLOCKED = "blocked"
    OTHER = "other"


# Подсказки владельцу бота — пишутся в лог Render.
OWNER_HINTS = {
    LoginProblem.BAD_CREDENTIALS: "неверный логин или пароль в INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD",
    LoginProblem.TWO_FACTOR: ("Instagram не принял код двухэтапной проверки — проверьте, не включена ли "
                              "двухфакторная аутентификация, и повторите /iglogin"),
    LoginProblem.CHECKPOINT: "Instagram просит код подтверждения — отправьте боту команду /iglogin",
    LoginProblem.CODE_TIMEOUT: "код подтверждения не пришёл вовремя — отправьте /iglogin ещё раз",
    LoginProblem.BLOCKED: "Instagram временно ограничил вход с IP сервера — попробуйте позже",
    LoginProblem.OTHER: "вход не удался, подробности выше в логе",
}


class InstagramLoginError(Exception):
    def __init__(self, problem: LoginProblem, detail: str = "") -> None:
        super().__init__(f"{problem.value}: {detail}" if detail else problem.value)
        self.problem = problem


def _short(exc: Exception) -> str:
    from .redact import redact
    return redact(" ".join(str(exc).split()))[:600]


def _classify(exc: Exception, code_missing: bool, interactive: bool = False) -> LoginProblem:
    from instagrapi.exceptions import (BadPassword, ChallengeError, PleaseWaitFewMinutes,
                                       TwoFactorRequired)
    text = str(exc).lower()
    if isinstance(exc, BadPassword):
        return LoginProblem.BAD_CREDENTIALS
    if code_missing:
        return LoginProblem.CODE_TIMEOUT
    if isinstance(exc, TwoFactorRequired):
        # Автоматический вход не может спросить код — нужен /iglogin. Если код уже вводили
        # через /iglogin и Instagram его не принял — это «неверный код / другой способ».
        return LoginProblem.TWO_FACTOR if interactive else LoginProblem.CHECKPOINT
    if isinstance(exc, ChallengeError) or "challenge" in text or "checkpoint" in text:
        return LoginProblem.CHECKPOINT
    if isinstance(exc, PleaseWaitFewMinutes) or "429" in text or "wait a few minutes" in text:
        return LoginProblem.BLOCKED
    return LoginProblem.OTHER


CodeProvider = Callable[[str], "str | None"]


def login_to_cookies_file(username: str, password: str, target: Path,
                          code_provider: CodeProvider | None = None) -> Path:
    """Блокирующий вход через instagrapi; результат — cookies.txt (права 600).

    code_provider(канал) вызывается, если Instagram просит код («email» или «sms»);
    возвращает код или None. Без code_provider вход с проверкой кодом не удаётся.
    """
    from instagrapi import Client
    from instagrapi.mixins.challenge import ChallengeChoice

    from .config import instagram_cookies_to_netscape

    client = Client()
    device_file = target.with_name("instagram_device.json")
    if device_file.is_file():
        # Тот же «телефон», что и при прошлом входе: Instagram реже просит подтверждение.
        with contextlib.suppress(Exception):
            client.load_settings(device_file)
    code_missing = {"value": False}  # код спрашивали у владельца, но он не пришёл

    def handler(_username: str, choice) -> str:
        if code_provider is None:
            return ""
        channel = "sms" if choice == ChallengeChoice.SMS else "email"
        code = (code_provider(channel) or "").strip()
        code_missing["value"] = not code
        return code

    client.challenge_code_handler = handler
    from instagrapi.exceptions import TwoFactorRequired

    try:
        # instagrapi печатает введённый код в stdout — не пускаем его в логи.
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                client.login(username, password)
            except TwoFactorRequired:
                # Новый вход Instagram (CAA) просит «двухэтапную проверку»: instagrapi не вызывает
                # challenge_code_handler, а ждёт код вторым вызовом login(verification_code=…).
                if code_provider is None:
                    raise
                log.info("Instagram asked for a verification code (two-step verification)")
                code = (code_provider("2fa") or "").strip()
                if not code:
                    code_missing["value"] = True
                    raise
                client.login(username, password, verification_code=code)
        sessionid = client.sessionid
    except Exception as exc:  # noqa: BLE001 — разбираем ниже
        problem = _classify(exc, code_missing["value"], code_provider is not None)
        # Точная причина — в лог (без пароля и кода), чтобы было видно, что ответил Instagram.
        log.warning("Instagram login error %s: %s", type(exc).__name__, _short(exc))
        raise InstagramLoginError(problem, type(exc).__name__) from exc
    if not sessionid:
        raise InstagramLoginError(LoginProblem.OTHER, "no sessionid after login")

    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write_private(target, instagram_cookies_to_netscape(f"sessionid={sessionid}"))
    with contextlib.suppress(Exception):
        client.dump_settings(device_file)
        os.chmod(device_file, 0o600)
    return target


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def sessionid_from_cookies(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) == 7 and fields[5] == "sessionid":
            return fields[6]
    return None


AskCode = Callable[[str], Awaitable["str | None"]]


class InstagramAuth:
    def __init__(self, username: str, password: str, work_dir: Path, clock=time.monotonic,
                 login_func=login_to_cookies_file) -> None:
        self._username = username
        self._password = password
        self.path = work_dir / "secrets" / "instagram_login_cookies.txt"
        self._clock = clock
        self._login = login_func
        self._lock = asyncio.Lock()
        self._last_attempt: float | None = None
        self._last_error: InstagramLoginError | None = None
        self._ready = False

    @property
    def current_path(self) -> Path | None:
        return self.path if self._ready else None

    async def ensure(self) -> Path:
        """Путь к cookies.txt с действующей сессией (автоматический вход без кода)."""
        async with self._lock:
            if not self._ready:
                await self._login_locked(None)
            return self.path

    async def relogin(self) -> bool:
        """Сессия устарела: войти заново. False — входили недавно, повторять бессмысленно."""
        async with self._lock:
            if self._last_attempt is not None and self._clock() - self._last_attempt < LOGIN_COOLDOWN:
                return False
            self._ready = False
            await self._login_locked(None)
            return True

    async def interactive_login(self, ask_code: AskCode) -> str:
        """Вход по команде владельца: если Instagram попросит код, спросить его через ask_code.

        Возвращает sessionid (владелец может сохранить его в INSTAGRAM_COOKIES)."""
        loop = asyncio.get_running_loop()

        def provider(channel: str) -> str | None:
            future = asyncio.run_coroutine_threadsafe(ask_code(channel), loop)
            try:
                return future.result(timeout=CODE_WAIT_SECONDS + 30)
            except Exception:  # noqa: BLE001 — нет кода: instagrapi сообщит об ошибке
                future.cancel()
                return None

        async with self._lock:
            self._ready = False
            await self._login_locked(provider, respect_cooldown=False)
            return sessionid_from_cookies(self.path) or ""

    async def _login_locked(self, provider: CodeProvider | None, *, respect_cooldown: bool = True) -> None:
        now = self._clock()
        if (respect_cooldown and self._last_attempt is not None
                and now - self._last_attempt < LOGIN_COOLDOWN and self._last_error is not None):
            raise self._last_error
        self._last_attempt = now
        self._ready = False
        try:
            await asyncio.to_thread(self._login, self._username, self._password, self.path, provider)
        except InstagramLoginError as exc:
            self._last_error = exc
            log.warning("Instagram login failed (%s): %s", exc.problem.value, OWNER_HINTS[exc.problem])
            raise
        self._last_error = None
        self._ready = True
        log.info("Instagram login OK, session saved for stories")
