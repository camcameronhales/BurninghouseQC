"""On-screen text QC: frame sampling -> OCR -> spell-check.

This is the highest-risk detector for false positives, so it is built to be
tunable and to explain itself: every flag records the OCR confidence, how many
sampled frames the word appeared in, and a thumbnail with the word boxed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytesseract
from PIL import Image, ImageDraw, ImageOps
from pytesseract import Output

from ..config import TextConfig
from ..ffmpeg_tools import ffmpeg, passthrough_args
from ..findings import Finding, Severity, format_timecode
from ..spelling import Speller, looks_like_proper_noun, normalise

_PTS_TIME = re.compile(r"pts_time:(?P<t>[0-9.]+)")


@dataclass
class OcrWord:
    text: str
    confidence: float
    box: tuple[int, int, int, int]  # left, top, width, height
    # Which text line Tesseract put this word on, so neighbours can be found.
    line: tuple[int, int, int] = (0, 0, 0)


@dataclass
class SampledFrame:
    timestamp: float
    path: Path
    words: list[OcrWord] = field(default_factory=list)


@dataclass
class TextRun:
    """A stretch of sampled frames showing substantially the same on-screen text."""

    words: frozenset[str]
    start: float
    end: float
    frames: int
    # The unbroken stretch of frames with *any* text that this run sits inside.
    # A card whose text changes while it stays up — a sentence replaced by a
    # URL, say — is several runs but one occupied stretch, and how long the
    # frame was occupied is the honest answer to "how long was that graphic on
    # screen". Set by `build_text_runs`; zero on a run built by hand.
    presence_start: float = 0.0
    presence_end: float = 0.0

    @property
    def span(self) -> float:
        return self.end - self.start

    @property
    def presence_span(self) -> float:
        return self.presence_end - self.presence_start


@dataclass
class SuspectWord:
    word: str
    occurrences: int
    best_confidence: float
    first_seen: float
    last_seen: float
    best_frame: SampledFrame
    best_box: tuple[int, int, int, int]


# --------------------------------------------------------------------------
# Frame planning
# --------------------------------------------------------------------------

def parse_scene_times(stdout: str) -> list[float]:
    """Pull scene-change timestamps out of the metadata printer's output."""
    return [float(m.group("t")) for m in _PTS_TIME.finditer(stdout)]


def scene_change_times(path: Path, cfg: TextConfig) -> list[float]:
    """Standalone scene detection over its own decode pass.

    The pipeline gets these from scan.scan_video, which shares one pass with
    black detection. This entry point stays for one-off use and for the tests.
    """
    proc = ffmpeg(
        [
            "-i", str(path),
            "-vf", f"select='gt(scene,{cfg.scene_threshold})',metadata=print:file=-",
            "-an", "-f", "null", "-",
        ]
    )
    if proc.returncode != 0 and not proc.stdout:
        return []
    return parse_scene_times(proc.stdout)


def effective_interval(duration: float, cfg: TextConfig) -> float:
    """The sampling interval actually used, widened to respect max_frames.

    Widening the interval rather than thinning a too-long list afterwards keeps
    the grid evenly spaced, which is what lets the whole baseline be pulled in
    a single FFmpeg pass.
    """
    if duration <= 0:
        return max(0.1, cfg.sample_interval)
    # Leave room in the budget for the extra frames taken around scene changes.
    budget = max(1, int(cfg.max_frames * 0.8))
    return max(0.1, cfg.sample_interval, duration / budget)


def plan_timestamps(duration: float, scene_times: list[float], cfg: TextConfig) -> list[float]:
    """The full sampling plan: the baseline grid plus post-cut follow-ups."""
    if duration <= 0:
        return []
    grid = grid_timestamps(duration, cfg)
    followups = followup_timestamps(duration, scene_times, grid, cfg)
    merged = sorted(grid + followups)
    return _space_out(merged, cfg.min_frame_gap)[: cfg.max_frames]


def grid_timestamps(duration: float, cfg: TextConfig) -> list[float]:
    """Evenly spaced baseline samples covering the whole programme."""
    if duration <= 0:
        return []
    step = effective_interval(duration, cfg)
    count = int(duration / step) + 1
    return [round(i * step, 3) for i in range(count) if i * step < duration]


