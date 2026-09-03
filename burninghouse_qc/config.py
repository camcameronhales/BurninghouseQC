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
    # Record of files already QC'd, so report_only mode does not re-check the
    # whole input folder every time the service restarts.
    ledger_file: Path = Path("qc_root/processed.json")

    def ensure(self, verdict_folders: bool = True) -> None:
        """Create the folders the app needs.

        The pass/review/error folders are only made when a routing mode
        actually files things into them — in "alongside" they would sit empty
        forever, which is exactly the clutter that mode exists to avoid.
        """
        needed = [self.input, self.work]
        if verdict_folders:
            needed += [self.passed, self.review, self.error]
        for directory in needed:
            directory.mkdir(parents=True, exist_ok=True)
        for file in (self.status_file, self.log_file):
            file.parent.mkdir(parents=True, exist_ok=True)


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
    # House deliverables are mp4 and mov. Anything FFmpeg can decode will work
    # if you add its extension here.
    video_extensions: list[str] = field(default_factory=lambda: [".mov", ".mp4"])
    # Files starting with these prefixes are skipped (.DS_Store, macOS
    # AppleDouble resource forks, our own scratch naming).
    ignore_prefixes: list[str] = field(default_factory=lambda: [".", "~", "_qc_"])
    # "auto" | "true" | "false". macOS delivers events via FSEvents, which does
    # not fire for SMB/AFP shares, so a network input folder needs polling.
    # "auto" detects the mount type and picks for you.
    use_polling: str = "auto"
    # How often the polling observer re-scans, when it is in use.
    polling_interval: float = 5.0
    # Hold a power assertion (caffeinate) while a job runs, so an idle edit
    # machine cannot fall asleep mid-QC. macOS only; ignored elsewhere.
    prevent_sleep: bool = True


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
    # (fade up / fade out).
    edge_grace: float = 1.50
    # What to do with black at the head or tail: "info" records it in the
    # report without affecting the verdict, "review" routes the file for a
    # human look, "ignore" drops it. Fades are on nearly every deliverable, so
    # flagging them for review makes every file look borderline.
    edge_severity: str = "info"


@dataclass
class SilenceConfig:
    enabled: bool = True
    noise_db: float = -50.0
    min_duration: float = 1.0
    # A silent run at least this long is a clear-cut audio dropout.
    fail_duration: float = 3.0
    edge_grace: float = 1.50
    # Handles at the head and tail are on nearly every deliverable. "info"
    # records them without affecting the verdict; "review" or "ignore" also
    # available.
    edge_severity: str = "info"
    # A file with no audio stream at all: "fail" | "review" | "ignore".
    missing_audio: str = "review"


