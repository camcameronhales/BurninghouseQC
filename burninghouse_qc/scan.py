"""One decode pass that feeds several detectors.

Black detection and scene detection each need to walk every frame of the file.
Running them as separate FFmpeg invocations decodes the whole master twice —
on a 10-minute 1080p deliverable that is ~35s of pure duplicated work. Both
filters are chained into a single pass here instead.

`blackdetect` sits ahead of `select` in the chain, so it still sees every frame
even though only scene-change frames survive to the metadata printer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import BlackConfig, TextConfig
from .detectors.black import BlackRun, parse_blackdetect
from .detectors.text import parse_scene_times
from .ffmpeg_tools import ffmpeg


@dataclass
class VideoScan:
    black_runs: list[BlackRun] = field(default_factory=list)
    scene_times: list[float] = field(default_factory=list)
    black_ok: bool = True
    scene_ok: bool = True
    stderr: str = ""


def scan_video(path: Path, black_cfg: BlackConfig, text_cfg: TextConfig) -> VideoScan:
    """Run blackdetect and scene detection together over one decode."""
    want_black = black_cfg.enabled
    want_scenes = text_cfg.enabled
    if not (want_black or want_scenes):
        return VideoScan()

    chain: list[str] = []
    if want_black:
        chain.append(
            f"blackdetect=d={black_cfg.min_duration}"
            f":pix_th={black_cfg.pixel_threshold}"
            f":pic_th={black_cfg.picture_threshold}"
        )
    if want_scenes:
        chain.append(f"select='gt(scene,{text_cfg.scene_threshold})'")
        chain.append("metadata=print:file=-")

    proc = ffmpeg(["-i", str(path), "-vf", ",".join(chain), "-an", "-f", "null", "-"])

    black_runs = parse_blackdetect(proc.stderr) if want_black else []
    scene_times = parse_scene_times(proc.stdout) if want_scenes else []
    failed = proc.returncode != 0 and not black_runs and not scene_times
    return VideoScan(
        black_runs=black_runs,
        scene_times=scene_times,
        black_ok=not (want_black and failed),
        scene_ok=not (want_scenes and failed),
        stderr=proc.stderr,
    )
