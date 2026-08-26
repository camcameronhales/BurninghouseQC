"""Black frame detection via FFmpeg's blackdetect filter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import BlackConfig
from ..ffmpeg_tools import ffmpeg
from ..findings import Finding, Severity

_LINE = re.compile(
    r"black_start:\s*(?P<start>[0-9.]+)\s+black_end:\s*(?P<end>[0-9.]+)\s+"
    r"black_duration:\s*(?P<duration>[0-9.]+)"
)


@dataclass
class BlackRun:
    start: float
    end: float
    duration: float


def parse_blackdetect(stderr: str) -> list[BlackRun]:
    """Pull black runs out of ffmpeg's stderr. Pure function — unit tested."""
    runs: list[BlackRun] = []
    for match in _LINE.finditer(stderr):
        runs.append(
            BlackRun(
                start=float(match.group("start")),
                end=float(match.group("end")),
                duration=float(match.group("duration")),
            )
        )
    return runs


def _touches_edge(run: BlackRun, duration: float, grace: float) -> bool:
    if run.start <= grace:
        return True
    return duration > 0 and run.end >= duration - grace


def classify(run: BlackRun, media_duration: float, cfg: BlackConfig) -> tuple[Severity, str]:
    at_edge = _touches_edge(run, media_duration, cfg.edge_grace)
    sustained = run.duration >= cfg.fail_duration

    if at_edge:
        return (
            Severity.REVIEW,
            f"Black for {run.duration:.2f}s at the head/tail — likely an intentional "
            f"fade, but worth an eye.",
        )
    if sustained:
        return (
            Severity.FAIL,
            f"Sustained black for {run.duration:.2f}s mid-programme.",
        )
    return (
        Severity.REVIEW,
        f"Short black flash of {run.duration:.2f}s mid-programme — could be an "
        f"intentional cut to black.",
    )


def detector_error(stderr: str = "") -> Finding:
    return Finding(
        detector="black",
        kind="detector_error",
        severity=Severity.REVIEW,
        message="Black frame detection could not complete; check the file manually.",
        detail={"stderr": stderr.strip()[-500:]},
    )


def findings_from_runs(
    runs: list[BlackRun], media_duration: float, cfg: BlackConfig
) -> list[Finding]:
    """Turn parsed black runs into findings. The pipeline calls this with runs
    from the shared single-pass scan; `detect` below is the standalone path."""
    findings: list[Finding] = []
    for run in runs:
        severity, message = classify(run, media_duration, cfg)
        findings.append(
            Finding(
                detector="black",
                kind="black_frames",
                severity=severity,
                message=message,
                start=run.start,
                end=run.end,
                confidence=1.0 if severity is Severity.FAIL else 0.5,
                detail={"duration": run.duration},
            )
        )
    return findings


def detect(path: Path, media_duration: float, cfg: BlackConfig) -> list[Finding]:
    """Standalone black detection over its own decode pass.

    The pipeline uses scan.scan_video instead, which shares one pass with scene
    detection. This entry point stays for one-off use and for the tests.
    """
    if not cfg.enabled:
        return []

    filter_spec = (
        f"blackdetect=d={cfg.min_duration}"
        f":pix_th={cfg.pixel_threshold}"
        f":pic_th={cfg.picture_threshold}"
    )
    proc = ffmpeg(["-i", str(path), "-vf", filter_spec, "-an", "-f", "null", "-"])
    # blackdetect writes to stderr; a non-zero exit with usable output still
    # yields findings, so only the empty case is treated as an error.
    runs = parse_blackdetect(proc.stderr)
    if proc.returncode != 0 and not runs:
        return [detector_error(proc.stderr)]
    return findings_from_runs(runs, media_duration, cfg)