@dataclass
class TextConfig:
    enabled: bool = True
    # Baseline sampling cadence, in seconds. At 1.5s any card held for 3s or
    # more is sampled at least twice, which is what `fail_min_occurrences`
    # needs before a misspelling can fail a file rather than route to review.
    sample_interval: float = 1.5
    # ffmpeg scene score above which a cut is assumed — graphics-heavy sections
    # get denser coverage because titles usually arrive on a cut.
    scene_threshold: float = 0.35
    # Extra frames grabbed just after each detected scene change.
    scene_followup_offsets: list[float] = field(default_factory=lambda: [0.15, 0.60, 1.20])
    # Never sample two frames closer together than this.
    min_frame_gap: float = 0.40
    # Hard ceiling on frames per job, to bound runtime on long masters. 600
    # lets a 10-minute deliverable keep the full 1.5s cadence; longer clips
    # widen the interval rather than losing coverage at the end.
    max_frames: int = 600
    tesseract_lang: str = "eng"
    # Tesseract page segmentation mode. 11 = sparse text, best for graphics.
    tesseract_psm: int = 11
    # Word-level OCR confidence (0-100) below which a token is discarded as noise.
    min_confidence: float = 70.0
    # Misspelling at or above this confidence can be a clear-cut fail.
    fail_confidence: float = 85.0
    # A suspect word must appear in at least this many sampled frames before it
    # is reported at all. Titles animate on, and a frame caught mid-wipe reads
    # the half-revealed super as a word — "nson" from "Branson", "offic" from
    # "office". Those fragments exist for a single frame; a real super holds
    # for seconds and is sampled repeatedly. Set to 1 to see everything, at the
    # cost of a flag on most animated lower thirds.
    report_min_occurrences: int = 2
    # ...and at least this many before it is a clear-cut fail rather than review.
    fail_min_occurrences: int = 2
    # Tokens shorter than this are ignored (too noisy from OCR).
    min_word_length: int = 4
    # Frames are rescaled to roughly this height before OCR. Small burnt-in
    # type reads far better upscaled, but upscaling an already-large frame just
    # burns time: at 2x a 1080p frame costs ~0.9s to OCR, at this setting ~0.5s
    # for the same result. 720p gets 2.0x, 1080p ~1.33x, 1440p and up are left
    # alone (never downscaled — that would shrink small type below what
    # Tesseract can read).
    ocr_target_height: int = 1440
    ocr_min_scale: float = 1.0
    ocr_max_scale: float = 3.0


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
    # Only check tokens whose capitalisation looks like a real word: lowercase,
    # Title Case or ALL CAPS. A token like "gOLOUR" is a misread character, not
    # a misspelling — people do not change case halfway through a word. This is
    # a significant false-positive control on stylised graphics.
    require_normal_case: bool = True
    # Skip Title-case words sitting next to another Title-case word — almost
    # always a name in a lower third. A spell-checker cannot validate a
    # surname, so flagging one is noise on every interview ever shot.
    skip_proper_nouns: bool = True


@dataclass
class RoutingConfig:
    """What the app does with the render and where the report goes.

    "alongside" is the default: reports are read whatever the verdict, so they
    belong next to the file they describe rather than filed away in one of
    three folders. The render itself is never moved.
    """

    # "alongside"   — write the report next to the render. No sorting folders,
    #                 no file movement. The verdict is inside the report.
    # "report_only" — leave the render, file the report in pass/review/error.
    # "copy"        — leave the original, put a verified copy in the verdict
    #                 folder.
    # "move"        — relocate the render into the verdict folder.
    mode: str = "alongside"
    # For the verdict-folder modes: drop a symlink beside the report pointing at
    # the original. Irrelevant in "alongside", where they are already together.
    symlink_in_verdict_folder: bool = False
    # Checksum a copy against its source before trusting it. Doubles the read
    # cost of a copy; worth it if the app is ever set to "move".
    verify_hash: bool = False
    # Copy the render to local scratch and analyse that instead of reading it
    # over the network several times. One network read instead of ~3, and the
    # server is not left with a file handle open for minutes at a time.
    work_from_local_copy: bool = True
    # Skip local staging if the file is larger than this (GB); it falls back to
    # reading in place.
    max_local_copy_gb: float = 25.0


@dataclass
class ReportConfig:
    # Thumbnails are embedded as base64 so a report is a single portable file.
    thumbnail_width: int = 480
    max_thumbnails: int = 60
    # A machine-readable sidecar next to the HTML. Off by default — it is
    # clutter next to a render unless something is actually consuming it.
    write_json: bool = False
    # Put the verdict in the report's filename ("Spot [FAIL].qc.html") so a
    # folder listing can be triaged without opening anything.
    verdict_in_filename: bool = False


@dataclass
class Config:
    paths: Paths = field(default_factory=Paths)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)
    black: BlackConfig = field(default_factory=BlackConfig)
    silence: SilenceConfig = field(default_factory=SilenceConfig)
    text: TextConfig = field(default_factory=TextConfig)
    spelling: SpellingConfig = field(default_factory=SpellingConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    source_path: Path | None = None

    def uses_verdict_folders(self) -> bool:
        return (self.routing.mode or "alongside").strip().lower() != "alongside"

    def ensure_paths(self) -> None:
        self.paths.ensure(verdict_folders=self.uses_verdict_folders())

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
