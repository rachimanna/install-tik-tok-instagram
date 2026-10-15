"""YtDlpProvider с подменённым YoutubeDL: видео, фото, карусели, лимиты, ошибки."""

import io
import shutil
import threading
from pathlib import Path

import pytest
import yt_dlp

from bot.links import parse_link
from bot.providers.base import ErrorCode, MediaKind, ProviderError, Watermark
from bot.providers.ytdlp_provider import (YtDlpProvider, best_instagram_image, classify_error,
                                          ensure_public_url, tiktok_image_urls)

MB = 1024 * 1024


class FakeResponse(io.BytesIO):
    def __init__(self, data, url, ctype="image/jpeg"):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Type": ctype, "Content-Length": str(len(data))}


class FakeTikTokIE:
    def __init__(self, item):
        self.item = item

    def _extract_web_data_and_status(self, url, video_id, fatal=True):
        return self.item, 0


def make_fake_ydl(info, media_for=None, error=None, tiktok_item=None, files=None):
    """media_for(entry) -> (исходный файл, выбранные форматы); files: url -> (bytes, content-type)."""

    class FakeYDL:
        calls = []
        opened = []

        def __init__(self, params):
            self.params = params
            self.workdir = Path(params["paths"]["home"])
            self.ie = FakeTikTokIE(tiktok_item)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_info_extractor(self, key):
            assert key == "TikTok"
            return self.ie

        def extract_info(self, url, download=False, process=False):
            assert self.params.get("ignore_no_formats_error") is True
            if error:
                raise error
            if tiktok_item is not None:
                # Как настоящий TikTokIE: данные страницы берутся этим методом.
                self.ie._extract_web_data_and_status(url, "1")
            return info

        def urlopen(self, request):
            FakeYDL.opened.append(request.url)
            data, ctype = files[request.url]
            return FakeResponse(data, request.url, ctype)

        def process_ie_result(self, entry, download=True):
            FakeYDL.calls.append(entry["id"])
            src, chosen = media_for(entry)
            for hook in self.params["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": src.stat().st_size if src else 0})
            res = {**entry, "requested_formats": chosen if len(chosen) > 1 else None, **chosen[0]}
            if src is not None:
                target = self.workdir / f"{entry['id']}.mp4"
                shutil.copyfile(src, target)
                res["requested_downloads"] = [{"filepath": str(target)}]
            else:
                res["requested_downloads"] = [{}]
            return res

    return FakeYDL


def provider(fake, **kw):
    return YtDlpProvider(max_bytes=kw.pop("max_bytes", 50 * MB), ydl_class=fake,
                         url_checker=lambda url: None, **kw)


IG = parse_link("https://www.instagram.com/p/Cabc/")
TT = parse_link("https://www.tiktok.com/@u/video/7500000000000000000")
TT_PHOTO = parse_link("https://www.tiktok.com/@u/photo/7500000000000000000")


def video_entry(i):
    return {"id": f"v{i}", "formats": [{"format_id": "1", "vcodec": "avc1", "url": "https://x/1"}]}


def photo_entry(i, url):
    return {"id": f"p{i}", "formats": [], "thumbnails": [
        {"url": url + "?small", "width": 320, "height": 400},
        {"url": url, "width": 1080, "height": 1350},
    ]}


def test_instagram_carousel_mixed_order(tmp_path, sample_mp4, sample_jpg):
    jpg = sample_jpg.read_bytes()
    info = {"_type": "playlist", "id": "Cabc", "extractor": "Instagram", "entries": [
        video_entry(1), photo_entry(2, "https://scontent.cdninstagram.com/2.jpg"), video_entry(3)]}
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "dash"}]),
                         files={"https://scontent.cdninstagram.com/2.jpg": (jpg, "image/jpeg")})
    result = provider(fake).download(IG, tmp_path, threading.Event())
    assert [i.kind for i in result.items] == [MediaKind.VIDEO, MediaKind.PHOTO, MediaKind.VIDEO]
    photo = result.items[1]
    assert photo.path.suffix == ".jpg" and photo.path.read_bytes() == jpg
    assert (photo.width, photo.height) == (1080, 1350) and photo.image_codec == "mjpeg"
    assert fake.opened == ["https://scontent.cdninstagram.com/2.jpg"]  # самый большой вариант
    v = result.items[0]
    assert (v.width, v.height) == (360, 640) and v.thumbnail.is_file()
    assert v.watermark is Watermark.NOT_APPLICABLE


def test_instagram_single_photo_post(tmp_path, sample_jpg):
    url = "https://scontent.cdninstagram.com/only.jpg"
    info = {**photo_entry(1, url), "id": "Cabc", "extractor": "Instagram"}
    fake = make_fake_ydl(info, files={url: (sample_jpg.read_bytes(), "image/jpeg")})
    result = provider(fake).download(IG, tmp_path, threading.Event())
    assert len(result.items) == 1 and result.items[0].kind is MediaKind.PHOTO


