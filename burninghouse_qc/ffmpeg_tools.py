"""Thin wrappers around the ffmpeg / ffprobe binaries.

Deliberately shells out rather than using a binding: FFmpeg's own filter output
is the source of truth for blackdetect/silencedetect, and parsing it keeps the
dependency surface small on a machine that only ever runs this one service.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


class FFmpegMissing(FFmpegError):
    pass


def _binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise FFmpegMissing(
            f"{name} not found on PATH. Install FFmpeg and make sure the service "
            f"account can see it."
        )
    return found


def run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run an ffmpeg-family command, capturing both streams as text."""
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    return proc


def ffmpeg(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return run([_binary("ffmpeg"), "-hide_banner", "-nostdin", *args], timeout=timeout)


def ffprobe(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return run([_binary("ffprobe"), "-hide_banner", *args], timeout=timeout)


_VERSION = re.compile(r"ffmpeg version n?(?P<major>\d+)\.(?P<minor>\d+)")


@functools.lru_cache(maxsize=1)
def version() -> tuple[int, int] | None:
    """(major, minor) of the ffmpeg on PATH, or None if it can't be read."""
    try:
        proc = run([_binary("ffmpeg"), "-version"], timeout=30)
    except (FFmpegMissing, subprocess.SubprocessError, OSError):
        return None
    match = _VERSION.search(proc.stdout or "")
    if not match:
        return None
    return int(match.group("major")), int(match.group("minor"))


@functools.lru_cache(maxsize=1)
def version_string() -> str:
    try:
        proc = run([_binary("ffmpeg"), "-version"], timeout=30)
    except (FFmpegMissing, subprocess.SubprocessError, OSError):
        return "unknown"
    first = (proc.stdout or "").splitlines()
    return first[0] if first else "unknown"


def passthrough_args() -> list[str]:
    """Ask ffmpeg not to duplicate or drop frames to hit a target rate.

    `-vsync 0` was deprecated in 5.1 in favour of `-fps_mode passthrough`, and
    warns loudly on 6.x. Newer builds may drop it entirely, so pick by version
    rather than relying on the old spelling continuing to work.
    """
    detected = version()
    if detected is None or detected >= (5, 1):
        return ["-fps_mode", "passthrough"]
    return ["-vsync", "0"]


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    has_video: bool
    has_audio: bool
    container: str | None = None

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "unknown"


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "N/A"):
        return None
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else None
        except ValueError:
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def probe(path: Path, timeout: float = 120.0) -> MediaInfo:
    """Read stream/format metadata. Raises FFmpegError if the file is unreadable."""
    proc = ffprobe(
        [
            "-loglevel", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {path.name}: {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:  # pragma: no cover - ffprobe always emits JSON
        raise FFmpegError(f"Could not parse ffprobe output for {path.name}") from exc

    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for source in (fmt.get("duration"), (video or {}).get("duration")):
        try:
            duration = float(source)
            break
        except (TypeError, ValueError):
            continue

    return MediaInfo(
        path=path,
        duration=duration,
        width=(video or {}).get("width"),
        height=(video or {}).get("height"),
        fps=_parse_fps((video or {}).get("avg_frame_rate")),
        video_codec=(video or {}).get("codec_name"),
        audio_codec=(audio or {}).get("codec_name"),
        has_video=video is not None,
        has_audio=audio is not None,
        container=fmt.get("format_name"),
    )
