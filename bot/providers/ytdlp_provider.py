"""Загрузчик на основе yt-dlp (https://github.com/yt-dlp/yt-dlp).

Что проверено по исходному коду yt-dlp 2026.8.19:
* TikTok: экстрактор TikTokIE принимает только www.tiktok.com/@user/video/<id>
  (короткие ссылки мы раскрываем сами, /photo/ приводим к /video/). Форматы с
  водяным знаком yt-dlp помечает в format_note словом «watermarked».
  Отдельного экстрактора для TikTok Stories в yt-dlp нет: сторис по адресу
  /video/<id> обрабатывается тем же TikTokIE.
* TikTok фото-посты (слайдшоу): yt-dlp отдаёт только звуковую дорожку, сами фото
  он не извлекает. Фото лежат в данных страницы: itemStruct.imagePost.images[]
  .imageURL.urlList (те же поля использует gallery-dl). TikTokIE получает эти данные
  методом _extract_web_data_and_status — мы перехватываем его результат, чтобы не
  делать второй запрос к TikTok.
* TikTok отдаёт отдельную дорожку «audio» (музыка) — её нельзя склеивать с видео,
  поэтому для TikTok выбираются только форматы, где уже есть и видео, и звук.
* Instagram: фото в публикации и карусели приходят без форматов, но с
  thumbnails (image_versions2.candidates) — берём самый большой вариант.
  Для поста только с фото yt-dlp бросает «There is no video in this post», если
  не задан ignore_no_formats_error — включаем его только на время извлечения.
"""

from __future__ import annotations

import logging
import shutil
import socket
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.utils import DownloadCancelled

from .. import media
from ..links import ParsedLink, Platform
from ..net_guard import is_public_ip
from ..redact import redact
from .base import (DownloadedMedia, DownloadResult, ErrorCode, MediaKind, ProviderError,
                   Watermark)

log = logging.getLogger(__name__)

ALLOWED_EXTRACTORS = [r"tiktok", r"instagram"]
_TOO_LARGE_MARK = "__too_large__"
_CANCEL_MARK = "__cancelled__"
_CHUNK = 256 * 1024
_IMAGE_TYPES = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/heic": ".heic", "image/avif": ".avif",
}
_REFERERS = {Platform.TIKTOK: "https://www.tiktok.com/", Platform.INSTAGRAM: "https://www.instagram.com/"}


def build_format_spec(platform: Platform, limit_bytes: int, allow_watermark: bool) -> str:
    """Строка выбора формата yt-dlp: максимум качества, со звуком, в пределах лимита."""
    fit = f"[filesize<?{limit_bytes}][filesize_approx<?{limit_bytes}]"
    if platform is Platform.TIKTOK:
        clean = "[format_note!*=?watermarked][format_id!^=?download][format_note!*=?UNPLAYABLE]"
        tiers = [f"b{clean}{fit}"]
        if allow_watermark:
            tiers.append(f"b[format_note!*=?UNPLAYABLE]{fit}")
        tiers.append(f"b{clean}")
        if allow_watermark:
            tiers.append("b[format_note!*=?UNPLAYABLE]")
        return "/".join(tiers)
    # Instagram: лучший видеопоток + лучший звук (склейка без перекодирования),
    # либо лучший готовый файл со звуком.
    return f"bv*{fit}+ba/b{fit}/bv*+ba/b"


def classify_watermark(platform: Platform, chosen_formats: list[dict]) -> Watermark:
    if platform is not Platform.TIKTOK:
        return Watermark.NOT_APPLICABLE
    if not chosen_formats:
        return Watermark.UNKNOWN
    notes = " ".join(str(f.get("format_note") or "") for f in chosen_formats).lower()
    if "watermark" in notes:
        return Watermark.WATERMARKED
    if any(str(f.get("format_id") or "").startswith("download") for f in chosen_formats):
        return Watermark.UNKNOWN
    return Watermark.CLEAN


