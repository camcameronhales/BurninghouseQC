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
from ..ffmpeg_tools import ffmpeg
from ..findings import Finding, Severity, format_timecode
from ..spelling import Speller, normalise

_PTS_TIME = re.compile(r"pts_time:(?P<t>[0-9.]+)")


@dataclass
class OcrWord:
    text: str
    confidence: float
    box: tuple[int, int, int, int]  # left, top, width, height


@dataclass
class SampledFrame:
    timestamp: float
    path: Path
    words: list[OcrWord] = field(default_factory=list)


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

def scene_change_times(path: Path, cfg: TextConfig) -> list[float]:
    """Timestamps where FFmpeg reports a scene score above the threshold.

    Graphics almost always arrive on a cut, so these anchor the dense sampling.
    A failure here is non-fatal — we fall back to interval sampling only.
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
    return [float(m.group("t")) for m in _PTS_TIME.finditer(proc.stdout)]


def plan_timestamps(duration: float, scene_times: list[float], cfg: TextConfig) -> list[float]:
    """Merge baseline interval sampling with denser coverage after each cut."""
    if duration <= 0:
        return []

    candidates: list[float] = []
    step = max(0.1, cfg.sample_interval)
    t = 0.0
    while t < duration:
        candidates.append(t)
        t += step

    for scene_t in scene_times:
        for offset in cfg.scene_followup_offsets:
            candidate = scene_t + offset
            if 0.0 <= candidate < duration:
                candidates.append(candidate)

    candidates.sort()
    spaced: list[float] = []
    for candidate in candidates:
        if not spaced or candidate - spaced[-1] >= cfg.min_frame_gap:
            spaced.append(round(candidate, 3))

    if len(spaced) > cfg.max_frames:
        # Thin evenly rather than truncating, so coverage stays spread over the
        # whole programme instead of stopping partway through.
        stride = len(spaced) / cfg.max_frames
        spaced = [spaced[int(i * stride)] for i in range(cfg.max_frames)]
    return spaced


def extract_frames(path: Path, timestamps: list[float], workdir: Path) -> list[SampledFrame]:
    workdir.mkdir(parents=True, exist_ok=True)
    frames: list[SampledFrame] = []
    for index, timestamp in enumerate(timestamps):
        out = workdir / f"frame_{index:05d}_{timestamp:09.3f}.png"
        proc = ffmpeg(
            [
                "-ss", f"{timestamp:.3f}",
                "-i", str(path),
                "-frames:v", "1",
                "-vsync", "0",
                "-y", str(out),
            ]
        )
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            frames.append(SampledFrame(timestamp=timestamp, path=out))
    return frames


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------

def prepare_for_ocr(image: Image.Image, cfg: TextConfig) -> Image.Image:
    """Grayscale + upscale + autocontrast. Small burnt-in type reads far better."""
    prepared = ImageOps.grayscale(image)
    if cfg.ocr_upscale and cfg.ocr_upscale != 1.0:
        size = (
            max(1, int(prepared.width * cfg.ocr_upscale)),
            max(1, int(prepared.height * cfg.ocr_upscale)),
        )
        prepared = prepared.resize(size, Image.LANCZOS)
    return ImageOps.autocontrast(prepared)


def ocr_frame(frame_path: Path, cfg: TextConfig) -> list[OcrWord]:
    with Image.open(frame_path) as image:
        image.load()
        scale = cfg.ocr_upscale or 1.0
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
        words.append(OcrWord(text=text, confidence=confidence, box=box))
    return words


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
        for word in frame.words:
            if word.confidence < cfg.min_confidence:
                continue
            if not speller.is_checkable(word.text, cfg.min_word_length):
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
    return sorted(suspects.values(), key=lambda s: s.first_seen)


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


def detect(
    path: Path,
    duration: float,
    has_video: bool,
    workdir: Path,
    cfg: TextConfig,
    speller: Speller,
) -> tuple[list[Finding], dict]:
    """Returns (findings, stats). Stats feed the report's coverage section."""
    stats = {"frames_sampled": 0, "scene_changes": 0, "words_read": 0}
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

    scene_times = scene_change_times(path, cfg)
    timestamps = plan_timestamps(duration, scene_times, cfg)
    frames = extract_frames(path, timestamps, workdir / "frames")

    for frame in frames:
        frame.words = ocr_frame(frame.path, cfg)

    stats["frames_sampled"] = len(frames)
    stats["scene_changes"] = len(scene_times)
    stats["words_read"] = sum(len(f.words) for f in frames)

    findings: list[Finding] = []
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
