"""Вход в служебный аккаунт Instagram по логину и паролю владельца бота (для сторис).

Используется, когда cookies нельзя выгрузить вручную (например, с iPhone Safari
скрывает HttpOnly-cookie sessionid). Логин и пароль задаёт владелец бота в переменных
окружения; у пользователей бот пароли не спрашивает.

Вход выполняет instaloader (https://github.com/instaloader/instaloader, 4.15.4):
Instaloader.login() получает сессию requests с cookie sessionid, которую мы
сохраняем в cookies.txt для yt-dlp. Вход делается лениво — при первой ссылке на
сторис, — повторно только когда сессия устарела, и не чаще одного раза в
LOGIN_COOLDOWN секунд, чтобы частые входы с сервера не привели к блокировке.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

LOGIN_COOLDOWN = 600


class LoginProblem(str, Enum):
    BAD_CREDENTIALS = "bad_credentials"
    TWO_FACTOR = "two_factor"
    CHECKPOINT = "checkpoint"
    BLOCKED = "blocked"
    OTHER = "other"


# Подсказки владельцу бота — пишутся в лог Render.
OWNER_HINTS = {
    LoginProblem.BAD_CREDENTIALS: "неверный логин или пароль в INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD",
    LoginProblem.TWO_FACTOR: "на служебном аккаунте включена двухфакторная аутентификация — отключите её",
    LoginProblem.CHECKPOINT: ("Instagram просит подтвердить вход: откройте приложение Instagram в служебном "
                              "аккаунте и нажмите «Это был я», затем пришлите боту ссылку на сторис снова "
                              f"(не раньше чем через {LOGIN_COOLDOWN // 60} мин)"),
    LoginProblem.BLOCKED: "Instagram временно ограничил вход с IP сервера — попробуйте позже",
    LoginProblem.OTHER: "вход не удался, подробности выше в логе",
}


class InstagramLoginError(Exception):
    def __init__(self, problem: LoginProblem, detail: str = "") -> None:
        super().__init__(f"{problem.value}: {detail}" if detail else problem.value)
        self.problem = problem


def _classify(exc: Exception) -> LoginProblem:
    from instaloader.exceptions import (BadCredentialsException, ConnectionException,
                                        TooManyRequestsException, TwoFactorAuthRequiredException)
    text = str(exc).lower()
    if isinstance(exc, BadCredentialsException):
        return LoginProblem.BAD_CREDENTIALS
    if isinstance(exc, TwoFactorAuthRequiredException):
        return LoginProblem.TWO_FACTOR
    if "checkpoint" in text or "challenge" in text:
        return LoginProblem.CHECKPOINT
    if isinstance(exc, TooManyRequestsException) or "blocked ip" in text or "429" in text:
        return LoginProblem.BLOCKED
    if "does not exist" in text:
        return LoginProblem.BAD_CREDENTIALS
    if isinstance(exc, ConnectionException):
        return LoginProblem.OTHER
    return LoginProblem.OTHER


def login_to_cookies_file(username: str, password: str, target: Path) -> Path:
    """Блокирующий вход через instaloader; результат — cookies.txt (права 600)."""
    import instaloader

    loader = instaloader.Instaloader(quiet=True, max_connection_attempts=1, request_timeout=30,
                                     download_pictures=False, download_videos=False,
                                     save_metadata=False, compress_json=False)
    try:
        loader.login(username, password)
        # Сессия хранится во внутреннем поле контекста — так её сохраняет и сам
        # instaloader (InstaloaderContext.save_session берёт self._session.cookies).
        cookies = loader.context._session.cookies  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 — разбираем ниже
        raise InstagramLoginError(_classify(exc), type(exc).__name__) from exc
    finally:
        try:
            loader.close()
        except Exception:  # noqa: BLE001
            pass

    expires = int(time.time()) + 365 * 24 * 3600
    lines = ["# Netscape HTTP Cookie File"]
    names = set()
    for cookie in cookies:
        if "instagram.com" not in (cookie.domain or "") or not cookie.value:
            continue
        if any(c in cookie.value for c in "\t\r\n"):
            continue
        names.add(cookie.name)
        lines.append(f".instagram.com\tTRUE\t/\tTRUE\t{cookie.expires or expires}\t{cookie.name}\t{cookie.value}")
    if "sessionid" not in names:
        raise InstagramLoginError(LoginProblem.OTHER, "no sessionid after login")

    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return target


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
        """Путь к cookies.txt с действующей сессией. Бросает InstagramLoginError."""
        async with self._lock:
            if not self._ready:
                await self._login_locked()
            return self.path

    async def relogin(self) -> bool:
        """Сессия устарела: войти заново. False — входили недавно, повторять бессмысленно."""
        async with self._lock:
            if self._last_attempt is not None and self._clock() - self._last_attempt < LOGIN_COOLDOWN:
                return False
            await self._login_locked()
            return True

    async def _login_locked(self) -> None:
        now = self._clock()
        if self._last_attempt is not None and now - self._last_attempt < LOGIN_COOLDOWN:
            if self._last_error is not None:
                raise self._last_error
        self._last_attempt = now
        self._ready = False
        try:
            await asyncio.to_thread(self._login, self._username, self._password, self.path)
        except InstagramLoginError as exc:
            self._last_error = exc
            log.warning("Instagram login failed (%s): %s", exc.problem.value, OWNER_HINTS[exc.problem])
            raise
        self._last_error = None
        self._ready = True
        log.info("Instagram login OK, session saved for stories")
