#!/usr/bin/env python3
"""Answer the question SPEC §5 turns on: can OCR see that graphic at all?

Run this against the frames a `--keep-work` scan left behind. It OCRs each
frame in a time window and prints three things per frame:

  raw      every word Tesseract returned, at any confidence
  kept     the words that survive the filters `frame_signature` applies
  dropped  the rest, and which filter dropped them

Then it builds the same text runs the flashed-graphic detector builds, prints
each run's *span in seconds* — which the detector computes and never reads —
and reports whether the pairing rule fires.

The point is to separate three failure modes that look identical from the
outside, because each one has a different fix:

  1. OCR returns nothing for the graphic  -> the text-based approach cannot
     work at all, and the answer is pixel-difference on the lower third.
  2. OCR reads it but the filters drop it -> lower `min_confidence` or
     `min_word_length`.
  3. Both halves are read but do not pair -> lower `flash_match`, or drop the
     pairing requirement and flag on span alone.

Usage, from the repo root with the venv active:

    python scripts/diagnose_frames.py "/Users/Shared/BurninghouseQC/qc_root/work/<job>"
    python scripts/diagnose_frames.py <workdir> --from 129 --to 142 -c config.toml
"""

from __future__ import annotations

import argparse
import re
import signal
import sys
from pathlib import Path

# The output is long and meant to be piped into `less` or `head`; without this,
# quitting the pager raises BrokenPipeError instead of just stopping.
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from burninghouse_qc.config import Config
from burninghouse_qc.detectors.text import (
    SampledFrame,
    build_text_runs,
    find_flashed_graphics,
    frame_signature,
    is_signature_token,
    ocr_frame,
    _overlap,
)
from burninghouse_qc.findings import format_timecode
from burninghouse_qc.spelling import normalise

# Frames are named after the moment they came from: t00132.000.png
FRAME_NAME = re.compile(r"^t(?P<t>\d+\.\d+)\.png$")


def frames_dir(workdir: Path) -> Path:
    """Where the sampled frames actually are.

    `text.detect` extracts into `<workdir>/frames`, so a job folder from
    `--keep-work` holds a `frames/` subdirectory rather than the PNGs
    themselves. Accept either, so pointing this at the job folder — the
    obvious thing to do — works.
    """
    nested = workdir / "frames"
    return nested if nested.is_dir() else workdir


def find_frames(workdir: Path, start: float, end: float) -> tuple[list[Path], int]:
    """Frames inside the window, plus how many were in the folder overall."""
    everything = []
    for path in frames_dir(workdir).glob("t*.png"):
        match = FRAME_NAME.match(path.name)
        if match:
            everything.append((float(match.group("t")), path))
    everything.sort()
    inside = [path for timestamp, path in everything if start <= timestamp <= end]
    return inside, len(everything)


def widest_gap(frames, run, cfg) -> float | None:
    """The largest interval between text-bearing frames inside a run.

    `build_text_runs` walks the frames in order and only closes a run when it
    hits one with no text at all. Nothing else bounds a run, so a gap wider
    than the sampling interval means no blank frame was sampled between two
    appearances and they were joined.
    """
    inside = [
        frame.timestamp for frame in frames
        if run.start <= frame.timestamp <= run.end and frame_signature(frame, cfg)
    ]
    if len(inside) < 2:
        return None
    return max(b - a for a, b in zip(inside, inside[1:]))


