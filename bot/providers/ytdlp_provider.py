"""Загрузчик на основе yt-dlp (https://github.com/yt-dlp/yt-dlp).

Что проверено по исходному коду yt-dlp 2026.8.19:
* TikTok: экстрактор TikTokIE принимает только www.tiktok.com/@user/video/<id>
  (короткие ссылки мы раскрываем сами). Форматы с водяным знаком yt-dlp помечает
  в format_note словом «watermarked» (форматы download / download_addr).
  Отдельного экстрактора для TikTok Stories в yt-dlp нет: сторис по адресу
  /video/<id> обрабатывается тем же TikTokIE, и работает ли это — зависит от того,
  что вернёт TikTok. Поэтому бот не обещает поддержку сторис.
* TikTok отдаёт отдельную дорожку «audio» (музыка) — её нельзя склеивать с видео,
  поэтому для TikTok выбираются только форматы, где уже есть и видео, и звук.
* Instagram: карусель возвращается как playlist; элементы-фото не содержат
  видеоформатов и пропускаются. DASH-потоки склеиваются без перекодирования.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from .. import media
from ..links import ParsedLink, Platform
from ..redact import redact
from .base import DownloadedVideo, DownloadResult, ErrorCode, ProviderError, Watermark

log = logging.getLogger(__name__)

ALLOWED_EXTRACTORS = [r"tiktok", r"instagram"]
_TOO_LARGE_MARK = "__too_large__"
_CANCEL_MARK = "__cancelled__"


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
    (ErrorCode.NO_VIDEO, ("no video formats", "requested format is not available",
                          "no video", "there is no video")),
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
        max_items: int = 10,
        allow_watermark_fallback: bool = True,
        cookies_file: Path | None = None,
        proxy: str | None = None,
        socket_timeout: int = 20,
        ydl_class: type = yt_dlp.YoutubeDL,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_items = max_items
        self.allow_watermark_fallback = allow_watermark_fallback
        self.cookies_file = cookies_file
        self.proxy = proxy
        self.socket_timeout = socket_timeout
        self._ydl_class = ydl_class

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
            "nopart": False,
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
                info = self._extract(ydl, link.url)
                entries, result.truncated = self._entries(info)
                for index, entry in enumerate(entries, start=1):
                    if cancel.is_set():
                        raise ProviderError(ErrorCode.TIMEOUT)
                    if not _has_video_formats(entry):
                        result.skipped_non_video += 1
                        continue
                    video = self._download_entry(ydl, info, entry, index, workdir, link.platform)
                    if isinstance(video, DownloadedVideo):
                        result.videos.append(video)
                    else:
                        result.skipped_too_large.append(video)
        except ProviderError:
            raise
        except DownloadCancelled as exc:
            raise _cancel_error(str(exc)) from exc
        except yt_dlp.utils.YoutubeDLError as exc:
            if _TOO_LARGE_MARK in str(exc) or _CANCEL_MARK in str(exc):
                raise _cancel_error(str(exc)) from exc
            code = classify_error(str(exc))
            if (code is ErrorCode.NO_VIDEO and link.platform is Platform.TIKTOK
                    and not self.allow_watermark_fallback
                    and "requested format is not available" in str(exc).lower()):
                code = ErrorCode.NO_CLEAN_VERSION
            log.info("yt-dlp error (%s): %s", code.value, redact(str(exc)))
            raise ProviderError(code, redact(str(exc))) from exc

        if not result.videos:
            if result.skipped_too_large:
                raise ProviderError(ErrorCode.TOO_LARGE, size=result.skipped_too_large[0])
            raise ProviderError(ErrorCode.NO_VIDEO)
        return result

    @staticmethod
    def _extract(ydl: Any, url: str) -> dict:
        info = ydl.extract_info(url, download=False, process=False)
        for _ in range(3):  # ссылка-перенаправление на другой экстрактор
            if not info or info.get("_type") not in ("url", "url_transparent"):
                break
            info = ydl.extract_info(info["url"], download=False, process=False)
        if not info:
            raise ProviderError(ErrorCode.NOT_FOUND)
        return info

    def _entries(self, info: dict) -> tuple[list[dict], bool]:
        if info.get("_type") != "playlist":
            return [info], False
        entries: list[dict] = []
        for entry in info.get("entries") or []:
            if len(entries) >= self.max_items:
                return entries, True
            if isinstance(entry, dict):
                entries.append(entry)
        return entries, False

    def _download_entry(
        self, ydl: Any, parent: dict, entry: dict, index: int, workdir: Path, platform: Platform,
    ) -> DownloadedVideo | int | None:
        """DownloadedVideo — успех; int/None — файл больше лимита (размер, если известен)."""
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
            # Например, фото-слайдшоу TikTok: yt-dlp отдаёт только звуковую дорожку
            path.unlink(missing_ok=True)
            raise ProviderError(ErrorCode.NO_VIDEO, "no video stream")

        return DownloadedVideo(
            path=path,
            size=size,
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


def _cancel_error(message: str) -> ProviderError:
    if _TOO_LARGE_MARK in message:
        return ProviderError(ErrorCode.TOO_LARGE)
    return ProviderError(ErrorCode.TIMEOUT)
