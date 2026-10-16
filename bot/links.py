"""Поиск ссылки в сообщении, проверка домена и определение типа контента.

Важно: путь TikTok вида /@user/video/<id> НЕ означает, что это обычное видео —
по такому же адресу открываются и сторис, и фото-посты. Поэтому тип TIKTOK_POST
означает «публикация TikTok», а что в ней — выясняет загрузчик.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
import base64
import binascii
from urllib.parse import parse_qs, urlsplit, urlunsplit


class Platform(str, Enum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"

    @property
    def title(self) -> str:
        return "TikTok" if self is Platform.TIKTOK else "Instagram"


class LinkKind(str, Enum):
    TIKTOK_SHORT = "tiktok_short"  # vm./vt.tiktok.com, tiktok.com/t/… — нужен редирект
    TIKTOK_POST = "tiktok_post"  # видео, фото-пост или сторис — заранее не известно
    INSTAGRAM_SHARE = "instagram_share"  # instagram.com/share/… — нужен редирект
    INSTAGRAM_POST = "instagram_post"  # /p/, /tv/
    INSTAGRAM_REEL = "instagram_reel"  # /reel/, /reels/
    INSTAGRAM_STORY = "instagram_story"


class LinkErrorCode(str, Enum):
    NO_URL = "no_url"
    BAD_SCHEME = "bad_scheme"
    BAD_HOST = "bad_host"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    CREDENTIALS = "credentials"
    BAD_PORT = "bad_port"
    UNSUPPORTED_CONTENT = "unsupported_content"
    TOO_LONG = "too_long"


class LinkError(Exception):
    def __init__(self, code: LinkErrorCode, detail: str = "") -> None:
        super().__init__(f"{code.value}: {detail}" if detail else code.value)
        self.code = code


@dataclass(frozen=True)
class ParsedLink:
    platform: Platform
    kind: LinkKind
    url: str  # нормализованный адрес для загрузчика
    original: str  # адрес, который прислал пользователь (для кнопки «Открыть источник»)
    content_id: str | None = None

    @property
    def needs_resolve(self) -> bool:
        return self.kind in (LinkKind.TIKTOK_SHORT, LinkKind.INSTAGRAM_SHARE)


TIKTOK_DOMAIN = "tiktok.com"
INSTAGRAM_DOMAIN = "instagram.com"
MAX_URL_LENGTH = 2048

_URL_RE = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)*(?:tiktok\.com|instagram\.com)(?::\d+)?(?:/[^\s<>\"']*)?"
    r"|https?://[^\s<>\"']+",
    re.IGNORECASE,
)

_TT_VIDEO = re.compile(r"^/@(?P<user>[\w.-]*)/(?P<kind>video|photo)/(?P<id>\d+)/?$")
_TT_MOBILE_V = re.compile(r"^/v/(?P<id>\d+)(?:\.html)?/?$")
_TT_SHARE = re.compile(r"^/(?:share/video|embed(?:/v2)?)/(?P<id>\d+)/?$")
_TT_SHORT_PATH = re.compile(r"^/t/(?P<id>[\w-]+)/?$")
_TT_SHORT_HOST_PATH = re.compile(r"^/(?P<id>[\w-]+)/?$")

_IG_MEDIA = re.compile(r"^(?:/[\w.]+)?/(?P<kind>p|tv|reels?)/(?P<id>[\w-]+)/?$")
_IG_SHARE = re.compile(r"^/share/(?:(?:reel|p)/)?[\w-]+/?$")
_IG_STORY = re.compile(r"^/stories/(?P<user>[\w.]+)(?:/(?P<id>\d+))?/?$")
_IG_STORY_SHORT = re.compile(r"^/s/(?P<code>[A-Za-z0-9_=-]+)/?$")


def extract_url(text: str | None) -> str | None:
    """Первая ссылка из текста. Ссылку без схемы дополняем https://."""
    if not text:
        return None
    match = _URL_RE.search(text)
    if not match:
        return None
    url = match.group(0).rstrip(".,;:!?)]}»\"'")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _clean_host(netloc_host: str | None) -> str:
    host = (netloc_host or "").strip().lower().rstrip(".")
    if not host or not host.isascii() or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise LinkError(LinkErrorCode.BAD_HOST, host)
    return host


def parse_link(url: str, original: str | None = None) -> ParsedLink:
    """Проверяет ссылку и определяет платформу/тип. Бросает LinkError."""
    if not url:
        raise LinkError(LinkErrorCode.NO_URL)
    if len(url) > MAX_URL_LENGTH:
        raise LinkError(LinkErrorCode.TOO_LONG)
    original = original or url

    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise LinkError(LinkErrorCode.BAD_HOST, str(exc)) from exc

    if parts.scheme.lower() not in ("http", "https"):
        raise LinkError(LinkErrorCode.BAD_SCHEME, parts.scheme)
    if parts.username is not None or parts.password is not None:
        raise LinkError(LinkErrorCode.CREDENTIALS)
    if port not in (None, 80, 443):
        raise LinkError(LinkErrorCode.BAD_PORT, str(port))

    host = _clean_host(parts.hostname)
    path = re.sub(r"/{2,}", "/", parts.path or "/")

    if _host_matches(host, TIKTOK_DOMAIN):
        return _parse_tiktok(host, path, parts.query, original)
    if _host_matches(host, INSTAGRAM_DOMAIN):
        return _parse_instagram(path, parts.query, original)
    raise LinkError(LinkErrorCode.DOMAIN_NOT_ALLOWED, host)


