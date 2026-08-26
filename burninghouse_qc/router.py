"""Moves a checked file into pass / review / error, with its report."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .findings import Verdict
from .pipeline import QCResult
from .report import write_report


@dataclass
class RouteOutcome:
    verdict: Verdict
    destination: Path       # where the video ended up
    report: Path            # the HTML report
    moved: bool


def destination_dir(verdict: Verdict, cfg: Config) -> Path:
    return {
        Verdict.PASS: cfg.paths.passed,
        Verdict.REVIEW: cfg.paths.review,
        Verdict.FAIL: cfg.paths.error,
    }[verdict]


def unique_path(target: Path) -> Path:
    """Never overwrite an existing deliverable — suffix instead."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(1, 1000):
        candidate = target.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a free name for {target}")


def route(result: QCResult, cfg: Config, move: bool = True) -> RouteOutcome:
    """Write the report and (optionally) relocate the source file beside it.

    The report is written first: if the move fails, the operator still has an
    explanation sitting in the destination folder.
    """
    target_dir = destination_dir(result.verdict, cfg)
    target_dir.mkdir(parents=True, exist_ok=True)

    final_video = unique_path(target_dir / result.source.name)
    # Report is named from the final video name so the pair always matches.
    report_result = result
    report_path = write_report(report_result, target_dir, cfg.report)
    if final_video.stem != result.source.stem:
        renamed = report_path.with_name(f"{final_video.stem}.qc.html")
        report_path.replace(renamed)
        report_path = renamed
        json_sidecar = target_dir / f"{result.source.stem}.qc.json"
        if json_sidecar.exists():
            json_sidecar.replace(target_dir / f"{final_video.stem}.qc.json")

    moved = False
    if move and result.source.exists():
        shutil.move(str(result.source), str(final_video))
        moved = True

    return RouteOutcome(
        verdict=result.verdict, destination=final_video, report=report_path, moved=moved
    )