TIKTOK_SLIDESHOW = {
    "id": "7500000000000000000",
    "imagePost": {"images": [
        {"imageURL": {"urlList": ["https://p16-sign.tiktokcdn-us.com/a~tplv-photomode-image.webp?x=1",
                                  "https://p16-sign.tiktokcdn-us.com/a~tplv-photomode-image.jpeg?x=1"]},
         "imageWidth": 1080, "imageHeight": 1440},
        {"imageURL": {"urlList": ["https://p16-sign.tiktokcdn-us.com/b~tplv-photomode-image.jpeg?x=2"]},
         "imageWidth": 1080, "imageHeight": 1440},
    ]},
}


def test_tiktok_slideshow_downloads_photos_not_audio(tmp_path, sample_jpg):
    jpg = sample_jpg.read_bytes()
    # yt-dlp для слайдшоу отдаёт только музыку — её не скачиваем.
    info = {"id": "7500000000000000000", "extractor": "TikTok",
            "formats": [{"format_id": "audio", "vcodec": "none", "acodec": "aac"}]}
    files = {
        "https://p16-sign.tiktokcdn-us.com/a~tplv-photomode-image.jpeg?x=1": (jpg, "image/jpeg"),
        "https://p16-sign.tiktokcdn-us.com/b~tplv-photomode-image.jpeg?x=2": (jpg, "image/jpeg"),
    }
    fake = make_fake_ydl(info, tiktok_item=TIKTOK_SLIDESHOW, files=files)
    result = provider(fake).download(TT_PHOTO, tmp_path, threading.Event())
    assert [i.kind for i in result.items] == [MediaKind.PHOTO, MediaKind.PHOTO]
    assert fake.calls == []  # звуковую дорожку не скачивали
    assert fake.opened[0].endswith("a~tplv-photomode-image.jpeg?x=1")  # JPEG предпочтительнее WebP
    assert all(i.watermark is Watermark.NOT_APPLICABLE for i in result.items)


def test_tiktok_photo_falls_back_to_next_url(tmp_path, sample_jpg):
    item = {"imagePost": {"images": [{"imageURL": {"urlList": [
        "https://p16.tiktokcdn.com/x.jpeg", "https://p19.tiktokcdn.com/x.jpeg"]}}]}}
    files = {"https://p16.tiktokcdn.com/x.jpeg": (b"<html>", "text/html"),
             "https://p19.tiktokcdn.com/x.jpeg": (sample_jpg.read_bytes(), "image/jpeg")}
    fake = make_fake_ydl({"id": "1", "formats": []}, tiktok_item=item, files=files)
    result = provider(fake).download(TT, tmp_path, threading.Event())
    assert len(result.items) == 1 and result.items[0].path.suffix == ".jpg"


def test_tiktok_regular_video_still_works(tmp_path, sample_mp4):
    info = {"id": "7500000000000000000", "extractor": "TikTok", "formats": [{"vcodec": "h264"}]}
    item = {"id": "7500000000000000000", "video": {"duration": 12}}  # без imagePost
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "play"}]), tiktok_item=item)
    result = provider(fake).download(TT, tmp_path, threading.Event())
    assert result.items[0].kind is MediaKind.VIDEO
    assert result.items[0].watermark is Watermark.CLEAN


def test_photo_too_large_is_skipped(tmp_path):
    item = {"imagePost": {"images": [{"imageURL": {"urlList": ["https://p16.tiktokcdn.com/x.jpeg"]}}]}}
    fake = make_fake_ydl({"id": "1", "formats": []}, tiktok_item=item,
                         files={"https://p16.tiktokcdn.com/x.jpeg": (b"x" * 5000, "image/jpeg")})
    with pytest.raises(ProviderError) as err:
        provider(fake, max_bytes=1000).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.TOO_LARGE


def test_max_items_truncates(tmp_path, sample_mp4):
    info = {"_type": "playlist", "id": "C", "entries": [video_entry(i) for i in range(5)]}
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "1"}]))
    result = provider(fake, max_items=2).download(IG, tmp_path, threading.Event())
    assert len(result.items) == 2 and result.truncated


def test_tiktok_watermark_label_comes_from_chosen_format(tmp_path, sample_mp4):
    info = {"id": "7500000000000000000", "extractor": "TikTok", "formats": [{"vcodec": "h264"}]}
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "download", "format_note": "watermarked"}]))
    result = provider(fake).download(TT, tmp_path, threading.Event())
    assert result.items[0].watermark is Watermark.WATERMARKED


def test_audio_only_result_is_not_sent_as_video(tmp_path, sample_m4a):
    info = {"id": "1", "formats": [{"vcodec": "h264"}]}
    fake = make_fake_ydl(info, lambda e: (sample_m4a, [{"format_id": "play"}]))
    with pytest.raises(ProviderError) as err:
        provider(fake).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.NO_MEDIA


