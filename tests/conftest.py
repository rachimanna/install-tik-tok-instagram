import subprocess
import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def sample_mp4(tmp_path_factory) -> Path:
    """Настоящий короткий mp4 (видео+звук), сгенерированный ffmpeg."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=360x640:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True)
    return path


@pytest.fixture(scope="session")
def sample_m4a(tmp_path_factory) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg не установлен")
    path = tmp_path_factory.mktemp("media") / "audio.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", "aac", str(path)], check=True)
    return path
