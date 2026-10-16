"""Все тексты интерфейса (русский)."""

from __future__ import annotations

from html import escape

from .links import LinkErrorCode, Platform
from .media import human_size
from .providers.base import DownloadedMedia, ErrorCode, MediaKind, Watermark

START = (
    "👋 Привет! Отправь ссылку на видео из TikTok или Instagram.\n\n"
    "🎬 Reels и видео\n"
    "🖼 Фото и карусели\n"
    "📲 Сторис Instagram — видео и фото\n"
    "🎵 Звук из видео отдельным аудиофайлом\n"
    "✨ TikTok без водяного знака — когда доступна чистая версия\n"
    "📱 TikTok Stories — если они доступны для скачивания\n"
    "📁 Отправка исходного файла"
)

HELP = (
    "<b>Как пользоваться</b>\n\n"
    "1. В TikTok или Instagram нажми «Поделиться» → «Копировать ссылку».\n"
    "2. Пришли ссылку сюда сообщением.\n"
    "3. Получи видео или фото. Следом за видео придёт его звук аудиофайлом. "
    "Кнопка «📁 Скачать оригинальным файлом» пришлёт те же файлы документами — "
    "без сжатия Telegram.\n\n"
    "<b>Поддерживается</b>\n"
    "• TikTok: видео и фото-посты (полные и короткие ссылки vm.tiktok.com), сторис — "
    "пока она доступна и если TikTok отдаёт её загрузчику\n"
    "• Instagram: Reels, видео и фото из публичных публикаций, включая карусели\n"
    "<b>Не поддерживается</b>\n"
    "• закрытые аккаунты и удалённые публикации\n\n"
    "<b>Сторис Instagram</b>\n"
    "Видео и фото из сторис и «Актуального» — если администратор подключил аккаунт бота. "
    "Сторис живут 24 часа.\n\n"
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
BTN_DOCUMENTS = "📁 Скачать оригиналы файлами"
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
}

RESOLVE_ERRORS = {
    "network": "🌐 Не удалось открыть ссылку. Попробуй ещё раз чуть позже.",
    "dead_link": "😔 Ссылка недействительна или публикация удалена.",
    "too_many_redirects": "⚠️ Ссылка ведёт по слишком длинной цепочке переадресаций.",
}

INSTAGRAM_STORIES_DISABLED = (
    "📱 Сторис Instagram открываются только после входа в аккаунт, а администратор бота "
    "ещё не подключил аккаунт. Посты, Reels и фото работают без этого."
)

INSTAGRAM_LOGIN_FAILED = (
    "🔐 Аккаунт бота для сторис ждёт подтверждения входа. Администратор: отправьте боту "
    "команду /iglogin. Посты, Reels и фото работают как обычно."
)

IGLOGIN_DISABLED = (
    "Вход не настроен: впишите в Render переменные INSTAGRAM_USERNAME и INSTAGRAM_PASSWORD "
    "служебного аккаунта."
)
IGLOGIN_USES_COOKIES = (
    "Бот уже использует сохранённую сессию (INSTAGRAM_COOKIES). Чтобы войти заново, удалите эту "
    "переменную в Render и после перезапуска отправьте /iglogin."
)
IGLOGIN_FORBIDDEN = "Эта команда только для администратора бота."
IGLOGIN_BUSY = "Вход уже идёт. Если бот ждёт код — пришлите его сюда."
IGLOGIN_START = "🔐 Вхожу в служебный аккаунт Instagram…"
IGLOGIN_CODE_RECEIVED = "🔎 Проверяю код…"


def iglogin_ask_code(channel: str) -> str:
    where = {"sms": "SMS на телефон", "email": "почту"}.get(
        channel, "почту, SMS или в приложение-аутентификатор")
    return (f"📧 Instagram просит код подтверждения входа — он приходит на {where} служебного аккаунта.\n\n"
            "Пришлите самый свежий код сюда одним сообщением (только цифры). Жду 5 минут.")


def iglogin_ok(sessionid: str) -> str:
    return (
        "✅ Вход выполнен, сторис Instagram работают.\n\n"
        "Чтобы вход не терялся при перезапуске Render, добавьте в <b>Environment</b> переменную "
        "<b>INSTAGRAM_COOKIES</b> со значением (нажмите, чтобы скопировать):\n\n"
        f"<code>sessionid={escape(sessionid)}</code>\n\n"
        "⚠️ После этого удалите это сообщение: по этой строке можно войти в аккаунт."
    )