def followup_timestamps(
    duration: float, scene_times: list[float], grid: list[float], cfg: TextConfig
) -> list[float]:
    """Extra samples just after each cut, where supers tend to animate on."""
    if duration <= 0 or not scene_times:
        return []
    budget = max(0, cfg.max_frames - len(grid))
    candidates: list[float] = []
    for scene_t in scene_times:
        for offset in cfg.scene_followup_offsets:
            candidate = round(scene_t + offset, 3)
            if 0.0 <= candidate < duration:
                candidates.append(candidate)
    candidates.sort()
    # Drop any that a grid sample already covers, then spread what is left
    # evenly across the clip so a burst of cuts early on cannot eat the budget.
    wanted = [t for t in candidates if _gap_to_nearest(t, grid) >= cfg.min_frame_gap]
    wanted = _space_out(wanted, cfg.min_frame_gap)
    if len(wanted) > budget:
        stride = len(wanted) / budget if budget else 0
        wanted = [wanted[int(i * stride)] for i in range(budget)]
    return wanted


def _gap_to_nearest(value: float, sorted_values: list[float]) -> float:
    if not sorted_values:
        return float("inf")
    import bisect

    index = bisect.bisect_left(sorted_values, value)
    gaps = []
    if index < len(sorted_values):
        gaps.append(abs(sorted_values[index] - value))
    if index:
        gaps.append(abs(value - sorted_values[index - 1]))
    return min(gaps)


def _space_out(values: list[float], min_gap: float) -> list[float]:
    spaced: list[float] = []
    for value in values:
        if not spaced or value - spaced[-1] >= min_gap:
            spaced.append(value)
    return spaced