def why_dropped(word, cfg) -> str | None:
    """Which `frame_signature` filter discards this word, if any.

    The last test calls `is_signature_token` rather than restating it. An
    earlier version inlined the rule, and when the rule changed this went on
    reporting URLs as dropped while the summary above it listed them as kept —
    a diagnostic disagreeing with the thing it is meant to explain.
    """
    clean = normalise(word.text)
    if word.confidence < cfg.min_confidence:
        return f"confidence {word.confidence:.0f} < {cfg.min_confidence:.0f}"
    if len(clean) < cfg.min_word_length:
        return f"{len(clean)} chars < min_word_length {cfg.min_word_length}"
    if not is_signature_token(clean):
        return "neither a word nor a domain"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workdir", type=Path, help="the --keep-work folder holding the frames")
    parser.add_argument("--from", dest="start", type=float, default=129.0, help="window start in seconds (default 129)")
    parser.add_argument("--to", dest="end", type=float, default=142.0, help="window end in seconds (default 142)")
    parser.add_argument("-c", "--config", default=None, help="config.toml to read thresholds from")
    args = parser.parse_args()

    if not args.workdir.is_dir():
        print(f"not a directory: {args.workdir}", file=sys.stderr)
        return 2

    cfg = Config.load(args.config).text
    paths, total = find_frames(args.workdir, args.start, args.end)

    print(f"workdir            {args.workdir}")
    if frames_dir(args.workdir) != args.workdir:
        print(f"frames             {frames_dir(args.workdir)}")
    print(f"window             {args.start:.1f}s - {args.end:.1f}s "
          f"({format_timecode(args.start)} - {format_timecode(args.end)})")
    print(f"frames in window   {len(paths)} of {total} in the folder")
    print(f"thresholds         min_confidence={cfg.min_confidence:.0f} "
          f"min_word_length={cfg.min_word_length} run_match={cfg.run_match} "
          f"flash_match={cfg.flash_match} flash_max_span={cfg.flash_max_span} "
          f"flash_min_proper_frames={cfg.flash_min_proper_frames}")
    print()

    if not paths:
        print("No frames in that window. Widen it with --from/--to, or check that")
        print("the run was given --keep-work. Frames live in <job folder>/frames.")
        return 1

    frames: list[SampledFrame] = []
    any_raw = 0
    any_kept = 0

    for path in paths:
        timestamp = float(FRAME_NAME.match(path.name).group("t"))
        frame = SampledFrame(timestamp=timestamp, path=path)
        frame.words = ocr_frame(path, cfg)
        frames.append(frame)

        kept = sorted(frame_signature(frame, cfg))
        dropped = [(w, why_dropped(w, cfg)) for w in frame.words]
        dropped = [(w, reason) for w, reason in dropped if reason]

        if frame.words:
            any_raw += 1
        if kept:
            any_kept += 1

        print(f"--- {format_timecode(timestamp)}  (t={timestamp:.3f})  {path.name}")
        if not frame.words:
            print("    raw      (nothing — OCR returned no text for this frame)")
        else:
            rendered = ", ".join(f"{w.text!r}@{w.confidence:.0f}" for w in frame.words)
            print(f"    raw      {rendered}")
        print(f"    kept     {', '.join(kept) if kept else '(none)'}")
        for word, reason in dropped:
            print(f"    dropped  {word.text!r} — {reason}")
        print()

    # The runs the flashed-graphic detector would build from these frames.
    runs = build_text_runs(frames, cfg)
    print("=" * 72)
    print(f"TEXT RUNS ({len(runs)})   — span is what a 'under 2 seconds' rule needs")
    print("=" * 72)
    merged = []
    for index, run in enumerate(runs):
        brief = "BRIEF" if run.span <= cfg.flash_max_span else "     "
        print(f"  [{index}] {brief} {format_timecode(run.start)} -> {format_timecode(run.end)}  "
              f"span={run.span:5.2f}s  frames={run.frames}")
        print(f"        words: {', '.join(sorted(run.words)) or '(none)'}")
        # A run only ends when a frame with no text is sampled. If the frames
        # inside this run are further apart than the sampling interval, two
        # separate appearances have been collapsed into one — and a flash that
        # merges with its own proper appearance can never look brief.
        gap = widest_gap(frames, run, cfg)
        if gap is not None and gap > cfg.sample_interval * 1.5:
            merged.append((run, gap))
            print(f"        ^ MERGED: {gap:.2f}s between sampled frames inside this run — "
                  f"likely two appearances, not one")
    print()

    flashes = find_flashed_graphics(runs, cfg)
    print("=" * 72)
    print(f"PAIRING RULE — {len(flashes)} flashed graphic(s) found")
    print("=" * 72)
    for brief, proper in flashes:
        print(f"  brief at {format_timecode(brief.start)} pairs with "
              f"{format_timecode(proper.start)}  "
              f"overlap={_overlap(brief.words, proper.words):.2f}")
    if not flashes:
        # Say *why* it stayed silent: the near-misses are the useful part.
        briefs = [r for r in runs if r.span <= cfg.flash_max_span]
        print(f"  no pair. {len(briefs)} run(s) were brief enough to qualify.")
        for brief in briefs:
            candidates = [
                other for other in runs
                if other is not brief and other.frames >= cfg.flash_min_proper_frames
            ]
            if not candidates:
                print(f"  - brief at {format_timecode(brief.start)}: no run in this window "
                      f"held {cfg.flash_min_proper_frames}+ frames to pair with")
                continue
            best_run = max(candidates, key=lambda other: _overlap(brief.words, other.words))
            best = _overlap(brief.words, best_run.words)
            if best == 0.0:
                print(f"  - brief at {format_timecode(brief.start)}: "
                      f"{len(candidates)} run(s) long enough to pair with, but none shares "
                      f"a single word with it")
            else:
                print(f"  - brief at {format_timecode(brief.start)}: closest is "
                      f"{format_timecode(best_run.start)} at overlap {best:.2f} "
                      f"(needs {cfg.flash_match})")
    print()

    print("=" * 72)
    print("WHAT THIS MEANS")
    print("=" * 72)
    if any_raw == 0:
        print("  OCR returned NOTHING anywhere in this window. A text-based detector")
        print("  cannot see this graphic, and no threshold change will help. The")
        print("  approach has to move to pixel-difference over the lower third.")
    elif any_kept == 0:
        print(f"  OCR read text in {any_raw} frame(s), but the filters dropped all of it.")
        print("  Look at the 'dropped' lines above: the reason there tells you whether")
        print("  to lower min_confidence or min_word_length.")
    elif not flashes and merged:
        print(f"  OCR read the text, but {len(merged)} run(s) merged appearances that are")
        print("  seconds apart, because no blank frame was sampled between them. A flash")
        print("  joined to its own proper appearance can never look brief, so the pairing")
        print("  rule cannot fire. Denser sampling would separate them.")
    elif not flashes:
        print(f"  OCR read the graphic and {any_kept} frame(s) survived the filters, so the")
        print("  text is there. The pairing rule is what is staying silent — see the")
        print("  near-misses above. Flagging on span alone would not need a pair.")
    else:
        print("  The detector fires on these frames. If the real run did not flag it,")
        print("  the difference is the frames sampled, not the rule.")
    print()
    print("  Spans above are measured on the sampled grid, so they are only as precise")
    print(f"  as the sampling interval ({cfg.sample_interval}s). A graphic shorter than that")
    print("  can fall between two samples and appear here as a single frame, or not at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
