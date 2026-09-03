"""End-to-end pipeline tests against real generated footage.

These are the tests that actually prove the QC works: synthetic clips are
rendered with known faults (a misspelled title card, a sustained black run, an
audio dropout) and the pipeline must find exactly those and nothing else.

Skipped automatically when ffmpeg/tesseract are not installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from burninghouse_qc.config import Config
from burninghouse_qc.findings import Severity, Verdict
from burninghouse_qc.pipeline import run_qc
from burninghouse_qc.router import route

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKE_SAMPLE = REPO_ROOT / "scripts" / "make_sample.py"


def _tools_available() -> bool:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001
        return False
    return True


pytestmark = [
    pytest.mark.ffmpeg,
    pytest.mark.skipif(not _tools_available(), reason="ffmpeg/tesseract not installed"),
]


def _render(destination: Path, clean: bool) -> Path:
    args = [sys.executable, str(MAKE_SAMPLE), str(destination)]
    if clean:
        args.append("--clean")
    subprocess.run(args, check=True, capture_output=True)
    return destination


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    directory = tmp_path_factory.mktemp("media")
    return {
        "faulty": _render(directory / "faulty.mp4", clean=False),
        "clean": _render(directory / "clean.mp4", clean=True),
    }


@pytest.fixture(scope="module")
def faulty_result(media, tmp_path_factory):
    cfg = Config()
    cfg.paths.work = tmp_path_factory.mktemp("work")
    cfg.spelling.custom_dictionary = REPO_ROOT / "dictionary" / "custom_words.txt"
    return run_qc(media["faulty"], cfg)


@pytest.fixture(scope="module")
def clean_result(media, tmp_path_factory):
    cfg = Config()
    cfg.paths.work = tmp_path_factory.mktemp("work")
    cfg.spelling.custom_dictionary = REPO_ROOT / "dictionary" / "custom_words.txt"
    return run_qc(media["clean"], cfg)


# -- the faults we planted must be found ---------------------------------

def test_faulty_clip_fails(faulty_result):
    assert faulty_result.verdict is Verdict.FAIL


def test_finds_the_planted_misspelling(faulty_result):
    misspellings = [f for f in faulty_result.findings if f.kind == "misspelling"]
    words = {f.detail["word"].lower() for f in misspellings}
    assert "acheiving" in words
    flagged = next(f for f in misspellings if f.detail["word"].lower() == "acheiving")
    assert flagged.severity is Severity.FAIL
    assert "achieving" in flagged.detail["suggestions"]


def test_misspelling_finding_carries_a_thumbnail(faulty_result):
    flagged = next(f for f in faulty_result.findings if f.kind == "misspelling")
    assert flagged.thumbnail is not None and Path(flagged.thumbnail).exists()


def test_finds_the_planted_black_run(faulty_result):
    black = [f for f in faulty_result.findings if f.kind == "black_frames"]
    assert black, "the 9-11s black run should have been detected"
    run = max(black, key=lambda f: f.duration or 0)
    assert run.severity is Severity.FAIL
    assert 8.5 <= run.start <= 9.5
    assert run.duration == pytest.approx(2.0, abs=0.3)


def test_finds_the_planted_audio_dropout(faulty_result):
    silences = [f for f in faulty_result.findings if f.kind == "silence"]
    assert silences
    dropout = max(silences, key=lambda f: f.duration or 0)
    assert dropout.severity is Severity.FAIL
    assert 3.5 <= dropout.start <= 4.5


# -- and the clean clip must come back clean -----------------------------

def test_clean_clip_passes_with_no_findings(clean_result):
    """The false-positive guard. Any flag here is a tuning regression."""
    assert clean_result.findings == [], [f.message for f in clean_result.findings]
    assert clean_result.verdict is Verdict.PASS


def test_clean_clip_actually_sampled_frames(clean_result):
    """Guards against 'passed' meaning 'the OCR never ran'."""
    assert clean_result.stats["frames_sampled"] > 0
    assert clean_result.stats["words_read"] > 0


# -- reporting and routing -----------------------------------------------

def test_media_metadata_is_read(faulty_result):
    assert faulty_result.media is not None
    assert faulty_result.media.resolution == "1280x720"
    assert faulty_result.media.duration == pytest.approx(16.0, abs=0.5)
    assert faulty_result.media.has_audio


def test_report_is_self_contained(faulty_result, tmp_path):
    cfg = Config()
    cfg.paths.error = tmp_path / "error"
    outcome = route(faulty_result, cfg, move=False)
    html = outcome.report.read_text(encoding="utf-8")
    assert "Acheiving" in html
    assert "data:image/jpeg;base64," in html, "thumbnails must be embedded, not linked"
    assert "http://" not in html and "https://" not in html


def test_unreadable_file_fails_rather_than_crashing(tmp_path):
    """A truncated or corrupt render must be routed, not raise."""
    broken = tmp_path / "broken.mov"
    broken.write_bytes(b"not really a video")
    cfg = Config()
    cfg.paths.work = tmp_path / "work"
    result = run_qc(broken, cfg)
    assert result.verdict is Verdict.FAIL
    assert any(f.kind == "unreadable_file" for f in result.findings)


# -- ffmpeg version compatibility ----------------------------------------

def test_no_deprecated_flags_reach_ffmpeg(media, tmp_path):
    """FFmpeg deprecates flags between majors and eventually removes them.

    `-vsync 0` warns on 6.x and may be gone in 9.x, and the target machine runs
    9.x. This asserts nothing we pass draws a deprecation notice.
    """
    from burninghouse_qc.ffmpeg_tools import ffmpeg, passthrough_args

    out_dir = tmp_path / "frames"
    out_dir.mkdir()
    proc = ffmpeg([
        "-i", str(media["clean"]),
        "-vf", "fps=1/4",
        *passthrough_args(),
        "-y", str(out_dir / "f_%03d.png"),
    ])
    assert proc.returncode == 0
    # ffmpeg echoes the output path, and pytest names tmp dirs after the test —
    # so ignore any line carrying the path, or this matches its own name.
    complaints = [
        line
        for line in proc.stderr.splitlines()
        if "deprecated" in line.lower() and str(tmp_path) not in line
    ]
    assert complaints == [], complaints
    assert list(out_dir.glob("f_*.png")), "frames should have been written"


def test_sample_generation_needs_no_optional_ffmpeg_filters(tmp_path):
    """The generator must not depend on drawtext: it needs libfreetype and
    libharfbuzz compiled in, and Homebrew's ffmpeg ships without it."""
    generated = _render(tmp_path / "sample.mp4", clean=True)
    assert generated.exists() and generated.stat().st_size > 0

    source = MAKE_SAMPLE.read_text()
    assert "drawtext" not in source, "title cards are rendered with Pillow, not drawtext"
