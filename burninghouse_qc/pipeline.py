"""Runs every detector over one file and assembles the result."""

from __future__ import annotations

import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .detectors import black, silence, text
from .ffmpeg_tools import FFmpegError, MediaInfo, probe
from .findings import Finding, Severity, Verdict, verdict_for
from .spelling import Speller


@dataclass
class QCResult:
    source: Path
    verdict: Verdict
    findings: list[Finding]
    media: MediaInfo | None
    started_at: datetime
    finished_at: datetime
    stats: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    workdir: Path | None = None

    @property
    def elapsed(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def counts(self) -> dict[str, int]:
        return {
            "fail": sum(1 for f in self.findings if f.severity is Severity.FAIL),
            "review": sum(1 for f in self.findings if f.severity is Severity.REVIEW),
            "info": sum(1 for f in self.findings if f.severity is Severity.INFO),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "filename": self.source.name,
            "verdict": self.verdict.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "elapsed_seconds": round(self.elapsed, 2),
            "counts": self.counts(),
            "error": self.error,
            "media": {
                "duration": self.media.duration,
                "resolution": self.media.resolution,
                "fps": self.media.fps,
                "video_codec": self.media.video_codec,
                "audio_codec": self.media.audio_codec,
                "container": self.media.container,
            }
            if self.media
            else None,
            "stats": self.stats,
            "findings": [f.to_dict() for f in self.findings],
        }


def workdir_for(source: Path, cfg: Config) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in source.stem)[:60]
    return cfg.paths.work / f"{stamp}_{safe}"


def run_qc(source: Path, cfg: Config, workdir: Path | None = None) -> QCResult:
    """Run the full QC pipeline. Never raises — failures become findings."""
    started = datetime.now(timezone.utc)
    workdir = workdir or workdir_for(source, cfg)
    workdir.mkdir(parents=True, exist_ok=True)

    findings: list[Finding] = []
    stats: dict[str, Any] = {}
    media: MediaInfo | None = None
    error: str | None = None

    try:
        media = probe(source)
    except FFmpegError as exc:
        error = str(exc)
        findings.append(
            Finding(
                detector="probe",
                kind="unreadable_file",
                severity=Severity.FAIL,
                message=f"File could not be read by FFmpeg: {exc}",
                confidence=1.0,
            )
        )
        finished = datetime.now(timezone.utc)
        return QCResult(
            source=source,
            verdict=Verdict.FAIL,
            findings=findings,
            media=None,
            started_at=started,
            finished_at=finished,
            stats=stats,
            error=error,
            workdir=workdir,
        )

    if media.duration <= 0:
        findings.append(
            Finding(
                detector="probe",
                kind="zero_duration",
                severity=Severity.FAIL,
                message="File reports zero duration — the render is probably truncated.",
                confidence=1.0,
            )
        )

    base_dir = cfg.source_path.parent if cfg.source_path else Path.cwd()
    speller = Speller(cfg.spelling, base_dir=base_dir)

    detectors: list[tuple[str, Any]] = [
        ("black", lambda: black.detect(source, media.duration, cfg.black)),
        ("silence", lambda: silence.detect(source, media.duration, media.has_audio, cfg.silence)),
    ]
    for name, runner in detectors:
        try:
            findings.extend(runner())
        except Exception as exc:  # noqa: BLE001 - a broken detector must not sink the job
            findings.append(_detector_crash(name, exc))

    if cfg.text.enabled:
        try:
            text_findings, text_stats = text.detect(
                source, media.duration, media.has_video, workdir, cfg.text, speller
            )
            findings.extend(text_findings)
            stats.update(text_stats)
        except Exception as exc:  # noqa: BLE001
            findings.append(_detector_crash("text", exc))

    stats["dictionary"] = str(speller.dictionary_path)
    stats["custom_words"] = len(speller.custom_words)

    findings.sort(key=lambda f: (-int(f.severity), f.start if f.start is not None else 0.0))
    finished = datetime.now(timezone.utc)
    return QCResult(
        source=source,
        verdict=verdict_for(findings),
        findings=findings,
        media=media,
        started_at=started,
        finished_at=finished,
        stats=stats,
        error=error,
        workdir=workdir,
    )


def _detector_crash(name: str, exc: Exception) -> Finding:
    return Finding(
        detector=name,
        kind="detector_crash",
        severity=Severity.REVIEW,
        message=f"The {name} detector failed to run ({exc}); this file was not fully checked.",
        detail={"traceback": traceback.format_exc()[-1500:]},
    )


def cleanup_workdir(workdir: Path | None, keep: bool = False) -> None:
    if keep or workdir is None or not workdir.exists():
        return
    shutil.rmtree(workdir, ignore_errors=True)
