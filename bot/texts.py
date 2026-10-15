"""Все тексты интерфейса (русский)."""

from __future__ import annotations

from html import escape

from .links import LinkErrorCode, Platform
from .media import human_size
from .providers.base import DownloadedVideo, ErrorCode, Watermark

START = (
    "👋 Привет! Отправь ссылку на видео из TikTok или Instagram.\n\n"
    "🎬 Reels и видео\n"
    "✨ TikTok без водяного знака — когда доступна чистая версия\n"
    "📱 TikTok Stories — если они доступны для скачивания\n"
    "📁 Отправка исходного файла"
)

HELP = (
    "<b>Как пользоваться</b>\n\n"
    "1. В TikTok или Instagram нажми «Поделиться» → «Копировать ссылку».\n"
    "2. Пришли ссылку сюда сообщением.\n"
    "3. Получи видео. Кнопка «📁 Скачать оригинальным файлом» пришлёт тот же файл "
    "документом — без сжатия Telegram.\n\n"
    "<b>Поддерживается</b>\n"
    "• TikTok: видео (полные и короткие ссылки vm.tiktok.com), сторис — пока она "
    "доступна и если TikTok отдаёт её загрузчику\n"
    "• Instagram: Reels и видео из публичных публикаций, включая карусели\n\n"
    "<b>Не поддерживается</b>\n"
    "• закрытые аккаунты и удалённые публикации\n"
    "• сторис Instagram\n\n"
    "<b>Ограничения</b>\n"
    "• размер файла — до {limit} (ограничение Telegram для ботов)\n"
    "• оригинальный файл хранится {ttl} мин после загрузки\n\n"
    "Бот никогда не спрашивает пароли от соцсетей."
)

CHECKING = "🔎 Проверяю ссылку…"
QUEUED = "⏳ Сейчас много загрузок, ты в очереди…"
DOWNLOADING = "⬇️ Скачиваю видео…"
SENDING = "📤 Отправляю…"

NO_LINK = "🤔 Не вижу ссылки. Отправь ссылку на видео из TikTok или Instagram."
BUSY = "⏳ Подожди, пока закончится предыдущая загрузка, и пришли ссылку снова."

BTN_DOCUMENT = "📁 Скачать оригинальным файлом"
BTN_SOURCE = "🔗 Открыть источник"
BTN_HELP = "❓ Как пользоваться"

DOC_EXPIRED = "Файл больше не хранится. Пришли ссылку ещё раз."
DOC_SENDING = "Отправляю файл…"
DOC_TOO_LARGE = "Файл слишком большой для отправки документом."
DOC_FAILED = "Не получилось отправить файл. Попробуй ещё раз позже."

LINK_ERRORS: dict[LinkErrorCode, str] = {
    LinkErrorCode.NO_URL: NO_LINK,
    LinkErrorCode.BAD_SCHEME: "⚠️ Поддерживаются только обычные ссылки http(s).",
    LinkErrorCode.BAD_HOST: "⚠️ Ссылка выглядит некорректно.",
    LinkErrorCode.DOMAIN_NOT_ALLOWED: "⚠️ Я работаю только со ссылками TikTok и Instagram.",
    LinkErrorCode.CREDENTIALS: "⚠️ Такая ссылка не поддерживается.",
    LinkErrorCode.BAD_PORT: "⚠️ Такая ссылка не поддерживается.",
    LinkErrorCode.TOO_LONG: "⚠️ Ссылка слишком длинная.",
    LinkErrorCode.UNSUPPORTED_CONTENT: (
        "⚠️ Это не ссылка на конкретное видео. Пришли ссылку на ролик, Reels или публикацию."
    ),
    LinkErrorCode.INSTAGRAM_STORY: (
        "😔 Сторис Instagram не поддерживаются: для них нужен вход в аккаунт."
    ),
    LinkErrorCode.TIKTOK_PHOTO: "🖼 Это фото-публикация TikTok — в ней нет видео.",
}

