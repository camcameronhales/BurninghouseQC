"""Command line entry points.

  bhqc scan FILE      QC one file, write a report, don't move anything
  bhqc run FILE       QC one file and route it into pass/review/error
  bhqc watch          Run the unattended folder-watching service
  bhqc init           Write a starter config.toml and create the QC folders
  bhqc status         Print the current service status
  bhqc doctor         Check FFmpeg/Tesseract/dictionary are all reachable
  bhqc check-access   Prove the QC account's permissions are what you think
  bhqc forget FILE    Clear a file from the ledger so it is checked again
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .access import Status, run_all
from .config import Config
from .ledger import Ledger
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


def _absolute_paths_block(cfg: Config) -> str:
    """Render the [paths] section with absolute paths.

    A launchd service does not inherit your shell's working directory, so
    relative paths in a config are a reliable way to have it silently watch the
    wrong folder. init always writes absolute ones.
    """
    entries = [
        ("root", cfg.paths.root),
        ("input", cfg.paths.input),
        ("passed", cfg.paths.passed),
        ("review", cfg.paths.review),
        ("error", cfg.paths.error),
        ("work", cfg.paths.work),
        ("status_file", cfg.paths.status_file),
        ("log_file", cfg.paths.log_file),
        ("ledger_file", cfg.paths.ledger_file),
    ]
    width = max(len(name) for name, _ in entries)
    lines = ["[paths]"]
    lines += [f"{name.ljust(width)} = \"{path}\"" for name, path in entries]
    return "\n".join(lines)


def _install_dictionary(target_dir: Path) -> Path:
    """Give the install its own copy of the custom word list.

    Keeping it outside the repo means `git pull` can never overwrite the brand
    and client names someone has added.
    """
    destination = target_dir / "dictionary" / "custom_words.txt"
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    bundled = Path(__file__).resolve().parent.parent / "dictionary" / "custom_words.txt"
    if bundled.exists():
        shutil.copyfile(bundled, destination)
    else:  # pragma: no cover - only if the install is incomplete
        destination.write_text(
            "# Burninghouse QC custom dictionary — one word per line.\n", encoding="utf-8"
        )
    return destination


def _render_config(template_text: str, cfg: Config, dictionary: Path) -> str:
    """Swap the template's [paths] section for absolute ones."""
    lines = template_text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "[paths]")
    except StopIteration:
        return _absolute_paths_block(cfg) + "\n\n" + template_text
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("["):
        end += 1
    # Keep the comment block that sits above [paths].
    rendered = "\n".join(lines[:start] + [_absolute_paths_block(cfg), ""] + lines[end:]) + "\n"
    # The dictionary path is resolved relative to the config file, which breaks
    # the moment the config lives somewhere other than the repo. Make it absolute.
    return rendered.replace(
        'custom_dictionary        = "dictionary/custom_words.txt"',
        f'custom_dictionary        = "{dictionary}"',
    )


