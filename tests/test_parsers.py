"""Detector output parsing and classification — no ffmpeg needed."""

import pytest

from burninghouse_qc.config import BlackConfig, SilenceConfig
from burninghouse_qc.detectors.black import BlackRun, classify as classify_black, parse_blackdetect
from burninghouse_qc.detectors.silence import (
    SilenceRun,
    classify as classify_silence,
    parse_silencedetect,
)
from burninghouse_qc.findings import Severity

BLACK_STDERR = """
frame=  100 fps=0.0 q=-1.0 size=N/A time=00:00:04.00 bitrate=N/A speed= 8x
[blackdetect @ 0x55d1] black_start:9 black_end:11.04 black_duration:2.04
[blackdetect @ 0x55d1] black_start:15.8 black_end:16 black_duration:0.2
"""

SILENCE_STDERR = """
[silencedetect @ 0x1a] silence_start: 4.01043
[silencedetect @ 0x1a] silence_end: 9.00002 | silence_duration: 4.98959
[silencedetect @ 0x1a] silence_start: 15.2
"""


def test_parse_blackdetect_reads_every_run():
    runs = parse_blackdetect(BLACK_STDERR)
    assert [(r.start, r.end) for r in runs] == [(9.0, 11.04), (15.8, 16.0)]
    assert runs[0].duration == pytest.approx(2.04)


def test_parse_blackdetect_ignores_unrelated_output():
    assert parse_blackdetect("frame= 10 fps=0 time=00:00:01.00") == []


def test_parse_silencedetect_pairs_start_and_end():
    runs = parse_silencedetect(SILENCE_STDERR)
    assert len(runs) == 2
    assert runs[0].start == pytest.approx(4.01043)
    assert runs[0].duration == pytest.approx(4.98959)


def test_parse_silencedetect_leaves_trailing_run_open():
    """Silence running to EOF has no silence_end line; end resolves to duration."""
    runs = parse_silencedetect(SILENCE_STDERR)
    trailing = runs[-1]
    assert trailing.end is None
    assert trailing.resolved_end(16.0) == 16.0
    assert trailing.resolved_duration(16.0) == pytest.approx(0.8)


def test_parse_silencedetect_handles_negative_start():
    """ffmpeg can report a tiny negative start on the first sample."""
    runs = parse_silencedetect("[silencedetect] silence_start: -0.001")
    assert runs[0].start == 0.0


class TestBlackClassification:
    cfg = BlackConfig()

    def test_sustained_mid_programme_black_fails(self):
        severity, _ = classify_black(BlackRun(9.0, 11.0, 2.0), 16.0, self.cfg)
        assert severity is Severity.FAIL

    def test_black_at_head_is_informational_only(self):
        """A fade up from black is on nearly every deliverable. Recorded in the
        report, but it must not make the file look borderline."""
        severity, message = classify_black(BlackRun(0.0, 1.2, 1.2), 16.0, self.cfg)
        assert severity is Severity.INFO
        assert "fade" in message

    def test_black_at_tail_is_informational_only(self):
        severity, _ = classify_black(BlackRun(14.9, 16.0, 1.1), 16.0, self.cfg)
        assert severity is Severity.INFO

    @pytest.mark.parametrize(
        "setting,expected",
        [("info", Severity.INFO), ("review", Severity.REVIEW), ("ignore", None)],
    )
    def test_edge_black_severity_is_configurable(self, setting, expected):
        cfg = BlackConfig(edge_severity=setting)
        severity, _ = classify_black(BlackRun(0.0, 1.2, 1.2), 16.0, cfg)
        assert severity is expected

    def test_brief_mid_programme_flash_is_review(self):
        severity, _ = classify_black(BlackRun(7.0, 7.2, 0.2), 16.0, self.cfg)
        assert severity is Severity.REVIEW


class TestSilenceClassification:
    cfg = SilenceConfig()

    def test_long_mid_programme_silence_fails(self):
        severity, _ = classify_silence(SilenceRun(4.0, 9.0, 5.0), 16.0, self.cfg)
        assert severity is Severity.FAIL

    def test_silence_over_whole_file_fails(self):
        severity, message = classify_silence(SilenceRun(0.0, 16.0, 16.0), 16.0, self.cfg)
        assert severity is Severity.FAIL
        assert "entire duration" in message

    def test_head_silence_is_informational_only(self):
        """Handles are on every deliverable — five real interview files each
        produced a tail-silence flag, which is noise, not a finding."""
        severity, _ = classify_silence(SilenceRun(0.0, 3.5, 3.5), 16.0, self.cfg)
        assert severity is Severity.INFO

    def test_tail_handles_do_not_make_a_clean_file_look_borderline(self):
        """Regression: a 126.83s interview with 2.69s of tail silence and
        nothing else wrong came back NEEDS REVIEW."""
        severity, _ = classify_silence(SilenceRun(124.09, 126.78, 2.69), 126.83, self.cfg)
        assert severity is Severity.INFO

    @pytest.mark.parametrize(
        "setting,expected",
        [("info", Severity.INFO), ("review", Severity.REVIEW), ("ignore", None)],
    )
    def test_edge_silence_severity_is_configurable(self, setting, expected):
        cfg = SilenceConfig(edge_severity=setting)
        severity, _ = classify_silence(SilenceRun(0.0, 3.5, 3.5), 16.0, cfg)
        assert severity is expected

    def test_short_mid_silence_is_review(self):
        severity, _ = classify_silence(SilenceRun(6.0, 7.5, 1.5), 16.0, self.cfg)
        assert severity is Severity.REVIEW