def _parse_tiktok(host: str, path: str, query: str, original: str) -> ParsedLink:
    if host in ("vm.tiktok.com", "vt.tiktok.com"):
        m = _TT_SHORT_HOST_PATH.match(path)
        if not m:
            raise LinkError(LinkErrorCode.UNSUPPORTED_CONTENT, path)
        url = urlunsplit(("https", host, path, "", ""))
        return ParsedLink(Platform.TIKTOK, LinkKind.TIKTOK_SHORT, url, original, m.group("id"))

    if m := _TT_SHORT_PATH.match(path):
        url = urlunsplit(("https", "www.tiktok.com", path, "", ""))
        return ParsedLink(Platform.TIKTOK, LinkKind.TIKTOK_SHORT, url, original, m.group("id"))

    if m := _TT_VIDEO.match(path):
        user, vid = m.group("user"), m.group("id")
        # yt-dlp понимает только www.tiktok.com/@user/video/<id>; фото-пост TikTok открывается
        # по тому же адресу с /video/. Параметры запроса загрузчику не нужны.
        url = f"https://www.tiktok.com/@{user}/video/{vid}"
        return ParsedLink(Platform.TIKTOK, LinkKind.TIKTOK_POST, url, original, vid)

    for pattern in (_TT_MOBILE_V, _TT_SHARE):
        if m := pattern.match(path):
            vid = m.group("id")
            url = f"https://www.tiktok.com/@/video/{vid}"
            return ParsedLink(Platform.TIKTOK, LinkKind.TIKTOK_POST, url, original, vid)

    raise LinkError(LinkErrorCode.UNSUPPORTED_CONTENT, path)


def _story(user: str, story_id: str | None, original: str) -> ParsedLink:
    if user == "highlights" and not story_id:
        raise LinkError(LinkErrorCode.UNSUPPORTED_CONTENT, "highlight without id")
    path = f"/stories/{user}/{story_id}/" if story_id else f"/stories/{user}/"
    return ParsedLink(Platform.INSTAGRAM, LinkKind.INSTAGRAM_STORY,
                      f"https://www.instagram.com{path}", original,
                      None if user == "highlights" else story_id)


def _parse_instagram(path: str, query: str, original: str) -> ParsedLink:
    if path.startswith("/stories/"):
        m = _IG_STORY.match(path)
        if not m:
            raise LinkError(LinkErrorCode.UNSUPPORTED_CONTENT, path)
        # /stories/<user>/<id>/ — одна сторис; /stories/<user>/ — все текущие сторис;
        # /stories/highlights/<id>/ — подборка «Актуальное».
        return _story(m.group("user"), m.group("id"), original)

    if m := _IG_STORY_SHORT.match(path):
        # Ссылка «Поделиться» на «Актуальное»: /s/<base64("highlight:<id>")>?story_media_id=<pk>_<user>
        code = m.group("code")
        try:
            decoded = base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)).decode("ascii")
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise LinkError(LinkErrorCode.UNSUPPORTED_CONTENT, "bad /s/ code") from exc
        hm = re.fullmatch(r"highlight:(\d+)", decoded)
        if not hm:
            raise LinkError(LinkErrorCode.UNSUPPORTED_CONTENT, "unknown /s/ link")
        link = _story("highlights", hm.group(1), original)
        media = (parse_qs(query).get("story_media_id") or [""])[0].split("_")[0]
        if media.isdigit():
            link = ParsedLink(link.platform, link.kind, link.url, original, media)
        return link

    if _IG_SHARE.match(path):
        url = urlunsplit(("https", "www.instagram.com", path, "", ""))
        return ParsedLink(Platform.INSTAGRAM, LinkKind.INSTAGRAM_SHARE, url, original)

    if m := _IG_MEDIA.match(path):
        kind, sc = m.group("kind"), m.group("id")
        if kind in ("reel", "reels"):
            url = f"https://www.instagram.com/reel/{sc}/"
            return ParsedLink(Platform.INSTAGRAM, LinkKind.INSTAGRAM_REEL, url, original, sc)
        url = f"https://www.instagram.com/{kind}/{sc}/"
        return ParsedLink(Platform.INSTAGRAM, LinkKind.INSTAGRAM_POST, url, original, sc)

    raise LinkError(LinkErrorCode.UNSUPPORTED_CONTENT, path)


def is_allowed_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return _host_matches(host, TIKTOK_DOMAIN) or _host_matches(host, INSTAGRAM_DOMAIN)
