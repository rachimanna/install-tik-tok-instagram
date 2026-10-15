"""Сведения о видеофайле через ffprobe и превью-кадр. Видео не перекодируется."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ProbeInfo:
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    codec: str | None = None


def probe(path: Path, timeout: int = 30) -> ProbeInfo | None:
    """None — если ffprobe недоступен или не смог прочитать файл."""
    if not shutil.which("ffprobe"):
        return None
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=True).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        log.warning("ffprobe failed: %s", type(exc).__name__)
        return None

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width = height = None
    if video:
        width, height = video.get("width"), video.get("height")
        rotation = _rotation(video)
        if rotation in (90, 270) and width and height:
            width, height = height, width
    duration = None
    for source in (data.get("format") or {}, video or {}):
        try:
            duration = float(source["duration"])
            break
        except (KeyError, TypeError, ValueError):
            continue
    return ProbeInfo(has_video=video is not None, has_audio=audio is not None,
                     width=width, height=height, duration=duration,
                     codec=(video or {}).get("codec_name"))


def _rotation(stream: dict) -> int:
    try:
        tag = (stream.get("tags") or {}).get("rotate")
        if tag is not None:
            return abs(int(tag)) % 360
        for side in stream.get("side_data_list") or []:
            if "rotation" in side:
                return abs(int(side["rotation"])) % 360
    except (TypeError, ValueError):
        pass
    return 0


def make_thumbnail(video: Path, target: Path, timeout: int = 30) -> Path | None:
    """JPEG ≤320 px для превью в Telegram. Сам видеофайл не меняется."""
    if not shutil.which("ffmpeg"):
        return None
    scale = "scale='if(gt(iw,ih),320,-2)':'if(gt(iw,ih),-2,320)'"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", str(video),
           "-frames:v", "1", "-vf", scale, "-q:v", "5", str(target)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    if target.is_file() and 0 < target.stat().st_size <= 200 * 1024:
        return target
    target.unlink(missing_ok=True)
    return None


def human_size(size: int | None) -> str:
    if size is None:
        return "?"
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit in ("Б", "КБ") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} Б"