_ERROR_PATTERNS: list[tuple[ErrorCode, tuple[str, ...]]] = [
    (ErrorCode.BLOCKED, ("ip address is blocked", "http error 429", "too many requests",
                         "rate-limit", "rate limit", "captcha")),
    (ErrorCode.NO_MEDIA, ("no video formats", "requested format is not available",
                          "there is no video")),
    (ErrorCode.PRIVATE, ("private", "status code 10216", "status code 10222",
                         "do not have permission")),
    (ErrorCode.LOGIN_REQUIRED, ("login required", "requires login", "log in", "login",
                                "cookies", "requiring login", "authentication")),
    (ErrorCode.NOT_FOUND, ("not available", "unavailable", "removed", "deleted",
                           "http error 404", "does not exist", "no longer", "status code 10204")),
    (ErrorCode.UNSUPPORTED, ("unsupported url",)),
    (ErrorCode.TIMEOUT, ("timed out", "timeout")),
    (ErrorCode.NETWORK, ("connection", "network", "unable to download webpage",
                         "ssl", "name resolution", "temporary failure")),
]


def classify_error(message: str) -> ErrorCode:
    text = message.lower()
    # Порядок важен: первое совпадение побеждает.
    for code, needles in _ERROR_PATTERNS:
        if any(n in text for n in needles):
            return code
    return ErrorCode.UNKNOWN


def tiktok_image_urls(item: dict | None) -> list[dict]:
    """Фото TikTok-поста из itemStruct: [{'url', 'fallbacks', 'width', 'height'}] по порядку."""
    images = ((item or {}).get("imagePost") or {}).get("images") or []
    result = []
    for image in images:
        if not isinstance(image, dict):
            continue
        urls = [u for u in ((image.get("imageURL") or {}).get("urlList") or [])
                if isinstance(u, str) and u.startswith("https://")]
        if not urls:
            continue
        # Предпочитаем JPEG: его Telegram показывает как фото без преобразований.
        url = next((u for u in urls if "jpeg" in u.lower() or ".jpg" in u.lower()), urls[0])
        result.append({"url": url, "fallbacks": [u for u in urls if u != url],
                       "width": image.get("imageWidth"), "height": image.get("imageHeight")})
    return result


