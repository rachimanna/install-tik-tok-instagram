import logging

from bot.redact import RedactingFilter, redact, redact_url


def test_bot_token_is_hidden():
    assert "AAH" not in redact("url https://api.telegram.org/bot123456789:AAHfakeFAKEfakeFAKEfakeFAKEfake123/getMe")
    assert "<bot-token>" in redact("token=123456789:AAHfakeFAKEfakeFAKEfakeFAKEfake123")


def test_signed_cdn_url_is_hidden():
    url = "https://v16-webapp.tiktokcdn.com/abc/def/?a=1988&x-expires=1&x-signature=SECRET"
    assert "SECRET" not in redact(f"downloading {url}")
    assert redact_url(url) == "https://v16-webapp.tiktokcdn.com/…"


def test_query_removed_from_page_url():
    assert redact_url("https://www.instagram.com/reel/abc/?igsh=SECRET") == "https://www.instagram.com/reel/abc/?…"


def test_cookies_hidden():
    out = redact("Cookie: sessionid=SECRETVALUE; csrftoken=OTHER")
    assert "SECRETVALUE" not in out


def test_logging_filter_redacts_args_and_exceptions(caplog):
    logger = logging.getLogger("test.redact")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="test.redact"):
        try:
            raise RuntimeError("fail https://scontent.cdninstagram.com/v/t50/x.mp4?oh=SIGNATURE")
        except RuntimeError:
            logger.exception("got %s", "https://x.fbcdn.net/v.mp4?oh=SIG2")
    text = caplog.text
    assert "SIGNATURE" not in text and "SIG2" not in text
