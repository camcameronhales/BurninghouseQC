#!/usr/bin/env python3
"""Generate synthetic test footage with known QC faults.

Used by the test suite and handy for smoke-testing thresholds without waiting
on a real render. Produces a clip with:

  * a correctly spelled title card
  * a title card containing a deliberate misspelling
  * a sustained mid-programme black run
  * a mid-programme audio dropout

Usage:
    python scripts/make_sample.py out.mp4 [--clean]
    python scripts/make_sample.py long.mp4 --duration 300 --size 1920x1080

`--duration` repeats the title cards across the whole clip, which is how the
benchmark material for real-world 2-10 minute deliverables is made.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    # macOS — Supplemental is where Arial and friends live on modern versions.
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    "/System/Library/Fonts/Supplemental/Tahoma Bold.ttf",
    "/Library/Fonts/Arial.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # Windows
    "C:/Windows/Fonts/arialbd.ttf",
]

# Searched if none of the above exist. drawtext needs a plain .ttf — a .ttc
# collection needs an index it has no way to pick.
FONT_DIRS = [
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "/usr/share/fonts",
    "/usr/local/share/fonts",
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

# Extra copy cycled through longer clips so a 5-minute benchmark carries a
# realistic amount of on-screen text rather than three cards and silence.
FILLER = [
    "Directed by Sam Whitfield",
    "Shot on location in Melbourne",
    "Grading by the Burninghouse colour suite",
    "A short film about patience",
    "Produced for broadcast delivery",
    "Sound design and final mix",
]


def find_font() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    # Nothing in the known list — take any .ttf we can find.
    for directory in FONT_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for found in sorted(base.rglob("*.ttf")):
            return str(found)
    raise SystemExit(
        "No usable .ttf font found. Pass one explicitly, e.g.\n"
        "  --font '/System/Library/Fonts/Supplemental/Arial.ttf'"
    )


def render_card(text: str, size: tuple[int, int], font_path: str, out: Path) -> Path:
    """Draw a title card as a transparent PNG.

    Text is burnt in with Pillow rather than FFmpeg's drawtext, because
    drawtext needs libfreetype and libharfbuzz compiled into FFmpeg and plenty
    of builds — Homebrew's included — ship without it. Pillow is already a
    dependency of the app, and reads the same system font files.
    """
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    font_size = max(18, round(height * 0.075))
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        font = ImageFont.load_default()

    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = right - left, bottom - top
    x = (width - text_w) // 2 - left
    y = (height - text_h) // 2 - top

    pad = round(font_size * 0.45)
    draw.rectangle(
        [x - pad, y - pad, x + text_w + pad, y + text_h + pad],
        fill=(0, 0, 0, 140),
    )
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    image.save(out)
    return out


def cards_for(duration: float, clean: bool) -> list[tuple[float, float, str]]:
    """Base cards, then filler copy repeated to fill a longer clip."""
    cards = list(CARDS_CLEAN if clean else CARDS_FAULTY)
    start = DURATION
    index = 0
    while start + 4.0 < duration:
        cards.append((start, start + 4.0, FILLER[index % len(FILLER)]))
        start += 8.0
        index += 1
    return cards


def build(out: Path, clean: bool, font: str, duration: float, size: str) -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH.")

    cards = cards_for(duration, clean)
    width, height = (int(part) for part in size.lower().split("x"))

    with tempfile.TemporaryDirectory(prefix="bhqc_cards_") as scratch:
        card_paths = [
            render_card(text, (width, height), font, Path(scratch) / f"card{index:03d}.png")
            for index, (_, _, text) in enumerate(cards)
        ]

        inputs: list[str] = [
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
        ]
        for card in card_paths:
            inputs += ["-i", str(card)]

        # A busy base so scene detection has something to work with, then each
        # card composited over it for its own window.
        filters = ["[0:v]format=yuv420p[base]"]
        chain = "[base]"
        for index, (start, end, _) in enumerate(cards):
            label = f"[t{index}]"
            stream = index + 2          # inputs 0 and 1 are video and audio
            filters.append(
                f"{chain}[{stream}:v]overlay=0:0:enable='between(t,{start},{end})'{label}"
            )
            chain = label

        if not clean:
            filters.append(
                f"{chain}drawbox=x=0:y=0:w=iw:h=ih:color=black@1.0:t=fill:"
                f"enable='between(t,{BLACK_FROM},{BLACK_TO})'[vout]"
            )
        else:
            filters.append(f"{chain}null[vout]")

        if clean:
            filters.append("[1:a]anull[aout]")
        else:
            filters.append(
                f"[1:a]volume=enable='between(t,{SILENT_FROM},{SILENT_TO})':volume=0[aout]"
            )

        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(duration),
            str(out),
        ]
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            raise SystemExit(f"ffmpeg failed ({proc.returncode})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--clean", action="store_true", help="Generate a fault-free clip.")
    parser.add_argument("--font", default=None)
    parser.add_argument("--duration", type=float, default=DURATION,
                        help="Clip length in seconds (default 16).")
    parser.add_argument("--size", default="1280x720", help="e.g. 1920x1080")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build(args.output, args.clean, args.font or find_font(), args.duration, args.size)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