RESOLVE_ERRORS = {
    "network": "🌐 Не удалось открыть ссылку. Попробуй ещё раз чуть позже.",
    "dead_link": "😔 Ссылка недействительна или публикация удалена.",
    "too_many_redirects": "⚠️ Ссылка ведёт по слишком длинной цепочке переадресаций.",
}

UNSAFE_ADDRESS = "⚠️ Эта ссылка ведёт на недопустимый адрес."


def provider_error(code: ErrorCode, platform: Platform, limit: int, size: int | None = None) -> str:
    if code is ErrorCode.NOT_FOUND:
        if platform is Platform.TIKTOK:
            return ("😔 Видео недоступно: оно удалено, скрыто автором, или это сторис, "
                    "срок которой истёк либо которую TikTok не отдаёт для скачивания.")
        return "😔 Публикация не найдена или удалена."
    if code is ErrorCode.PRIVATE:
        return "🔒 Это закрытый аккаунт или публикация доступна не всем — скачать нельзя."
    if code is ErrorCode.LOGIN_REQUIRED:
        return (f"🔒 {platform.title} не отдаёт эту публикацию без входа в аккаунт. "
                "Возможно, она закрыта или ограничена по возрасту.")
    if code is ErrorCode.BLOCKED:
        return f"🚧 {platform.title} временно ограничил доступ серверу бота. Попробуй позже."
    if code is ErrorCode.NO_VIDEO:
        return "🖼 В этой публикации нет видео (например, только фото)."
    if code is ErrorCode.NO_CLEAN_VERSION:
        return "😔 Чистая версия без водяного знака для этого видео недоступна."
    if code is ErrorCode.TOO_LARGE:
        got = f" ({human_size(size)})" if size else ""
        return (f"📦 Видео слишком большое{got}. Telegram позволяет боту отправлять файлы "
                f"до {human_size(limit)}.")
    if code is ErrorCode.TIMEOUT:
        return "⏱ Загрузка заняла слишком много времени. Попробуй ещё раз позже."
    if code is ErrorCode.UNSUPPORTED:
        return "⚠️ Этот тип ссылки не поддерживается."
    if code is ErrorCode.NETWORK:
        return f"🌐 Не удалось связаться с {platform.title}. Попробуй ещё раз позже."
    return "😔 Не удалось скачать видео. Попробуй ещё раз позже."


SEND_FAILED = "😔 Видео скачалось, но Telegram не принял файл. Попробуй ещё раз позже."
UNEXPECTED = "😔 Что-то пошло не так. Попробуй ещё раз позже."


def video_caption(platform: Platform, video: DownloadedVideo, index: int, total: int) -> str:
    icon = "🎵" if platform is Platform.TIKTOK else "📸"
    parts = [f"{icon} <b>{platform.title}</b>"]
    if total > 1:
        parts.append(f"{index}/{total}")
    if video.width and video.height:
        parts.append(f"{video.width}×{video.height}")
    parts.append(human_size(video.size))
    lines = [" · ".join(parts)]
    if video.watermark is Watermark.CLEAN:
        lines.append("✨ Без водяного знака")
    elif video.watermark is Watermark.WATERMARKED:
        lines.append("⚠️ Чистая версия недоступна — это видео с водяным знаком")
    elif video.watermark is Watermark.UNKNOWN:
        lines.append("ℹ️ Не удалось подтвердить, что водяного знака нет")
    return "\n".join(lines)


def summary_notes(skipped_non_video: int, skipped_too_large: list[int | None], truncated: bool,
                  limit: int, max_items: int) -> str | None:
    notes = []
    if skipped_too_large:
        notes.append(f"📦 Пропущено видео больше {human_size(limit)}: {len(skipped_too_large)}")
    if skipped_non_video:
        notes.append(f"🖼 Пропущено фото: {skipped_non_video}")
    if truncated:
        notes.append(f"✂️ Отправлены только первые {max_items} элементов публикации")
    return "\n".join(notes) or None


def help_text(limit: int, ttl_seconds: int) -> str:
    return HELP.format(limit=escape(human_size(limit)), ttl=ttl_seconds // 60)