def cmd_init(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if not getattr(args, "root", None):
        # Default the QC root next to the config being written, resolved.
        root = Path(args.output or "config.toml").expanduser().resolve().parent / "qc_root"
        cfg.paths.root = root
        cfg.paths.input = root / "input"
        cfg.paths.passed = root / "pass"
        cfg.paths.review = root / "review"
        cfg.paths.error = root / "error"
        cfg.paths.work = root / "work"
        cfg.paths.status_file = root / "status.json"
        cfg.paths.log_file = root / "burninghouse-qc.log"
        cfg.paths.ledger_file = root / "processed.json"
    cfg.paths.ensure()

    target = Path(args.output or "config.toml").expanduser().resolve()
    template = Path(__file__).resolve().parent.parent / "config.example.toml"
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
    elif template.exists():
        dictionary = _install_dictionary(target.parent)
        target.write_text(
            _render_config(template.read_text(encoding="utf-8"), cfg, dictionary),
            encoding="utf-8",
        )
        print(f"Wrote {target}")
        print(f"Wrote {dictionary}  (add brand and client names here)")
    else:
        print("config.example.toml is missing from the install", file=sys.stderr)
        return 2

    print(f"\nQC folders ready under {cfg.paths.root}:")
    for label, path in (
        ("input ", cfg.paths.input),
        ("pass  ", cfg.paths.passed),
        ("review", cfg.paths.review),
        ("error ", cfg.paths.error),
    ):
        print(f"  {label}  {path}")
    print(f"\nDrop renders into {cfg.paths.input}")
    print(f"Next:  bhqc -c {target} doctor")
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    """Clear the ledger so a file gets checked again — the tuning loop needs it."""
    cfg = _load(args)
    ledger = Ledger(cfg.paths.ledger_file)
    if args.all:
        if cfg.paths.ledger_file.exists():
            cfg.paths.ledger_file.unlink()
            print(f"Cleared {cfg.paths.ledger_file} — everything will be checked again.")
        else:
            print("Nothing recorded yet.")
        return 0
    if not args.file:
        print("Give a file, or --all to clear the whole ledger.", file=sys.stderr)
        return 2
    path = Path(args.file).expanduser().resolve()
    if not ledger.seen(path):
        print(f"{path.name} was not in the ledger (it will be checked next time anyway).")
        return 0
    ledger.forget(path)
    print(f"{path.name} will be checked again.")
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

    if shutil.which("ffmpeg"):
        from .ffmpeg_tools import REQUIRED_FILTERS, missing_filters, version, version_string

        detected = version()
        print(f"  {'version':<10} {version_string()}")
        if detected and detected < (4, 3):
            print("             WARNING: 4.3+ is needed for the filters this uses.")

        absent = missing_filters()
        if absent:
            print(f"  {'filters':<10} MISSING: {', '.join(absent)}")
            print("             This FFmpeg build cannot run the QC pipeline.")
            print("             Reinstall a full build:  brew reinstall ffmpeg")
            problems += 1
        else:
            print(f"  {'filters':<10} all {len(REQUIRED_FILTERS)} required filters present")

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
    print("    Run 'bhqc check-access' to verify the account's permissions.")

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


_MARK = {
    Status.OK: "  ok  ",
    Status.WARN: " warn ",
    Status.FAIL: " FAIL ",
    Status.SKIPPED: " skip ",
}


def cmd_check_access(args: argparse.Namespace) -> int:
    """Verify what the QC account can and cannot do, by actually trying it."""
    cfg = _load(args)

    print("\n  Checking what this account can do.")
    if not args.no_write_probe:
        print("  A zero-byte probe file is created and immediately removed in each")
        print("  folder — it is how a read-only share is confirmed to be read-only.")
    print()

    checks = run_all(cfg, write_probe=not args.no_write_probe)
    for check in checks:
        print(f"  [{_MARK[check.status]}] {check.name}")
        print(f"           {check.detail}")
        if check.advice:
            for line in _wrap(check.advice, 68):
                print(f"           {line}")
        print()

    failures = [c for c in checks if c.status is Status.FAIL]
    warnings = [c for c in checks if c.status is Status.WARN]

    if failures:
        print(f"  {len(failures)} problem(s) must be fixed before this will run.\n")
        return 1
    if warnings:
        print(f"  Usable, with {len(warnings)} thing(s) worth tightening.\n")
        return 0

    read_only_input = any(
        c.name == "input folder is read-only" and c.status is Status.OK for c in checks
    )
    if read_only_input:
        print("  All good — read-only on the renders, full access to its own folders.\n")
    else:
        print("  All good — the input folder and the QC folders are all usable.\n")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


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

    access = sub.add_parser(
        "check-access",
        help="Verify the QC account is read-only on the renders and writable on its own folders",
    )
    access.add_argument(
        "--no-write-probe",
        action="store_true",
        help="Do not attempt a test write in the input folder (skips the read-only check)",
    )
    access.set_defaults(func=cmd_check_access)

    forget = sub.add_parser(
        "forget", help="Clear a file (or everything) from the ledger so it is re-checked"
    )
    forget.add_argument("file", nargs="?", default=None)
    forget.add_argument("--all", action="store_true", help="Clear the whole ledger")
    forget.set_defaults(func=cmd_forget)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
