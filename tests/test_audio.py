"""Звук из видео: извлечение без перекодирования и отправка следом за видео."""

import shutil
import subprocess

import pytest

from bot import media
from bot.links import Platform
from bot.providers.base import AudioTrack, DownloadedMedia, MediaKind


def ffprobe_streams(path):
    info = media.probe(path)
    return info.has_video, info.has_audio, info.audio_codec


def test_aac_track_copied_to_m4a(tmp_path, sample_mp4):
    out = media.extract_audio(sample_mp4, tmp_path / "a")
    assert out == tmp_path / "a.m4a"
    assert ffprobe_streams(out) == (False, True, "aac")
    assert 1.5 < media.probe(out).duration < 2.5


def test_video_without_sound_gives_no_audio(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    silent = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)], check=True)
    assert media.extract_audio(silent, tmp_path / "a") is None
    assert not list(tmp_path.glob("a.*"))


def test_opus_is_converted_for_telegram_player(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    src = tmp_path / "v.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:duration=1",
                    "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "libvpx-vp9", "-c:a", "libopus",
                    "-shortest", str(src)], check=True)
    out = media.extract_audio(src, tmp_path / "a")
    assert out.suffix == ".m4a" and ffprobe_streams(out)[2] == "aac"


def test_audio_metadata_for_telegram():
    from bot.telegram_io import _audio_meta
    from pathlib import Path
    item = DownloadedMedia(Path("v.mp4"), 1, MediaKind.VIDEO, duration=12.4,
                           title="Очень длинное название " * 5, author="someone")
    item.audio = AudioTrack(Path("a.m4a"), 1, 12.4, "tiktok_1.m4a")
    meta = _audio_meta(Platform.TIKTOK, item)
    assert meta["performer"] == "someone" and meta["duration"] == 12
    assert len(meta["title"]) <= 64 and meta["thumbnail"] is None
