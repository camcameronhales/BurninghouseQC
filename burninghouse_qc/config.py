"""Configuration loading.

All tunables live in a single TOML file so thresholds can be adjusted during
piloting without touching code. `Config.load(None)` returns the built-in
defaults, which are deliberately conservative first guesses (see SPEC.md §6).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class Paths:
    root: Path = Path("qc_root")
    input: Path = Path("qc_root/input")
    passed: Path = Path("qc_root/pass")
    review: Path = Path("qc_root/review")
    error: Path = Path("qc_root/error")
    work: Path = Path("qc_root/work")
    status_file: Path = Path("qc_root/status.json")
    log_file: Path = Path("qc_root/burninghouse-qc.log")

    def ensure(self) -> None:
        for d in (self.input, self.passed, self.review, self.error, self.work):
            d.mkdir(parents=True, exist_ok=True)
        for f in (self.status_file, self.log_file):
            f.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class WatcherConfig:
    # How often the stability poller re-stats a growing file.
    poll_interval: float = 5.0
    # Consecutive identical (size, mtime) samples required before a render is
    # considered finished. 3 x 5s = file must be untouched for ~10s.
    stability_checks: int = 3
    # Give up waiting for a file to settle after this long.
    stability_timeout: float = 6 * 60 * 60
    # Extensions NLEs use for in-progress writes — never picked up.
    ignore_extensions: list[str] = field(
        default_factory=lambda: [".tmp", ".part", ".partial", ".crdownload", ".download"]
    )
    video_extensions: list[str] = field(
        default_factory=lambda: [
            ".mov", ".mp4", ".mxf", ".m4v", ".avi", ".mkv", ".prores", ".r3d", ".webm",
        ]
    )
    # Files starting with these prefixes are skipped (macOS resource forks etc).
    ignore_prefixes: list[str] = field(default_factory=lambda: [".", "~", "_qc_"])


@dataclass
class BlackConfig:
    enabled: bool = True
    # ffmpeg blackdetect params.
    pixel_threshold: float = 0.10
    picture_threshold: float = 0.98
    min_duration: float = 0.10
    # A black run at least this long is a clear-cut fail.
    fail_duration: float = 0.50
    # Black inside this many seconds of the head/tail is usually intentional
    # (fade up / fade out) so it is downgraded to review.
    edge_grace: float = 1.50


@dataclass
class SilenceConfig:
    enabled: bool = True
    noise_db: float = -50.0
    min_duration: float = 1.0
    # A silent run at least this long is a clear-cut audio dropout.
    fail_duration: float = 3.0
    edge_grace: float = 1.50
    # A file with no audio stream at all: "fail" | "review" | "ignore".
    missing_audio: str = "review"


@dataclass
class TextConfig:
    enabled: bool = True
    # Baseline sampling cadence, in seconds.
    sample_interval: float = 2.0
    # ffmpeg scene score above which a cut is assumed — graphics-heavy sections
    # get denser coverage because titles usually arrive on a cut.
    scene_threshold: float = 0.35
    # Extra frames grabbed just after each detected scene change.
    scene_followup_offsets: list[float] = field(default_factory=lambda: [0.15, 0.60, 1.20])
    # Never sample two frames closer together than this.
    min_frame_gap: float = 0.40
    # Hard ceiling on frames per job, to bound runtime on long masters.
    max_frames: int = 400
    tesseract_lang: str = "eng"
    # Tesseract page segmentation mode. 11 = sparse text, best for graphics.
    tesseract_psm: int = 11
    # Word-level OCR confidence (0-100) below which a token is discarded as noise.
    min_confidence: float = 70.0
    # Misspelling at or above this confidence can be a clear-cut fail.
    fail_confidence: float = 85.0
    # A suspect word must appear in at least this many sampled frames before it
    # is treated as a clear-cut fail rather than a review flag.
    fail_min_occurrences: int = 2
    # Tokens shorter than this are ignored (too noisy from OCR).
    min_word_length: int = 4
    # Upscale frames before OCR; small on-screen type reads much better.
    ocr_upscale: float = 2.0


@dataclass
class SpellingConfig:
    language: str = "en"
    custom_dictionary: Path = Path("dictionary/custom_words.txt")
    # Treat these as always-correct regardless of dictionary.
    ignore_all_caps_acronyms: bool = True
    # pyspellchecker's `en` dictionary is US English. With this on, a word is
    # accepted when its British/Australian form maps to a known US word
    # (colour -> color, organise -> organize). See variants.py.
    accept_british_spellings: bool = True


@dataclass
class ReportConfig:
    # Thumbnails are embedded as base64 so a report is a single portable file.
    thumbnail_width: int = 480
    max_thumbnails: int = 60
    # Also drop a machine-readable sidecar next to the HTML.
    write_json: bool = True


@dataclass
class Config:
    paths: Paths = field(default_factory=Paths)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    black: BlackConfig = field(default_factory=BlackConfig)
    silence: SilenceConfig = field(default_factory=SilenceConfig)
    text: TextConfig = field(default_factory=TextConfig)
    spelling: SpellingConfig = field(default_factory=SpellingConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    source_path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        cfg = cls()
        if path is None:
            return cfg
        path = Path(path)
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        _apply(cfg, data, context=path.parent)
        cfg.source_path = path
        return cfg


def _apply(target: Any, data: dict, context: Path) -> None:
    """Overlay a parsed TOML mapping onto a nested dataclass instance."""
    by_name = {f.name: f for f in fields(target)}
    for key, value in data.items():
        if key not in by_name:
            raise ValueError(f"Unknown config key: {key}")
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _apply(current, value, context)
        elif isinstance(current, Path):
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = (context / candidate).resolve()
            setattr(target, key, candidate)
        else:
            setattr(target, key, value)