def test_nothing_downloadable(tmp_path):
    fake = make_fake_ydl({"id": "1", "formats": [{"vcodec": "none"}]}, tiktok_item={"id": "1"})
    with pytest.raises(ProviderError) as err:
        provider(fake).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.NO_MEDIA


def test_too_large_is_reported_not_retried(tmp_path, sample_mp4):
    info = {"id": "1", "formats": [{"vcodec": "h264"}]}
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "play"}]))
    with pytest.raises(ProviderError) as err:
        provider(fake, max_bytes=1000).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.TOO_LARGE
    assert fake.calls == ["1"]


def test_missing_file_with_known_oversize(tmp_path):
    info = {"id": "1", "formats": [{"vcodec": "h264"}]}
    fake = make_fake_ydl(info, lambda e: (None, [{"format_id": "play", "filesize": 90 * MB}]))
    with pytest.raises(ProviderError) as err:
        provider(fake).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.TOO_LARGE and err.value.size == 90 * MB


def test_cancel_event_stops_download(tmp_path, sample_mp4):
    cancel = threading.Event()
    cancel.set()
    fake = make_fake_ydl({"id": "1", "formats": [{"vcodec": "h264"}]},
                         lambda e: (sample_mp4, [{"format_id": "play"}]))
    with pytest.raises(ProviderError) as err:
        provider(fake).download(TT, tmp_path, cancel)
    assert err.value.code is ErrorCode.TIMEOUT


def test_image_url_helpers():
    assert [i["url"] for i in tiktok_image_urls(TIKTOK_SLIDESHOW)] == [
        "https://p16-sign.tiktokcdn-us.com/a~tplv-photomode-image.jpeg?x=1",
        "https://p16-sign.tiktokcdn-us.com/b~tplv-photomode-image.jpeg?x=2"]
    assert tiktok_image_urls({"imagePost": {"images": [{"imageURL": {"urlList": ["http://insecure/x"]}}]}}) == []
    assert tiktok_image_urls(None) == []
    assert best_instagram_image({"thumbnails": []}) is None


@pytest.mark.parametrize("url", ["http://p16.tiktokcdn.com/x.jpg", "https://127.0.0.1/x.jpg",
                                 "https://10.0.0.1/x.jpg", "https://u:p@example.com/x.jpg"])
def test_media_url_must_be_public_https(url):
    with pytest.raises(ProviderError):
        ensure_public_url(url)


@pytest.mark.parametrize("message, code", [
    ("ERROR: [TikTok] 1: Video not available, status code 0", ErrorCode.NOT_FOUND),
    ("ERROR: [TikTok] 1: Your IP address is blocked from accessing this post", ErrorCode.BLOCKED),
    ("ERROR: [TikTok] 1: You do not have permission to view this post. Log into an account", ErrorCode.PRIVATE),
    ("ERROR: [Instagram] C: Requested content is not available, rate-limit reached or login required. "
     "Use --cookies", ErrorCode.BLOCKED),
    ("ERROR: [Instagram] C: Instagram sent an empty media response. Check if this post is accessible "
     "in your browser without being logged-in. Use --cookies-from-browser", ErrorCode.LOGIN_REQUIRED),
    ("ERROR: [TikTok] 1: Requested format is not available", ErrorCode.NO_MEDIA),
    ("ERROR: Unsupported URL: https://x", ErrorCode.UNSUPPORTED),
    ("ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure>", ErrorCode.NETWORK),
    ("something weird", ErrorCode.UNKNOWN),
])
def test_classify_error(message, code):
    assert classify_error(message) is code


def test_ytdlp_error_maps_to_provider_error(tmp_path):
    fake = make_fake_ydl({}, error=yt_dlp.utils.DownloadError(
        "ERROR: [TikTok] 1: Video not available, status code 10231"))
    with pytest.raises(ProviderError) as err:
        provider(fake).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.NOT_FOUND


def test_no_clean_version_when_fallback_disabled(tmp_path):
    fake = make_fake_ydl({}, error=yt_dlp.utils.ExtractorError(
        "Requested format is not available. Use --list-formats", expected=True))
    with pytest.raises(ProviderError) as err:
        provider(fake, allow_watermark_fallback=False).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.NO_CLEAN_VERSION


def test_cookie_file_is_copied_not_modified(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    work = tmp_path / "job"
    work.mkdir()
    opts = YtDlpProvider(max_bytes=MB, cookies_file=cookies)._options(TT, work, threading.Event())
    assert opts["cookiefile"] == str(work / "cookies.txt")
    assert opts["allowed_extractors"] == ["tiktok", "instagram"]


def test_capture_hooks_real_tiktok_extractor():
    """Перехват работает с настоящим TikTokIE yt-dlp (метод существует и подменяется)."""
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        captured = YtDlpProvider._capture_tiktok_item(ydl)
        ie = ydl.get_info_extractor("TikTok")
        assert ie._extract_web_data_and_status.__name__ == "capture"
        assert captured == {}
