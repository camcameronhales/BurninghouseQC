"""Filing a checked render: the report is written, the render is not touched.

Two modes, set by `routing.mode`:

  alongside    the default. The report is written next to the render, and
               nothing is moved or sorted. Reports get read whatever the
               verdict, so they belong with the file they describe; the verdict
               is inside the report. This is the only mode that writes into the
               folder being watched.
  report_only  nothing is written beside the render; the report is filed in
               pass/review/error instead. Use this when the watched folder must
               stay untouched.

Neither mode moves, renames or alters the render. Sorting the file into verdict
folders was tried and dropped: the report is read either way, and the decision
about where a finished render belongs is one a person makes, not the QC.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .findings import Verdict
from .pipeline import QCResult
from .report import write_report
from .transfer import FileSnapshot

ALONGSIDE = "alongside"
REPORT_ONLY = "report_only"
VALID_MODES = (ALONGSIDE, REPORT_ONLY)


@dataclass
class RouteOutcome:
    verdict: Verdict
    destination: Path       # always the render, which is never relocated
    report: Path            # the HTML report
    action: str             # "left_in_place" — the only outcome there is
    warning: str | None = None

    @property
    def source_untouched(self) -> bool:
        return self.action == "left_in_place"


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


def resolve_mode(cfg: Config) -> str:
    mode = (cfg.routing.mode or ALONGSIDE).strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            f"routing.mode must be one of {', '.join(VALID_MODES)} — got {mode!r}"
        )
    return mode


def unique_stem(target_dir: Path, stem: str, suffixes: tuple[str, ...]) -> str:
    """A stem for which none of `suffixes` is already taken in target_dir.

    Reports and their deliverable are named from the same stem, so both have to
    be free before it can be used — otherwise a second render of the same name
    overwrites the first one's report.
    """
    for index in range(0, 1000):
        candidate = stem if index == 0 else f"{stem} ({index})"
        if not any((target_dir / f"{candidate}{suffix}").exists() for suffix in suffixes):
            return candidate
    raise FileExistsError(f"Could not find a free name for {stem} in {target_dir}")


def _place_symlink(source: Path, target_dir: Path, stem: str) -> str | None:
    """Best-effort pointer to the original. Never fatal — it is a convenience."""
    link = target_dir / f"{stem}{source.suffix}"
    if link.exists() or link.is_symlink():
        return None
    try:
        os.symlink(source, link)
    except OSError as exc:
        return f"Could not create a shortcut to {source.name}: {exc}"
    return None


def route(
    result: QCResult,
    cfg: Config,
    source_snapshot: FileSnapshot | None = None,
) -> RouteOutcome:
    """File the result. Returns where everything ended up.

    `source_snapshot` is what the file looked like when QC started. If it no
    longer matches, the render was rewritten while we were checking it and the
    report describes a version that no longer exists, so the caller is warned.
    """
    mode = resolve_mode(cfg)
    if mode != ALONGSIDE:
        target_dir = destination_dir(result.verdict, cfg)
        target_dir.mkdir(parents=True, exist_ok=True)

    warning: str | None = None
    if not result.source.exists():
        # Filing a report for a render that is no longer there is still worth
        # doing — it is the only record that the file was ever checked — but
        # the report should not imply the file is sitting where it says.
        warning = f"{result.source.name} disappeared before its report was filed."
    if source_snapshot is not None and result.source.exists():
        if not source_snapshot.matches(FileSnapshot.of(result.source)):
            warning = (
                f"{result.source.name} changed while it was being checked — this "
                f"report describes the earlier version, and the file was left alone."
            )

    if mode == ALONGSIDE:
        # The report lives with the render. Nothing is moved, nothing is sorted.
        target_dir = result.source.parent
        stem = unique_stem(target_dir, result.source.stem, (".qc.html",))
        report_path = write_report(result, target_dir, cfg.report, stem=stem)
        return RouteOutcome(
            verdict=result.verdict,
            destination=result.source,
            report=report_path,
            action="left_in_place",
            warning=warning,
        )

    # report_only: the only other mode. Left as a fall-through rather than a
    # second `if` so that adding a mode without handling it here is a syntax-
    # level mistake, not a silent None.
    stem = unique_stem(
        target_dir, result.source.stem, (".qc.html", result.source.suffix)
    )
    report_path = write_report(result, target_dir, cfg.report, stem=stem)
    if cfg.routing.symlink_in_verdict_folder:
        link_warning = _place_symlink(result.source, target_dir, stem)
        warning = warning or link_warning
    return RouteOutcome(
        verdict=result.verdict,
        destination=result.source,
        report=report_path,
        action="left_in_place",
        warning=warning,
    )
