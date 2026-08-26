#!/usr/bin/env python3
"""Generate synthetic test footage with known QC faults.

Used by the test suite and handy for smoke-testing thresholds without waiting
on a real render. Produces a clip with:

  * a correctly spelled title card
  * a title card containing a deliberate misspelling
  * a sustained mid-programme black run
  * a mid-programme audio dropout

Usage:  python scripts/make_sample.py out.mp4 [--clean]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

CARDS_FAULTY = [
    (0.0, 4.0, "PROFESSIONAL COLOUR GRADING"),
    (4.5, 8.5, "Acheiving the Perfect Shot"),   # "Achieving" misspelled
    (12.0, 16.0, "Burninghouse Studios"),
]
CARDS_CLEAN = [
    (0.0, 4.0, "PROFESSIONAL COLOUR GRADING"),
    (4.5, 8.5, "Achieving the Perfect Shot"),
    (12.0, 16.0, "Burninghouse Studios"),
]

DURATION = 16.0
BLACK_FROM, BLACK_TO = 9.0, 11.0      # sustained mid-programme black
SILENT_FROM, SILENT_TO = 4.0, 9.0     # mid-programme audio dropout


def find_font() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise SystemExit("No usable TTF font found; pass one via --font.")


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build(out: Path, clean: bool, font: str) -> None:
    cards = CARDS_CLEAN if clean else CARDS_FAULTY
    filters = [
        # A mid-grey base with a moving element so scene detection has something
        # to work with, then the title cards burnt in.
        "[0:v]format=yuv420p[base]",
    ]
    chain = "[base]"
    for index, (start, end, text) in enumerate(cards):
        label = f"[t{index}]"
        filters.append(
            f"{chain}drawtext=fontfile='{font}':text='{escape(text)}':"
            f"fontcolor=white:fontsize=54:x=(w-text_w)/2:y=(h-text_h)/2:"
            f"box=1:boxcolor=black@0.55:boxborderw=24:"
            f"enable='between(t,{start},{end})'{label}"
        )
        chain = label
    if not clean:
        filters.append(
            f"{chain}drawbox=x=0:y=0:w=iw:h=ih:color=black@1.0:t=fill:"
            f"enable='between(t,{BLACK_FROM},{BLACK_TO})'[vout]"
        )
    else:
        filters.append(f"{chain}null[vout]")

    audio_filter = "[1:a]anull[aout]"
    if not clean:
        audio_filter = (
            f"[1:a]volume=enable='between(t,{SILENT_FROM},{SILENT_TO})':volume=0[aout]"
        )
    filters.append(audio_filter)

    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={DURATION}",
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(DURATION),
        str(out),
    ]
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH.")
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"ffmpeg failed ({proc.returncode})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--clean", action="store_true", help="Generate a fault-free clip.")
    parser.add_argument("--font", default=None)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build(args.output, args.clean, args.font or find_font())
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
