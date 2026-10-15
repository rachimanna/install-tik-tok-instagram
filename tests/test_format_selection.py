"""Проверка строк выбора формата на настоящем yt-dlp (без сети: форматы синтетические,
повторяют поля, которые выставляет экстрактор TikTok/Instagram в yt-dlp 2026.8.19)."""

import pytest
import yt_dlp

from bot.links import Platform
from bot.providers.base import Watermark
from bot.providers.ytdlp_provider import build_format_spec, classify_watermark

MB = 1024 * 1024
LIMIT = 50 * MB


def select(platform, formats, allow_watermark=True, limit=LIMIT):
    info = {
        "id": "1", "title": "t", "extractor": "TikTok", "extractor_key": "TikTok",
        "webpage_url": "https://www.tiktok.com/@u/video/1", "formats": formats,
    }
    opts = {"format": build_format_spec(platform, limit, allow_watermark), "quiet": True,
            "simulate": True, "logger": None}
    with yt_dlp.YoutubeDL(opts) as ydl:
        res = ydl.process_ie_result(info, download=False)
    return res.get("requested_formats") or [res]


def tt(fid, h, note=None, size=None, vcodec="h264", acodec="aac"):
    return {"format_id": fid, "url": f"https://v16.tiktokcdn.com/{fid}.mp4", "ext": "mp4",
            "width": h * 9 // 16, "height": h, "vcodec": vcodec, "acodec": acodec,
            "format_note": note, "filesize": size}


TIKTOK_FORMATS = [
    tt("download", 720, note="watermarked", size=5 * MB),
    tt("play", 720, size=4 * MB),  # format_note отсутствует — не должен отфильтроваться
    tt("h264_540p_1", 1024, size=6 * MB),
    tt("bytevc1_1080p_2", 1920, size=8 * MB, vcodec="h265"),
    {"format_id": "audio", "url": "https://sf16.tiktokcdn.com/a.m4a", "ext": "m4a",
     "vcodec": "none", "acodec": "aac"},
]


def test_tiktok_picks_best_clean_with_audio():
    chosen = select(Platform.TIKTOK, TIKTOK_FORMATS)
    assert [f["format_id"] for f in chosen] == ["bytevc1_1080p_2"]
    assert classify_watermark(Platform.TIKTOK, chosen) is Watermark.CLEAN


def test_tiktok_never_merges_music_track():
    chosen = select(Platform.TIKTOK, TIKTOK_FORMATS)
    assert all(f["format_id"] != "audio" for f in chosen)


def test_tiktok_watermarked_fallback_is_labelled():
    formats = [tt("download", 720, note="watermarked", size=5 * MB)]
    chosen = select(Platform.TIKTOK, formats)
    assert chosen[0]["format_id"] == "download"
    assert classify_watermark(Platform.TIKTOK, chosen) is Watermark.WATERMARKED


def test_tiktok_without_fallback_fails_instead_of_faking_clean():
    formats = [tt("download", 720, note="watermarked", size=5 * MB)]
    with pytest.raises(yt_dlp.utils.YoutubeDLError, match="Requested format is not available"):
        select(Platform.TIKTOK, formats, allow_watermark=False)


def test_tiktok_prefers_clean_that_fits_limit():
    formats = [tt("bytevc1_1080p_2", 1920, size=80 * MB, vcodec="h265"), tt("play", 720, size=10 * MB)]
    chosen = select(Platform.TIKTOK, formats)
    assert chosen[0]["format_id"] == "play"


def test_tiktok_skips_unplayable_bytevc2():
    formats = [tt("bytevc2_1080p", 1920, note="(UNPLAYABLE)", vcodec="bytevc2"), tt("play", 720)]
    assert select(Platform.TIKTOK, formats)[0]["format_id"] == "play"


def test_tiktok_unlabelled_download_addr_is_unknown():
    assert classify_watermark(Platform.TIKTOK, [{"format_id": "download_addr-0", "format_note": "Download video"}]) \
        is Watermark.UNKNOWN


def test_instagram_merges_best_video_and_audio():
    formats = [
        {"format_id": "101", "url": "https://scontent.cdninstagram.com/a.mp4", "ext": "mp4",
         "width": 720, "height": 1280, "vcodec": None, "acodec": None},
        {"format_id": "dash-v1080", "url": "https://scontent.cdninstagram.com/v.mp4", "ext": "mp4",
         "width": 1080, "height": 1920, "vcodec": "avc1.640028", "acodec": "none", "tbr": 3000},
        {"format_id": "dash-a", "url": "https://scontent.cdninstagram.com/a.m4a", "ext": "m4a",
         "vcodec": "none", "acodec": "mp4a.40.2", "tbr": 128},
    ]
    chosen = select(Platform.INSTAGRAM, formats)
    assert [f["format_id"] for f in chosen] == ["dash-v1080", "dash-a"]
    assert classify_watermark(Platform.INSTAGRAM, chosen) is Watermark.NOT_APPLICABLE