def extract_grid(path: Path, duration: float, workdir: Path, cfg: TextConfig) -> list[SampledFrame]:
    """Pull the whole baseline grid in ONE decode pass via the fps filter.

    Seeking to each timestamp separately costs ~0.4s per frame on a 1080p
    master; a single pass gets the same frames for roughly the price of one
    decode. Output frame *i* corresponds to *i x interval* seconds.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    step = effective_interval(duration, cfg)
    pattern = workdir / "grid_%05d.png"   # renamed below to carry the timestamp
    proc = ffmpeg(
        [
            "-i", str(path),
            "-vf", f"fps=1/{step}",
            *passthrough_args(),
            "-y", str(pattern),
        ]
    )
    frames: list[SampledFrame] = []
    for index, frame_path in enumerate(sorted(workdir.glob("grid_*.png"))):
        if frame_path.stat().st_size == 0:
            continue
        timestamp = round(index * step, 3)
        # Name the file after the moment it came from. With --keep-work this is
        # what makes the folder answerable: "what did QC see at 02:12?"
        named = frame_path.with_name(f"t{timestamp:09.3f}.png")
        try:
            frame_path.replace(named)
        except OSError:
            named = frame_path
        frames.append(SampledFrame(timestamp=timestamp, path=named))
    if proc.returncode != 0 and not frames:
        return []
    return frames


def extract_frames(path: Path, timestamps: list[float], workdir: Path) -> list[SampledFrame]:
    """Seek-and-grab individual frames. Used for the handful of follow-up
    samples around cuts, which do not sit on the baseline grid."""
    workdir.mkdir(parents=True, exist_ok=True)
    frames: list[SampledFrame] = []
    for index, timestamp in enumerate(timestamps):
        out = workdir / f"t{timestamp:09.3f}.png"
        proc = ffmpeg(
            [
                "-ss", f"{timestamp:.3f}",
                "-i", str(path),
                "-frames:v", "1",
                *passthrough_args(),
                "-y", str(out),
            ]
        )
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            frames.append(SampledFrame(timestamp=timestamp, path=out))
    return frames


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------

def ocr_scale(height: int, cfg: TextConfig) -> float:
    """How much to rescale a frame of this height before handing it to OCR."""
    if height <= 0:
        return 1.0
    scale = cfg.ocr_target_height / height
    return max(cfg.ocr_min_scale, min(cfg.ocr_max_scale, scale))


def prepare_for_ocr(image: Image.Image, cfg: TextConfig) -> Image.Image:
    """Grayscale + rescale + autocontrast. Small burnt-in type reads far better."""
    prepared = ImageOps.grayscale(image)
    scale = ocr_scale(prepared.height, cfg)
    if scale != 1.0:
        size = (max(1, round(prepared.width * scale)), max(1, round(prepared.height * scale)))
        prepared = prepared.resize(size, Image.LANCZOS)
    return ImageOps.autocontrast(prepared)


def ocr_frame(frame_path: Path, cfg: TextConfig) -> list[OcrWord]:
    with Image.open(frame_path) as image:
        image.load()
        scale = ocr_scale(image.height, cfg)
        prepared = prepare_for_ocr(image, cfg)
        data = pytesseract.image_to_data(
            prepared,
            lang=cfg.tesseract_lang,
            config=f"--psm {cfg.tesseract_psm}",
            output_type=Output.DICT,
        )

    words: list[OcrWord] = []
    for i, raw in enumerate(data["text"]):
        text = (raw or "").strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if confidence < 0:
            continue
        # Boxes come back in upscaled coordinates; map them back to the frame.
        box = (
            int(data["left"][i] / scale),
            int(data["top"][i] / scale),
            int(data["width"][i] / scale),
            int(data["height"][i] / scale),
        )
        words.append(
            OcrWord(
                text=text,
                confidence=confidence,
                box=box,
                line=(data["block_num"][i], data["par_num"][i], data["line_num"][i]),
            )
        )
    return words


def neighbours_on_line(words: list[OcrWord], index: int) -> list[str]:
    """The words either side of words[index] on the same text line."""
    target = words[index]
    found: list[str] = []
    for step in (-1, 1):
        position = index + step
        if 0 <= position < len(words) and words[position].line == target.line:
            found.append(words[position].text)
    return found


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def collect_suspects(
    frames: list[SampledFrame], speller: Speller, cfg: TextConfig
) -> list[SuspectWord]:
    """Group misspelled tokens across frames so one bad super is one finding."""
    suspects: dict[str, SuspectWord] = {}
    for frame in frames:
        seen_this_frame: set[str] = set()
        for index, word in enumerate(frame.words):
            if word.confidence < cfg.min_confidence:
                continue
            if not speller.is_checkable(word.text, cfg.min_word_length):
                continue
            if speller.cfg.skip_proper_nouns and looks_like_proper_noun(
                word.text, neighbours_on_line(frame.words, index)
            ):
                continue
            if not speller.is_misspelled(word.text):
                continue
            key = normalise(word.text).lower()
            if key in seen_this_frame:
                continue
            seen_this_frame.add(key)

            existing = suspects.get(key)
            if existing is None:
                suspects[key] = SuspectWord(
                    word=normalise(word.text),
                    occurrences=1,
                    best_confidence=word.confidence,
                    first_seen=frame.timestamp,
                    last_seen=frame.timestamp,
                    best_frame=frame,
                    best_box=word.box,
                )
            else:
                existing.occurrences += 1
                existing.last_seen = frame.timestamp
                if word.confidence > existing.best_confidence:
                    existing.best_confidence = word.confidence
                    existing.best_frame = frame
                    existing.best_box = word.box
                    existing.word = normalise(word.text)
    return sorted(
        (s for s in suspects.values() if s.occurrences >= cfg.report_min_occurrences),
        key=lambda s: s.first_seen,
    )


# --------------------------------------------------------------------------
# Graphics appearing when they should not
# --------------------------------------------------------------------------

# A URL or domain is usually the most identifying thing on an end card, and it
# is not alphabetic — so it was dropped from the signature, which left frames
# showing only the URL with an *empty* signature. An empty signature ends a run,
# so one graphic became two and its span collapsed to a fraction of the time it
# was really on screen. Matched after `normalise`, which has already stripped
# any leading and trailing punctuation.
_DOMAIN_LIKE = re.compile(r"^[a-z0-9][a-z0-9._\-/:]*\.[a-z0-9][a-z0-9._\-/]*$", re.IGNORECASE)


def is_signature_token(word: str) -> bool:
    """Whether a normalised token helps identify what is on screen.

    Looser than the spell-check gate, because this decides "is the same graphic
    still up" rather than "is this a real word" — but not so loose that OCR
    noise off a busy frame starts forming runs of its own, which is why a
    non-alphabetic token has to look like a domain and carry real letters.
    """
    if word.isalpha():
        return True
    if not _DOMAIN_LIKE.match(word):
        return False
    return sum(character.isalpha() for character in word) >= 3


def frame_signature(frame: SampledFrame, cfg: TextConfig) -> frozenset[str]:
    """The words on screen in this frame, as something comparable between frames.

    Deliberately includes correctly-spelled words: a graphic flashing on at the
    wrong moment is a mistake regardless of whether its text is spelled right.
    """
    words = {
        normalise(word.text).lower()
        for word in frame.words
        if word.confidence >= cfg.min_confidence
        and len(normalise(word.text)) >= cfg.min_word_length
        and is_signature_token(normalise(word.text))
    }
    return frozenset(words)


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def build_text_runs(frames: list[SampledFrame], cfg: TextConfig) -> list[TextRun]:
    """Collapse consecutive frames showing the same text into runs.

    Runs that are not separated by a frame with no text at all belong to one
    *occupied stretch*, and every run in it records that stretch's bounds. Two
    runs sit in one stretch when the text changed but something stayed on
    screen — which is what a card animating from a line of copy to a URL looks
    like, and is indistinguishable from two graphics by their words alone.
    """
    runs: list[TextRun] = []
    current: TextRun | None = None
    occupied: list[TextRun] = []

    def close_occupied() -> None:
        if not occupied:
            return
        start, end = occupied[0].start, occupied[-1].end
        for run in occupied:
            run.presence_start, run.presence_end = start, end
        occupied.clear()

    for frame in frames:
        signature = frame_signature(frame, cfg)
        if not signature:
            current = None
            close_occupied()
            continue
        if current is not None and _overlap(current.words, signature) >= cfg.run_match:
            current.words = current.words | signature
            current.end = frame.timestamp
            current.frames += 1
        else:
            current = TextRun(signature, frame.timestamp, frame.timestamp, 1)
            runs.append(current)
            occupied.append(current)
    close_occupied()
    return runs


def find_flashed_graphics(
    runs: list[TextRun], cfg: TextConfig
) -> list[tuple[TextRun, TextRun]]:
    """Graphics that appear briefly, vanish, then appear properly later.

    The signature of the real-world mistake this exists for: a super pops on at
    the wrong moment, is pulled, and lands properly a few shots later. Either
    appearance alone is unremarkable — a brief one could be sampling luck, a
    long one is just a super. It is the pairing that makes it a defect.
    """
    flashes: list[tuple[TextRun, TextRun]] = []
    for index, brief in enumerate(runs):
        # Measured seconds, not a frame count: how many frames a graphic lands
        # on depends on where the sampling grid falls, while how long it was up
        # is the thing that makes it a mistake. Measured over the whole occupied
        # stretch, so a card that changes its own text midway is not mistaken
        # for two brief graphics.
        if brief.presence_span > cfg.flash_max_span:
            continue
        for other in runs[index + 1:]:
            if other.frames < cfg.flash_min_proper_frames:
                continue
            # The proper appearance has to be held *longer* than the brief one.
            # Without this, a brand tag shown twice for the same length pairs
            # with itself: both are under flash_max_span, so either could be
            # read as the flash. "Briefly, then properly" means one is shorter.
            if other.span <= brief.span:
                continue
            if _overlap(brief.words, other.words) >= cfg.flash_match:
                flashes.append((brief, other))
                break
    return flashes


def flashed_graphic_findings(
    frames: list[SampledFrame], runs: list[TextRun], cfg: TextConfig
) -> list[Finding]:
    """Turn flashed-graphic pairs into findings.

    Separate from `detect` so it can be exercised without rendering video —
    the first version of this built its findings inline, and because the loop
    only runs when something is actually found, every test passed while the
    code crashed on the first real detection.
    """
    findings: list[Finding] = []
    for brief, proper in find_flashed_graphics(runs, cfg):
        shown = ", ".join(sorted(brief.words & proper.words)[:5])
        findings.append(
            Finding(
                detector="text",
                kind="flashed_graphic",
                severity=Severity.REVIEW,
                message=(
                    f"A graphic appears briefly at {format_timecode(brief.start)}, "
                    f"then again properly at {format_timecode(proper.start)} — "
                    f"looks like it was flashed on at the wrong moment."
                ),
                start=brief.start,
                end=brief.end,
                confidence=0.6,
                detail={
                    "text": shown,
                    "proper_appearance": format_timecode(proper.start),
                    "brief_span": round(brief.span, 2),
                    "brief_frames": brief.frames,
                    "proper_span": round(proper.span, 2),
                    "proper_frames": proper.frames,
                },
                thumbnail=_first_frame_path(frames, brief.start),
            )
        )
    return findings


def classify(suspect: SuspectWord, cfg: TextConfig) -> tuple[Severity, str]:
    confident_read = suspect.best_confidence >= cfg.fail_confidence
    repeated = suspect.occurrences >= cfg.fail_min_occurrences
    if confident_read and repeated:
        return (
            Severity.FAIL,
            f'Misspelling on screen: "{suspect.word}" '
            f"(read at {suspect.best_confidence:.0f}% confidence in "
            f"{suspect.occurrences} sampled frames).",
        )
    if confident_read:
        return (
            Severity.REVIEW,
            f'Possible misspelling: "{suspect.word}" read clearly '
            f"({suspect.best_confidence:.0f}%) but only in one sampled frame.",
        )
    return (
        Severity.REVIEW,
        f'Uncertain OCR read of "{suspect.word}" '
        f"({suspect.best_confidence:.0f}% confidence) — check the frame.",
    )


def annotate_thumbnail(suspect: SuspectWord, workdir: Path) -> Path | None:
    """Copy the best frame with a box drawn round the offending word."""
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(suspect.best_frame.path) as image:
            annotated = image.convert("RGB")
            draw = ImageDraw.Draw(annotated)
            left, top, width, height = suspect.best_box
            pad = max(3, int(min(annotated.width, annotated.height) * 0.005))
            draw.rectangle(
                [left - pad, top - pad, left + width + pad, top + height + pad],
                outline=(255, 45, 45),
                width=max(2, int(min(annotated.width, annotated.height) * 0.004)),
            )
            out = workdir / f"flag_{normalise(suspect.word).lower()}_{suspect.first_seen:.3f}.png"
            annotated.save(out)
            return out
    except OSError:
        return None


def _first_frame_path(frames: list[SampledFrame], timestamp: float) -> Path | None:
    for frame in frames:
        if abs(frame.timestamp - timestamp) < 1e-6:
            return frame.path
    return None


def detect(
    path: Path,
    duration: float,
    has_video: bool,
    workdir: Path,
    cfg: TextConfig,
    speller: Speller,
    scene_times: list[float] | None = None,
) -> tuple[list[Finding], dict]:
    """Returns (findings, stats). Stats feed the report's coverage section.

    `scene_times` comes from the shared single-pass scan when the pipeline
    calls this; passing None makes it run its own scene detection.
    """
    stats = {"frames_sampled": 0, "scene_changes": 0, "words_read": 0, "sample_interval": None}
    if not cfg.enabled:
        return [], stats
    if not has_video:
        return (
            [
                Finding(
                    detector="text",
                    kind="no_video_stream",
                    severity=Severity.REVIEW,
                    message="No video stream — on-screen text could not be checked.",
                    confidence=1.0,
                )
            ],
            stats,
        )

    if scene_times is None:
        scene_times = scene_change_times(path, cfg)

    # The evenly spaced baseline comes out of one decode pass; only the handful
    # of post-cut follow-ups need individual seeks.
    frames = extract_grid(path, duration, workdir / "frames", cfg)
    grid_stamps = [f.timestamp for f in frames]
    followups = followup_timestamps(duration, scene_times, grid_stamps, cfg)
    frames += extract_frames(path, followups, workdir / "frames")
    frames.sort(key=lambda f: f.timestamp)
    frames = frames[: cfg.max_frames]

    for frame in frames:
        frame.words = ocr_frame(frame.path, cfg)

    stats["frames_sampled"] = len(frames)
    stats["scene_changes"] = len(scene_times)
    stats["words_read"] = sum(len(f.words) for f in frames)
    stats["sample_interval"] = round(effective_interval(duration, cfg), 3)

    findings: list[Finding] = []

    if cfg.detect_flashed_graphics:
        runs = build_text_runs(frames, cfg)
        stats["text_runs"] = len(runs)
        findings.extend(flashed_graphic_findings(frames, runs, cfg))

    for suspect in collect_suspects(frames, speller, cfg):
        severity, message = classify(suspect, cfg)
        thumbnail = annotate_thumbnail(suspect, workdir / "thumbnails")
        findings.append(
            Finding(
                detector="text",
                kind="misspelling",
                severity=severity,
                message=message,
                start=suspect.first_seen,
                end=suspect.last_seen if suspect.last_seen > suspect.first_seen else None,
                confidence=round(suspect.best_confidence / 100.0, 3),
                detail={
                    "word": suspect.word,
                    "suggestions": speller.suggestions(suspect.word),
                    "occurrences": suspect.occurrences,
                    "ocr_confidence": round(suspect.best_confidence, 1),
                    "first_seen_tc": format_timecode(suspect.first_seen),
                },
                thumbnail=thumbnail,
            )
        )
    return findings, stats
