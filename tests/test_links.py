import pytest

from bot.links import LinkErrorCode, LinkError, LinkKind, Platform, extract_url, parse_link


@pytest.mark.parametrize("text, expected", [
    ("смотри https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/ круто", "https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/"),
    ("vm.tiktok.com/ZMabc/", "https://vm.tiktok.com/ZMabc/"),
    ("(https://www.instagram.com/reel/Cabc123/).", "https://www.instagram.com/reel/Cabc123/"),
    ("нет ссылки", None),
    ("", None),
    (None, None),
])
def test_extract_url(text, expected):
    assert extract_url(text) == expected


@pytest.mark.parametrize("url, kind, normalized", [
    ("https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/", LinkKind.TIKTOK_SHORT, "https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/"),
    ("https://vt.tiktok.com/ZSabc/", LinkKind.TIKTOK_SHORT, "https://vt.tiktok.com/ZSabc/"),
    ("https://www.tiktok.com/t/ZTabc/", LinkKind.TIKTOK_SHORT, "https://www.tiktok.com/t/ZTabc/"),
    ("https://www.tiktok.com/@user.name/video/7123456789012345678?is_from_webapp=1&sender_device=pc",
     LinkKind.TIKTOK_POST, "https://www.tiktok.com/@user.name/video/7123456789012345678"),
    ("https://tiktok.com/@u/video/7123456789012345678", LinkKind.TIKTOK_POST,
     "https://www.tiktok.com/@u/video/7123456789012345678"),
    ("https://m.tiktok.com/v/7123456789012345678.html", LinkKind.TIKTOK_POST,
     "https://www.tiktok.com/@/video/7123456789012345678"),
    ("https://www.tiktok.com/embed/v2/7123456789012345678", LinkKind.TIKTOK_POST,
     "https://www.tiktok.com/@/video/7123456789012345678"),
    ("https://www.tiktok.com/@u/photo/7123456789012345678?lang=ru", LinkKind.TIKTOK_POST,
     "https://www.tiktok.com/@u/video/7123456789012345678"),
    ("https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=abc", LinkKind.INSTAGRAM_REEL,
     "https://www.instagram.com/reel/C1a2B3c4D5e/"),
    ("https://instagram.com/reels/C1a2B3c4D5e", LinkKind.INSTAGRAM_REEL, "https://www.instagram.com/reel/C1a2B3c4D5e/"),
    ("https://www.instagram.com/p/C1a2B3c4D5e/", LinkKind.INSTAGRAM_POST, "https://www.instagram.com/p/C1a2B3c4D5e/"),
    ("https://www.instagram.com/someuser/p/C1a2B3c4D5e/", LinkKind.INSTAGRAM_POST,
     "https://www.instagram.com/p/C1a2B3c4D5e/"),
    ("https://www.instagram.com/tv/C1a2B3c4D5e/", LinkKind.INSTAGRAM_POST, "https://www.instagram.com/tv/C1a2B3c4D5e/"),
    ("https://www.instagram.com/share/reel/BAabc123/", LinkKind.INSTAGRAM_SHARE,
     "https://www.instagram.com/share/reel/BAabc123/"),
])
def test_parse_supported(url, kind, normalized):
    link = parse_link(url)
    assert link.kind is kind
    assert link.url == normalized
    assert link.original == url


def test_tiktok_video_path_is_not_assumed_to_be_regular_video():
    # Адрес /video/<id> может открывать сторис — тип означает «публикация», а не «обычное видео».
    link = parse_link("https://www.tiktok.com/@someone/video/7400000000000000000")
    assert link.kind is LinkKind.TIKTOK_POST
    assert link.platform is Platform.TIKTOK
    assert "story" not in link.kind.value and link.kind.value != "tiktok_video"


@pytest.mark.parametrize("url, code", [
    ("https://tiktok.com.evil.com/@u/video/1", LinkErrorCode.DOMAIN_NOT_ALLOWED),
    ("https://eviltiktok.com/@u/video/1", LinkErrorCode.DOMAIN_NOT_ALLOWED),
    ("https://evil.com/www.tiktok.com/@u/video/1", LinkErrorCode.DOMAIN_NOT_ALLOWED),
    ("https://www.tiktok.com@127.0.0.1/@u/video/1", LinkErrorCode.CREDENTIALS),
    ("https://user:pass@www.instagram.com/p/abc/", LinkErrorCode.CREDENTIALS),
    ("https://www.instagram.com:8080/p/abc/", LinkErrorCode.BAD_PORT),
    ("ftp://www.instagram.com/p/abc/", LinkErrorCode.BAD_SCHEME),
    ("file:///etc/passwd", LinkErrorCode.BAD_SCHEME),
    ("http://127.0.0.1/", LinkErrorCode.DOMAIN_NOT_ALLOWED),
    ("http://169.254.169.254/latest/meta-data/", LinkErrorCode.DOMAIN_NOT_ALLOWED),
    ("http://[::1]/", LinkErrorCode.BAD_HOST),
    ("https://localhost/@u/video/1", LinkErrorCode.DOMAIN_NOT_ALLOWED),
    ("https://xn--tktok-3ra.com/@u/video/1", LinkErrorCode.DOMAIN_NOT_ALLOWED),
    ("https://www.instagram.com/stories/highlights/", LinkErrorCode.UNSUPPORTED_CONTENT),
    ("https://www.instagram.com/s/bm90LWEtaGlnaGxpZ2h0/", LinkErrorCode.UNSUPPORTED_CONTENT),
    ("https://www.instagram.com/someuser/", LinkErrorCode.UNSUPPORTED_CONTENT),
    ("https://www.tiktok.com/@someuser", LinkErrorCode.UNSUPPORTED_CONTENT),
    ("https://www.tiktok.com/" + "a" * 3000, LinkErrorCode.TOO_LONG),
])
def test_parse_rejected(url, code):
    with pytest.raises(LinkError) as err:
        parse_link(url)
    assert err.value.code is code


def test_unicode_host_rejected():
    with pytest.raises(LinkError) as err:
        parse_link("https://tіktok.com/@u/video/1")  # кириллическая «і»
    assert err.value.code in (LinkErrorCode.BAD_HOST, LinkErrorCode.DOMAIN_NOT_ALLOWED)


@pytest.mark.parametrize("url, normalized, content_id", [
    ("https://www.instagram.com/stories/some.user/3412345678901234567/?igsh=abc",
     "https://www.instagram.com/stories/some.user/3412345678901234567/", "3412345678901234567"),
    ("https://instagram.com/stories/some.user", "https://www.instagram.com/stories/some.user/", None),
    ("https://www.instagram.com/stories/highlights/17900000000000000/",
     "https://www.instagram.com/stories/highlights/17900000000000000/", None),
    # «Поделиться» из «Актуального»: base64("highlight:17900000000000000")
    ("https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTAwMDAwMDAwMDAwMDAw?story_media_id=3412345678901234567_123&igsh=x",
     "https://www.instagram.com/stories/highlights/17900000000000000/", "3412345678901234567"),
])
def test_instagram_story_links(url, normalized, content_id):
    link = parse_link(url)
    assert link.kind is LinkKind.INSTAGRAM_STORY
    assert (link.url, link.content_id) == (normalized, content_id)
