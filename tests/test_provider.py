"""YtDlpProvider с подменённым YoutubeDL: порядок элементов, пропуск фото, лимиты, ошибки."""

import shutil
import threading
from pathlib import Path

import pytest
import yt_dlp

from bot.links import parse_link
from bot.providers.base import ErrorCode, ProviderError, Watermark
from bot.providers.ytdlp_provider import YtDlpProvider, classify_error

MB = 1024 * 1024


def make_fake_ydl(info, media_for, error=None):
    """media_for(entry) -> (путь к исходному файлу, выбранные форматы)."""

    class FakeYDL:
        calls = []

        def __init__(self, params):
            self.params = params
            self.workdir = Path(params["paths"]["home"])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False, process=False):
            if error:
                raise error
            return info

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


IG = parse_link("https://www.instagram.com/p/Cabc/")
TT = parse_link("https://www.tiktok.com/@u/video/7500000000000000000")


def video_entry(i):
    return {"id": f"v{i}", "formats": [{"format_id": "1", "vcodec": "avc1", "url": "https://x/1"}]}


def test_instagram_carousel_order_and_photo_skip(tmp_path, sample_mp4):
    info = {"_type": "playlist", "id": "Cabc", "extractor": "Instagram", "entries": [
        video_entry(1), {"id": "photo", "formats": []}, video_entry(2), video_entry(3)]}
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "dash"}]))
    provider = YtDlpProvider(max_bytes=50 * MB, ydl_class=fake)
    result = provider.download(IG, tmp_path, threading.Event())
    assert [v.path.name for v in result.videos] == ["v1.mp4", "v2.mp4", "v3.mp4"]
    assert result.skipped_non_video == 1
    v = result.videos[0]
    assert (v.width, v.height) == (360, 640)
    assert v.duration and 1.5 < v.duration < 2.5
    assert v.watermark is Watermark.NOT_APPLICABLE
    assert v.thumbnail is not None and v.thumbnail.is_file()
    assert v.size == sample_mp4.stat().st_size


def test_max_items_truncates(tmp_path, sample_mp4):
    info = {"_type": "playlist", "id": "C", "entries": [video_entry(i) for i in range(5)]}
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "1"}]))
    result = YtDlpProvider(max_bytes=50 * MB, max_items=2, ydl_class=fake).download(IG, tmp_path, threading.Event())
    assert len(result.videos) == 2 and result.truncated


def test_tiktok_watermark_label_comes_from_chosen_format(tmp_path, sample_mp4):
    info = {"id": "7500000000000000000", "extractor": "TikTok", "formats": [{"vcodec": "h264"}]}
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "download", "format_note": "watermarked"}]))
    result = YtDlpProvider(max_bytes=50 * MB, ydl_class=fake).download(TT, tmp_path, threading.Event())
    assert result.videos[0].watermark is Watermark.WATERMARKED


def test_audio_only_result_is_not_sent_as_video(tmp_path, sample_m4a):
    info = {"id": "1", "formats": [{"vcodec": "h264"}]}
    fake = make_fake_ydl(info, lambda e: (sample_m4a, [{"format_id": "play"}]))
    with pytest.raises(ProviderError) as err:
        YtDlpProvider(max_bytes=50 * MB, ydl_class=fake).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.NO_VIDEO


def test_too_large_is_reported_not_retried(tmp_path, sample_mp4):
    info = {"id": "1", "formats": [{"vcodec": "h264"}]}
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "play"}]))
    with pytest.raises(ProviderError) as err:
        YtDlpProvider(max_bytes=1000, ydl_class=fake).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.TOO_LARGE
    assert fake.calls == ["1"]  # одна попытка


def test_missing_file_with_known_oversize(tmp_path):
    info = {"id": "1", "formats": [{"vcodec": "h264"}]}
    fake = make_fake_ydl(info, lambda e: (None, [{"format_id": "play", "filesize": 90 * MB}]))
    with pytest.raises(ProviderError) as err:
        YtDlpProvider(max_bytes=50 * MB, ydl_class=fake).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.TOO_LARGE and err.value.size == 90 * MB


def test_cancel_event_stops_download(tmp_path, sample_mp4):
    info = {"id": "1", "formats": [{"vcodec": "h264"}]}
    cancel = threading.Event()
    cancel.set()
    fake = make_fake_ydl(info, lambda e: (sample_mp4, [{"format_id": "play"}]))
    with pytest.raises(ProviderError) as err:
        YtDlpProvider(max_bytes=50 * MB, ydl_class=fake).download(TT, tmp_path, cancel)
    assert err.value.code is ErrorCode.TIMEOUT


@pytest.mark.parametrize("message, code", [
    ("ERROR: [TikTok] 1: Video not available, status code 0", ErrorCode.NOT_FOUND),
    ("ERROR: [TikTok] 1: Your IP address is blocked from accessing this post", ErrorCode.BLOCKED),
    ("ERROR: [TikTok] 1: You do not have permission to view this post. Log into an account", ErrorCode.PRIVATE),
    ("ERROR: [Instagram] C: Requested content is not available, rate-limit reached or login required. "
     "Use --cookies", ErrorCode.BLOCKED),
    ("ERROR: [Instagram] C: Instagram sent an empty media response. Check if this post is accessible "
     "in your browser without being logged-in. Use --cookies-from-browser", ErrorCode.LOGIN_REQUIRED),
    ("ERROR: [TikTok] 1: Requested format is not available", ErrorCode.NO_VIDEO),
    ("ERROR: Unsupported URL: https://x", ErrorCode.UNSUPPORTED),
    ("ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure>", ErrorCode.NETWORK),
    ("something weird", ErrorCode.UNKNOWN),
])
def test_classify_error(message, code):
    assert classify_error(message) is code


def test_ytdlp_error_maps_to_provider_error(tmp_path):
    fake = make_fake_ydl({}, None, error=yt_dlp.utils.DownloadError(
        "ERROR: [TikTok] 1: Video not available, status code 10231"))
    with pytest.raises(ProviderError) as err:
        YtDlpProvider(max_bytes=MB, ydl_class=fake).download(TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.NOT_FOUND


def test_no_clean_version_when_fallback_disabled(tmp_path):
    fake = make_fake_ydl({}, None, error=yt_dlp.utils.ExtractorError(
        "Requested format is not available. Use --list-formats", expected=True))
    with pytest.raises(ProviderError) as err:
        YtDlpProvider(max_bytes=MB, allow_watermark_fallback=False, ydl_class=fake).download(
            TT, tmp_path, threading.Event())
    assert err.value.code is ErrorCode.NO_CLEAN_VERSION


def test_cookie_file_is_copied_not_modified(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    work = tmp_path / "job"
    work.mkdir()
    opts = YtDlpProvider(max_bytes=MB, cookies_file=cookies)._options(TT, work, threading.Event())
    assert opts["cookiefile"] == str(work / "cookies.txt")
    assert opts["allowed_extractors"] == ["tiktok", "instagram"]
