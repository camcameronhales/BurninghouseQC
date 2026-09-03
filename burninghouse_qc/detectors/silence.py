"""Silence / audio dropout detection via FFmpeg's silencedetect filter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import SilenceConfig
from ..ffmpeg_tools import ffmpeg
from ..findings import Finding, Severity

_START = re.compile(r"silence_start:\s*(?P<start>-?[0-9.]+)")
_END = re.compile(
    r"silence_end:\s*(?P<end>-?[0-9.]+)(?:\s*\|\s*silence_duration:\s*(?P<duration>[0-9.]+))?"
)


@dataclass
class SilenceRun:
    start: float
    end: float | None
    duration: float | None

    def resolved_end(self, media_duration: float) -> float:
        if self.end is not None:
            return self.end
        return media_duration

    def resolved_duration(self, media_duration: float) -> float:
        if self.duration is not None:
            return self.duration
        return max(0.0, self.resolved_end(media_duration) - self.start)


def parse_silencedetect(stderr: str) -> list[SilenceRun]:
    """Pair up silence_start / silence_end lines in emission order.

    A trailing silence that runs to the end of the file has a start with no end;
    it is returned with end=None for the caller to resolve against duration.
    """
    runs: list[SilenceRun] = []
    pending: float | None = None
    for line in stderr.splitlines():
        start_match = _START.search(line)
        if start_match:
            if pending is not None:
                runs.append(SilenceRun(start=pending, end=None, duration=None))
            pending = max(0.0, float(start_match.group("start")))
            continue
        end_match = _END.search(line)
        if end_match and pending is not None:
            duration = end_match.group("duration")
            runs.append(
                SilenceRun(
                    start=pending,
                    end=float(end_match.group("end")),
                    duration=float(duration) if duration else None,
                )
            )
            pending = None
    if pending is not None:
        runs.append(SilenceRun(start=pending, end=None, duration=None))
    return runs


# "ignore" maps to None, which drops the finding entirely.
_SEVERITIES: dict[str, Severity | None] = {
    "info": Severity.INFO,
    "review": Severity.REVIEW,
    "fail": Severity.FAIL,
    "ignore": None,
}


def _edge_severity(cfg: SilenceConfig) -> Severity | None:
    key = (cfg.edge_severity or "info").strip().lower()
    return _SEVERITIES.get(key, Severity.INFO) if key in _SEVERITIES else Severity.INFO


def classify(
    run: SilenceRun, media_duration: float, cfg: SilenceConfig
) -> tuple[Severity | None, str]:
    duration = run.resolved_duration(media_duration)
    end = run.resolved_end(media_duration)
    covers_whole_file = (
        media_duration > 0 and run.start <= cfg.edge_grace and end >= media_duration - cfg.edge_grace
    )
    at_edge = run.start <= cfg.edge_grace or (
        media_duration > 0 and end >= media_duration - cfg.edge_grace
    )

    if covers_whole_file:
        return Severity.FAIL, "Audio is silent for the entire duration — no programme audio."
    if duration >= cfg.fail_duration and not at_edge:
        return Severity.FAIL, f"Audio dropout: {duration:.2f}s of silence mid-programme."
    if at_edge:
        return (
            _edge_severity(cfg),
            f"{duration:.2f}s of silence at the head/tail — normal handles.",
        )
    return (
        Severity.REVIEW,
        f"{duration:.2f}s of silence mid-programme — may be an intentional pause.",
    )


def detect(
    path: Path, media_duration: float, has_audio: bool, cfg: SilenceConfig
) -> list[Finding]:
    if not cfg.enabled:
        return []

    if not has_audio:
        mode = (cfg.missing_audio or "review").lower()
        if mode == "ignore":
            return []
        severity = Severity.FAIL if mode == "fail" else Severity.REVIEW
        return [
            Finding(
                detector="silence",
                kind="no_audio_stream",
                severity=severity,
                message="File contains no audio stream at all.",
                confidence=1.0,
            )
        ]

    filter_spec = f"silencedetect=noise={cfg.noise_db}dB:d={cfg.min_duration}"
    proc = ffmpeg(["-i", str(path), "-af", filter_spec, "-vn", "-f", "null", "-"])
    runs = parse_silencedetect(proc.stderr)
    if proc.returncode != 0 and not runs:
        return [
            Finding(
                detector="silence",
                kind="detector_error",
                severity=Severity.REVIEW,
                message="Silence detection could not complete; check the audio manually.",
                detail={"stderr": proc.stderr.strip()[-500:]},
            )
        ]

    findings: list[Finding] = []
    for run in runs:
        severity, message = classify(run, media_duration, cfg)
        if severity is None:
            continue
        findings.append(
            Finding(
                detector="silence",
                kind="silence",
                severity=severity,
                message=message,
                start=run.start,
                end=run.resolved_end(media_duration),
                confidence=1.0 if severity is Severity.FAIL else 0.5,
                detail={"duration": run.resolved_duration(media_duration)},
            )
        )
    return findings