IGLOGIN_FAILED = {
    "bad_credentials": "❌ Неверный логин или пароль. Проверьте INSTAGRAM_USERNAME и INSTAGRAM_PASSWORD в Render.",
    "two_factor": ("❌ Instagram не принял код. Если на аккаунте включена двухфакторная аутентификация — "
                   "отключите её; если код пришёл на почту — возможно, этот способ проверки библиотека "
                   "не поддерживает. Причина записана в логах Render."),
    "checkpoint": ("❌ Instagram не принял вход (неверный код или другая проверка). "
                   "Откройте Instagram в служебном аккаунте, проверьте уведомления и повторите /iglogin."),
    "code_timeout": "⏱ Код не пришёл за 5 минут. Отправьте /iglogin ещё раз.",
    "blocked": "🚧 Instagram временно ограничил вход с сервера бота. Попробуйте через час.",
    "other": "❌ Вход не удался. Подробности в логах Render. Попробуйте /iglogin позже.",
}

UNSAFE_ADDRESS = "⚠️ Эта ссылка ведёт на недопустимый адрес."


def provider_error(code: ErrorCode, platform: Platform, limit: int, size: int | None = None,
                   *, story: bool = False) -> str:
    if story and platform is Platform.INSTAGRAM:
        if code is ErrorCode.NOT_FOUND:
            return "😔 Сторис не найдена: сторис живут 24 часа, возможно, она уже исчезла."
        if code in (ErrorCode.PRIVATE, ErrorCode.LOGIN_REQUIRED):
            return ("🔒 Не удалось открыть сторис: профиль закрыт для аккаунта бота "
                    "или вход в аккаунт бота устарел.")
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
    if code is ErrorCode.NO_MEDIA:
        return "😔 В этой публикации не нашлось ни видео, ни фото, которые можно скачать."
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


def _item_parts(item: DownloadedMedia) -> list[str]:
    parts = []
    if item.width and item.height:
        parts.append(f"{item.width}×{item.height}")
    parts.append(human_size(item.size))
    return parts


def _watermark_line(item: DownloadedMedia) -> str | None:
    if item.watermark is Watermark.CLEAN:
        return "✨ Без водяного знака"
    if item.watermark is Watermark.WATERMARKED:
        return "⚠️ Чистая версия недоступна — это видео с водяным знаком"
    if item.watermark is Watermark.UNKNOWN:
        return "ℹ️ Не удалось подтвердить, что водяного знака нет"
    return None


def _icon(item: DownloadedMedia) -> str:
    return "🖼" if item.kind is MediaKind.PHOTO else "🎬"


def single_caption(platform: Platform, item: DownloadedMedia) -> str:
    lines = [" · ".join([f"{_icon(item)} <b>{platform.title}</b>", *_item_parts(item)])]
    if line := _watermark_line(item):
        lines.append(line)
    return "\n".join(lines)


def _count(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def album_caption(platform: Platform, items: list[DownloadedMedia]) -> str:
    videos = sum(1 for i in items if i.kind is MediaKind.VIDEO)
    photos = len(items) - videos
    counts = []
    if videos:
        counts.append(_count(videos, "видео", "видео", "видео"))
    if photos:
        counts.append(_count(photos, "фото", "фото", "фото"))
    lines = [f"📦 <b>{platform.title}</b> · {', '.join(counts)}", ""]
    for index, item in enumerate(items, start=1):
        line = f"{index}. {_icon(item)} " + " · ".join(_item_parts(item))
        if item.watermark is Watermark.CLEAN:
            line += " · ✨ без водяного знака"
        elif item.watermark is Watermark.WATERMARKED:
            line += " · ⚠️ с водяным знаком"
        lines.append(line)
    return "\n".join(lines)


def audio_title(platform: Platform, item: DownloadedMedia) -> str:
    title = (item.title or "").strip() or f"Звук из {platform.title}"
    return title[:64]


def summary_notes(skipped_unsupported: int, skipped_too_large: list[int | None], truncated: bool,
                  limit: int, max_items: int) -> str | None:
    notes = []
    if skipped_too_large:
        notes.append(f"📦 Пропущено файлов больше {human_size(limit)}: {len(skipped_too_large)}")
    if skipped_unsupported:
        notes.append(f"⚠️ Не удалось скачать элементов: {skipped_unsupported}")
    if truncated:
        notes.append(f"✂️ Отправлены только первые {max_items} элементов публикации")
    return "\n".join(notes) or None


def help_text(limit: int, ttl_seconds: int) -> str:
    return HELP.format(limit=escape(human_size(limit)), ttl=ttl_seconds // 60)
