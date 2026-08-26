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
from .scan import scan_video
from .ffmpeg_tools import FFmpegError, MediaInfo, probe
from .findings import Finding, Severity, Verdict, verdict_for
from .mounts import is_network_path
from .spelling import Speller
from .transfer import FileSnapshot, TransferError, free_space, safe_copy


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
    # What the source looked like when the run started, so the router can tell
    # whether it was rewritten underneath us.
    snapshot: FileSnapshot | None = None

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


def should_stage_locally(source: Path, cfg: Config) -> bool:
    """Whether to pull a network file to local scratch before analysing it.

    The detectors read the file several times over (the black/scene pass, then
    frame extraction). Doing that across a share means several full reads and a
    file handle held open on the server for the length of the job; one copy is
    kinder to everyone.
    """
    if not cfg.routing.work_from_local_copy:
        return False
    try:
        size = source.stat().st_size
    except OSError:
        return False
    if size > cfg.routing.max_local_copy_gb * 1e9:
        return False
    if free_space(cfg.paths.work) < size * 1.2:
        return False
    return is_network_path(source)


def stage_locally(source: Path, workdir: Path, cfg: Config) -> tuple[Path, str | None]:
    """Copy the source to local scratch. Falls back to reading in place."""
    try:
        staged = safe_copy(source, workdir / f"_qc_source{source.suffix}")
    except TransferError as exc:
        return source, f"Could not stage {source.name} locally ({exc}); reading in place."
    return staged, None


def workdir_for(source: Path, cfg: Config) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in source.stem)[:60]
    return cfg.paths.work / f"{stamp}_{safe}"


def run_qc(source: Path, cfg: Config, workdir: Path | None = None) -> QCResult:
    """Run the full QC pipeline. Never raises — failures become findings."""
    started = datetime.now(timezone.utc)
    workdir = workdir or workdir_for(source, cfg)
    workdir.mkdir(parents=True, exist_ok=True)
    snapshot = FileSnapshot.of(source)

    findings: list[Finding] = []
    stats: dict[str, Any] = {}
    media: MediaInfo | None = None
    error: str | None = None

    # Everything below reads `analysis_source`; `source` stays the real file so
    # the report and the router always refer to the original.
    analysis_source = source
    if should_stage_locally(source, cfg):
        analysis_source, stage_warning = stage_locally(source, workdir, cfg)
        stats["staged_locally"] = analysis_source is not source
        if stage_warning:
            stats["staging_note"] = stage_warning

    try:
        media = probe(analysis_source)
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
            snapshot=snapshot,
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

    # Black detection and scene detection share a single decode of the file.
    scan = None
    if media.has_video:
        try:
            scan = scan_video(analysis_source, cfg.black, cfg.text)
        except Exception as exc:  # noqa: BLE001
            findings.append(_detector_crash("video scan", exc))

    if cfg.black.enabled:
        try:
            if not media.has_video:
                pass
            elif scan is None or not scan.black_ok:
                findings.append(black.detector_error(scan.stderr if scan else ""))
            else:
                findings.extend(
                    black.findings_from_runs(scan.black_runs, media.duration, cfg.black)
                )
        except Exception as exc:  # noqa: BLE001 - a broken detector must not sink the job
            findings.append(_detector_crash("black", exc))

    try:
        findings.extend(
            silence.detect(analysis_source, media.duration, media.has_audio, cfg.silence)
        )
    except Exception as exc:  # noqa: BLE001
        findings.append(_detector_crash("silence", exc))

    if cfg.text.enabled:
        try:
            text_findings, text_stats = text.detect(
                analysis_source,
                media.duration,
                media.has_video,
                workdir,
                cfg.text,
                speller,
                scene_times=scan.scene_times if scan and scan.scene_ok else None,
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
        snapshot=snapshot,
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
