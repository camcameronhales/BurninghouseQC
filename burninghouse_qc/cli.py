"""Command line entry points.

  bhqc scan FILE      QC one file, write a report, don't move anything
  bhqc run FILE       QC one file and route it into pass/review/error
  bhqc watch          Run the unattended folder-watching service
  bhqc init           Write a starter config.toml and create the QC folders
  bhqc status         Print the current service status
  bhqc doctor         Check FFmpeg/Tesseract/dictionary are all reachable
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .findings import Verdict
from .pipeline import cleanup_workdir, run_qc
from .report import write_report
from .router import route
from .spelling import Speller

_VERDICT_MARK = {Verdict.PASS: "PASS", Verdict.REVIEW: "REVIEW", Verdict.FAIL: "FAIL"}
# Exit codes let a scheduled task or CI step branch on the outcome.
_EXIT_CODE = {Verdict.PASS: 0, Verdict.REVIEW: 10, Verdict.FAIL: 20}


def _load(args: argparse.Namespace) -> Config:
    cfg = Config.load(args.config)
    if getattr(args, "root", None):
        root = Path(args.root).expanduser().resolve()
        cfg.paths.root = root
        cfg.paths.input = root / "input"
        cfg.paths.passed = root / "pass"
        cfg.paths.review = root / "review"
        cfg.paths.error = root / "error"
        cfg.paths.work = root / "work"
        cfg.paths.status_file = root / "status.json"
        cfg.paths.log_file = root / "burninghouse-qc.log"
    return cfg


def _print_result(result, report_path: Path | None) -> None:
    counts = result.counts()
    print(f"\n  {result.source.name}")
    print(f"  Verdict: {_VERDICT_MARK[result.verdict]}   "
          f"({counts['fail']} fail, {counts['review']} review)   "
          f"{result.elapsed:.1f}s")
    if result.stats.get("frames_sampled"):
        print(f"  Sampled {result.stats['frames_sampled']} frames "
              f"({result.stats.get('scene_changes', 0)} scene changes)")
    if result.findings:
        print()
        for finding in result.findings:
            print(f"  [{finding.severity.label:>6}] {finding.timecode:<26} {finding.message}")
    if report_path:
        print(f"\n  Report: {report_path}")
    print()


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = _load(args)
    source = Path(args.file).expanduser().resolve()
    if not source.exists():
        print(f"No such file: {source}", file=sys.stderr)
        return 2
    result = run_qc(source, cfg)
    # Deliberately NOT source.parent: scan must never write anything next to a
    # render that may be sitting on a shared server.
    report_dir = (
        Path(args.report_dir).expanduser().resolve()
        if args.report_dir
        else cfg.paths.root / "reports"
    )
    report_path = write_report(result, report_dir, cfg.report)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        _print_result(result, report_path)
    cleanup_workdir(result.workdir, keep=args.keep_work)
    return _EXIT_CODE[result.verdict]


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args)
    cfg.paths.ensure()
    source = Path(args.file).expanduser().resolve()
    if not source.exists():
        print(f"No such file: {source}", file=sys.stderr)
        return 2
    if args.mode:
        cfg.routing.mode = args.mode
    result = run_qc(source, cfg)
    outcome = route(
        result, cfg, move=False if args.no_move else None, source_snapshot=result.snapshot
    )
    _print_result(result, outcome.report)
    described = {
        "left_in_place": f"  Source left untouched: {outcome.destination}",
        "copied": f"  Copied to: {outcome.destination}",
        "moved": f"  Moved to: {outcome.destination}",
    }[outcome.action]
    print(described)
    if outcome.warning:
        print(f"  WARNING: {outcome.warning}")
    print()
    cleanup_workdir(result.workdir, keep=args.keep_work)
    return _EXIT_CODE[result.verdict]


def cmd_watch(args: argparse.Namespace) -> int:
    from .watcher import QCService

    cfg = _load(args)
    cfg.paths.ensure()
    QCService(cfg, keep_work=args.keep_work, verbose=args.verbose).run()
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    cfg = _load(args)
    cfg.paths.ensure()
    target = Path(args.output or "config.toml").expanduser().resolve()
    template = Path(__file__).resolve().parent.parent / "config.example.toml"
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
    elif template.exists():
        shutil.copyfile(template, target)
        print(f"Wrote {target}")
    else:
        print("config.example.toml is missing from the install", file=sys.stderr)
        return 2
    print(f"QC folders ready under {cfg.paths.root}:")
    for label, path in (
        ("input ", cfg.paths.input),
        ("pass  ", cfg.paths.passed),
        ("review", cfg.paths.review),
        ("error ", cfg.paths.error),
    ):
        print(f"  {label}  {path}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if not cfg.paths.status_file.exists():
        print("No status file — the service has not run yet.")
        return 1
    print(cfg.paths.status_file.read_text(encoding="utf-8"))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _load(args)
    problems = 0

    for binary in ("ffmpeg", "ffprobe"):
        found = shutil.which(binary)
        print(f"  {binary:<10} {found or 'NOT FOUND'}")
        problems += 0 if found else 1

    try:
        import pytesseract

        version = pytesseract.get_tesseract_version()
        print(f"  {'tesseract':<10} {version}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {'tesseract':<10} NOT USABLE ({exc})")
        problems += 1

    base_dir = cfg.source_path.parent if cfg.source_path else Path.cwd()
    speller = Speller(cfg.spelling, base_dir=base_dir)
    exists = speller.dictionary_path.exists()
    print(f"  {'dictionary':<10} {speller.dictionary_path} "
          f"({len(speller.custom_words)} custom words{'' if exists else ', FILE MISSING'})")

    mode = (cfg.routing.mode or "report_only").strip().lower()
    consequence = {
        "report_only": "renders are never touched; only the report is written",
        "copy": "the original stays put; a verified copy is filed",
        "move": "renders are RELOCATED out of the input folder",
    }.get(mode, "UNKNOWN MODE — this will fail at routing time")
    print(f"\n  Routing mode: {mode} — {consequence}")
    if mode == "move":
        print("    Only use 'move' on a QC folder this app owns, never on shared storage.")

    print(f"\n  Folders under {cfg.paths.root}:")
    for label, path in (
        ("input", cfg.paths.input),
        ("pass", cfg.paths.passed),
        ("review", cfg.paths.review),
        ("error", cfg.paths.error),
    ):
        print(f"    {label:<7} {path} {'ok' if path.exists() else '(will be created)'}")

    print("\n  All good." if not problems else f"\n  {problems} problem(s) found.")
    return 0 if problems == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bhqc", description="Burninghouse video QC")
    parser.add_argument("--version", action="version", version=f"burninghouse-qc {__version__}")
    parser.add_argument("-c", "--config", default=None, help="Path to config.toml")
    parser.add_argument("--root", default=None, help="Override the QC folder root")
    parser.add_argument("--keep-work", action="store_true",
                        help="Keep extracted frames for threshold tuning")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="QC one file without moving it")
    scan.add_argument("file")
    scan.add_argument("--report-dir", default=None)
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=cmd_scan)

    run_cmd = sub.add_parser("run", help="QC one file and route it")
    run_cmd.add_argument("file")
    run_cmd.add_argument("--no-move", action="store_true",
                         help="Force report_only: write the report, touch nothing")
    run_cmd.add_argument("--mode", choices=["report_only", "copy", "move"], default=None,
                         help="Override routing.mode for this run")
    run_cmd.set_defaults(func=cmd_run)

    watch = sub.add_parser("watch", help="Run the folder-watching service")
    watch.set_defaults(func=cmd_watch)

    init = sub.add_parser("init", help="Create folders and a starter config")
    init.add_argument("-o", "--output", default=None)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="Print the service status file")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="Check dependencies and folders")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