def best_instagram_image(entry: dict) -> dict | None:
    candidates = [t for t in entry.get("thumbnails") or []
                  if isinstance(t, dict) and isinstance(t.get("url"), str)
                  and t["url"].startswith("https://")]
    if not candidates:
        return None
    best = max(candidates, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
    return {"url": best["url"], "fallbacks": [], "width": best.get("width"), "height": best.get("height")}


def ensure_public_url(url: str) -> None:
    """Проверка адреса файла, который вернула площадка: https и только публичные IP."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ProviderError(ErrorCode.UNKNOWN, "unsafe media url")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProviderError(ErrorCode.NETWORK, "dns") from exc
    if not infos or any(not is_public_ip(i[4][0].split("%", 1)[0]) for i in infos):
        raise ProviderError(ErrorCode.UNKNOWN, "media url resolves to non-public address")


class _YtdlpLogger:
    """Перенаправляет вывод yt-dlp в logging, вырезая подписанные ссылки и cookies."""

    def debug(self, msg: str) -> None:
        log.debug(redact(msg))

    def info(self, msg: str) -> None:
        log.debug(redact(msg))

    def warning(self, msg: str) -> None:
        log.info("yt-dlp: %s", redact(msg))

    def error(self, msg: str) -> None:
        log.info("yt-dlp: %s", redact(msg))


class YtDlpProvider:
    name = "yt-dlp"

    def __init__(
        self,
        *,
        max_bytes: int,
        max_items: int = 20,
        allow_watermark_fallback: bool = True,
        cookies_file: Path | None = None,
        proxy: str | None = None,
        socket_timeout: int = 20,
        ydl_class: type = yt_dlp.YoutubeDL,
        url_checker=ensure_public_url,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_items = max_items
        self.allow_watermark_fallback = allow_watermark_fallback
        self.cookies_file = cookies_file
        self.proxy = proxy
        self.socket_timeout = socket_timeout
        self._ydl_class = ydl_class
        self._check_url = url_checker

    def supports(self, link: ParsedLink) -> bool:
        return link.platform in (Platform.TIKTOK, Platform.INSTAGRAM)

    def _options(self, link: ParsedLink, workdir: Path, cancel: threading.Event) -> dict[str, Any]:
        def hook(status: dict) -> None:
            if cancel.is_set():
                raise DownloadCancelled(_CANCEL_MARK)
            done = status.get("downloaded_bytes") or 0
            if done > self.max_bytes:
                raise DownloadCancelled(_TOO_LARGE_MARK)

        opts: dict[str, Any] = {
            "format": build_format_spec(link.platform, self.max_bytes, self.allow_watermark_fallback),
            "merge_output_format": "mp4",
            "outtmpl": {"default": str(workdir / "%(id).60B.%(ext)s")},
            "paths": {"home": str(workdir), "temp": str(workdir)},
            "restrictfilenames": True,
            "windowsfilenames": True,
            "overwrites": True,
            "continuedl": False,
            "cachedir": False,
            "quiet": True,
            "noprogress": True,
            "no_color": True,
            "logger": _YtdlpLogger(),
            "socket_timeout": self.socket_timeout,
            "retries": 2,
            "fragment_retries": 2,
            "extractor_retries": 1,
            "file_access_retries": 1,
            "max_filesize": self.max_bytes,
            "allowed_extractors": ALLOWED_EXTRACTORS,
            "progress_hooks": [hook],
            "writethumbnail": False,
            "writesubtitles": False,
            "check_formats": False,
        }
        if self.cookies_file:
            # yt-dlp перезаписывает cookie-файл при выходе — работаем с копией в папке задачи.
            copy = workdir / "cookies.txt"
            shutil.copyfile(self.cookies_file, copy)
            opts["cookiefile"] = str(copy)
        if self.proxy:
            opts["proxy"] = self.proxy
        return opts

    def download(self, link: ParsedLink, workdir: Path, cancel: threading.Event) -> DownloadResult:
        result = DownloadResult(platform=link.platform)
        try:
            with self._ydl_class(self._options(link, workdir, cancel)) as ydl:
                captured = self._capture_tiktok_item(ydl) if link.platform is Platform.TIKTOK else {}
                info = self._extract(ydl, link.url)
                self._collect(ydl, link, info, captured, workdir, cancel, result)
        except ProviderError:
            raise
        except DownloadCancelled as exc:
            raise _cancel_error(str(exc)) from exc
        except yt_dlp.utils.YoutubeDLError as exc:
            if _TOO_LARGE_MARK in str(exc) or _CANCEL_MARK in str(exc):
                raise _cancel_error(str(exc)) from exc
            code = classify_error(str(exc))
            if (code is ErrorCode.NO_MEDIA and link.platform is Platform.TIKTOK
                    and not self.allow_watermark_fallback
                    and "requested format is not available" in str(exc).lower()):
                code = ErrorCode.NO_CLEAN_VERSION
            log.info("yt-dlp error (%s): %s", code.value, redact(str(exc)))
            raise ProviderError(code, redact(str(exc))) from exc

        if not result.items:
            if result.skipped_too_large:
                raise ProviderError(ErrorCode.TOO_LARGE, size=result.skipped_too_large[0])
            raise ProviderError(ErrorCode.NO_MEDIA)
        return result

    def _collect(self, ydl: Any, link: ParsedLink, info: dict, captured: dict, workdir: Path,
                 cancel: threading.Event, result: DownloadResult) -> None:
        # Фото-пост TikTok (в том числе фото-сторис): берём фото из данных страницы.
        images = tiktok_image_urls(captured.get("item")) if link.platform is Platform.TIKTOK else []
        if images:
            for index, image in enumerate(images, start=1):
                if len(result.items) >= self.max_items:
                    result.truncated = True
                    return
                self._add_photo(ydl, link.platform, image, f"{link.content_id or 'photo'}_{index:02d}",
                                workdir, cancel, result)
            return

        entries = [info] if info.get("_type") != "playlist" else [
            e for e in info.get("entries") or [] if isinstance(e, dict)]
        for index, entry in enumerate(entries, start=1):
            if cancel.is_set():
                raise ProviderError(ErrorCode.TIMEOUT)
            if len(result.items) >= self.max_items:
                result.truncated = True
                return
            if _has_video_formats(entry):
                downloaded = self._download_entry(ydl, info, entry, index, workdir, link.platform)
                if isinstance(downloaded, DownloadedMedia):
                    result.items.append(downloaded)
                else:
                    result.skipped_too_large.append(downloaded)
                continue
            image = best_instagram_image(entry) if link.platform is Platform.INSTAGRAM else None
            if image:
                self._add_photo(ydl, link.platform, image, f"{entry.get('id') or 'photo'}_{index:02d}",
                                workdir, cancel, result)
            else:
                result.skipped_unsupported += 1

    @staticmethod
    def _capture_tiktok_item(ydl: Any) -> dict:
        """Перехватывает itemStruct, который TikTokIE и так скачивает, — без лишнего запроса."""
        captured: dict = {}
        try:
            ie = ydl.get_info_extractor("TikTok")
            original = ie._extract_web_data_and_status
        except Exception:  # noqa: BLE001 — другой загрузчик/версия: работаем без фото
            log.debug("TikTok item capture unavailable")
            return captured

        def capture(*args: Any, **kwargs: Any) -> Any:
            data, status = original(*args, **kwargs)
            if isinstance(data, dict) and data:
                captured["item"] = data
            return data, status

        ie._extract_web_data_and_status = capture
        return captured

    @staticmethod
    def _extract(ydl: Any, url: str) -> dict:
        # Пост только с фото: без этого флага Instagram-экстрактор бросает «no video».
        ydl.params["ignore_no_formats_error"] = True
        try:
            info = ydl.extract_info(url, download=False, process=False)
            for _ in range(3):  # ссылка-перенаправление на другой экстрактор
                if not info or info.get("_type") not in ("url", "url_transparent"):
                    break
                info = ydl.extract_info(info["url"], download=False, process=False)
        finally:
            ydl.params["ignore_no_formats_error"] = False
        if not info:
            raise ProviderError(ErrorCode.NOT_FOUND)
        return info

    def _add_photo(self, ydl: Any, platform: Platform, image: dict, stem: str, workdir: Path,
                   cancel: threading.Event, result: DownloadResult) -> None:
        last_error: ProviderError | None = None
        for url in [image["url"], *image.get("fallbacks", [])][:3]:
            try:
                photo = self._fetch_photo(ydl, platform, url, stem, workdir, cancel)
            except ProviderError as exc:
                if exc.code is ErrorCode.TIMEOUT:
                    raise
                last_error = exc
                continue
            if isinstance(photo, DownloadedMedia):
                photo.width = photo.width or image.get("width")
                photo.height = photo.height or image.get("height")
                result.items.append(photo)
            else:
                result.skipped_too_large.append(photo)
            return
        log.info("Photo download failed: %s", last_error)
        result.skipped_unsupported += 1

    def _fetch_photo(self, ydl: Any, platform: Platform, url: str, stem: str, workdir: Path,
                     cancel: threading.Event) -> DownloadedMedia | int:
        self._check_url(url)
        try:
            response = ydl.urlopen(Request(url, headers={"Referer": _REFERERS[platform]}))
        except yt_dlp.utils.YoutubeDLError as exc:
            raise ProviderError(classify_error(str(exc)), redact(str(exc))) from exc
        try:
            self._check_url(response.url or url)  # адрес после редиректов
            ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = _IMAGE_TYPES.get(ctype)
            if ext is None:
                raise ProviderError(ErrorCode.UNKNOWN, f"not an image: {ctype or 'unknown'}")
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > self.max_bytes:
                return declared
            path = workdir / f"{_safe_stem(stem)}{ext}"
            total = 0
            with open(path, "wb") as fh:
                while chunk := response.read(_CHUNK):
                    if cancel.is_set():
                        raise ProviderError(ErrorCode.TIMEOUT)
                    total += len(chunk)
                    if total > self.max_bytes:
                        break
                    fh.write(chunk)
            if total > self.max_bytes:
                path.unlink(missing_ok=True)
                return total
            if total == 0:
                path.unlink(missing_ok=True)
                raise ProviderError(ErrorCode.UNKNOWN, "empty image")
        finally:
            response.close()

        info = media.probe(path)
        return DownloadedMedia(
            path=path, size=path.stat().st_size, kind=MediaKind.PHOTO,
            width=info.width if info else None, height=info.height if info else None,
            image_codec=(info.codec if info else None) or ext.lstrip("."),
        )

    def _download_entry(
        self, ydl: Any, parent: dict, entry: dict, index: int, workdir: Path, platform: Platform,
    ) -> DownloadedMedia | int | None:
        """DownloadedMedia — успех; int/None — файл больше лимита (размер, если известен)."""
        if entry is not parent:
            for key in ("extractor", "extractor_key", "webpage_url", "webpage_url_basename",
                        "webpage_url_domain", "original_url"):
                if parent.get(key) is not None:
                    entry.setdefault(key, parent[key])
            entry.setdefault("id", f"{parent.get('id', 'item')}_{index}")
            entry.setdefault("title", parent.get("title") or entry["id"])

        try:
            processed = ydl.process_ie_result(entry, download=True)
        except DownloadCancelled as exc:
            if _TOO_LARGE_MARK in str(exc):
                return None
            raise
        except yt_dlp.utils.YoutubeDLError as exc:
            if _TOO_LARGE_MARK in str(exc):
                return None
            raise

        chosen = processed.get("requested_formats") or [processed]
        expected = sum(int(f.get("filesize") or f.get("filesize_approx") or 0) for f in chosen) or None
        downloads = processed.get("requested_downloads") or []
        filepath = downloads[0].get("filepath") if downloads else None
        path = Path(filepath) if filepath else None
        if path is None or not path.is_file():
            # yt-dlp прерывает загрузку, если файл больше max_filesize, и файла нет
            if expected and expected > self.max_bytes:
                return expected
            raise ProviderError(ErrorCode.UNKNOWN, "downloaded file is missing")

        path = path.resolve()
        if workdir.resolve() not in path.parents:
            raise ProviderError(ErrorCode.UNKNOWN, "file outside of workdir")

        size = path.stat().st_size
        if size > self.max_bytes:
            return size

        info = media.probe(path)
        if info is not None and not info.has_video:
            # Только звук (например, музыка слайдшоу) — это не видео
            path.unlink(missing_ok=True)
            raise ProviderError(ErrorCode.NO_MEDIA, "no video stream")

        return DownloadedMedia(
            path=path,
            size=size,
            kind=MediaKind.VIDEO,
            width=(info.width if info else None) or processed.get("width"),
            height=(info.height if info else None) or processed.get("height"),
            duration=(info.duration if info else None) or processed.get("duration"),
            thumbnail=media.make_thumbnail(path, path.with_suffix(".thumb.jpg")),
            watermark=classify_watermark(platform, chosen),
            title=processed.get("title"),
        )


def _has_video_formats(entry: dict) -> bool:
    formats = entry.get("formats")
    if formats is None:
        return bool(entry.get("url")) and entry.get("vcodec") != "none"
    return any(f.get("vcodec") != "none" for f in formats if isinstance(f, dict))


def _safe_stem(stem: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:60] or "photo"


def _cancel_error(message: str) -> ProviderError:
    if _TOO_LARGE_MARK in message:
        return ProviderError(ErrorCode.TOO_LARGE)
    return ProviderError(ErrorCode.TIMEOUT)
