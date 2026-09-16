"""Graphics that appear when they should not.

The real case this exists for: on a client deliverable a super popped on at the
wrong moment, was pulled, and landed properly a couple of shots later. Nothing
in the app looked for that — the text was spelled correctly, there was no black
and no silence, so it passed clean.

Built with constructed frames rather than rendered video: the logic under test
is the text timeline, and generated clips proved an unreliable way to assert
anything about timing.
"""

from pathlib import Path

import pytest

from burninghouse_qc.config import TextConfig
from burninghouse_qc.detectors.text import (
    OcrWord,
    SampledFrame,
    build_text_runs,
    find_flashed_graphics,
    frame_signature,
)


def frame(timestamp: float, *words: str, confidence: float = 92.0) -> SampledFrame:
    sampled = SampledFrame(timestamp=timestamp, path=Path(f"/x/t{timestamp}.png"))
    sampled.words = [
        OcrWord(text=w, confidence=confidence, box=(0, 0, 10, 10), line=(1, 1, 1))
        for w in words
    ]
    return sampled


class TestFrameSignature:
    def test_it_keeps_correctly_spelled_words(self):
        """A graphic flashing on at the wrong moment is a mistake whether or
        not its text is spelled right."""
        sig = frame_signature(frame(0.0, "PRODUCT", "LAUNCH"), TextConfig())
        assert sig == frozenset({"product", "launch"})

    def test_it_drops_low_confidence_noise(self):
        cfg = TextConfig()
        sig = frame_signature(frame(0.0, "PRODUCT", confidence=40.0), cfg)
        assert sig == frozenset()

    def test_it_drops_short_tokens_and_non_words(self):
        sig = frame_signature(frame(0.0, "a", "of", "L1GHT", "LAUNCH"), TextConfig())
        assert sig == frozenset({"launch"})


class TestTextRuns:
    def test_consecutive_frames_of_one_graphic_become_one_run(self):
        frames = [frame(t, "PRODUCT", "LAUNCH") for t in (10.0, 11.5, 13.0)]
        runs = build_text_runs(frames, TextConfig())
        assert len(runs) == 1
        assert runs[0].frames == 3
        assert runs[0].start == 10.0 and runs[0].end == 13.0

    def test_a_gap_with_no_text_splits_the_runs(self):
        frames = [frame(10.0, "PRODUCT", "LAUNCH"), frame(11.5), frame(13.0, "PRODUCT", "LAUNCH")]
        assert len(build_text_runs(frames, TextConfig())) == 2

    def test_different_graphics_are_different_runs(self):
        frames = [frame(10.0, "PRODUCT", "LAUNCH"), frame(11.5, "CLOSING", "REMARKS")]
        assert len(build_text_runs(frames, TextConfig())) == 2


class TestFlashedGraphics:
    def test_the_real_case_is_caught(self):
        """One brief appearance, then the proper one a few shots later."""
        frames = [
            frame(129.0),
            frame(132.0, "PRODUCT", "LAUNCH", "2026"),      # the mistake
            frame(133.5),
            frame(135.0),
            frame(138.0, "PRODUCT", "LAUNCH", "2026"),      # proper
            frame(139.5, "PRODUCT", "LAUNCH", "2026"),
            frame(141.0, "PRODUCT", "LAUNCH", "2026"),
        ]
        cfg = TextConfig()
        flashes = find_flashed_graphics(build_text_runs(frames, cfg), cfg)

        assert len(flashes) == 1
        brief, proper = flashes[0]
        assert brief.start == 132.0 and brief.frames == 1
        assert proper.start == 138.0 and proper.frames == 3

    def test_a_graphic_that_only_appears_properly_is_not_flagged(self):
        frames = [frame(t, "PRODUCT", "LAUNCH") for t in (10.0, 11.5, 13.0)]
        cfg = TextConfig()
        assert find_flashed_graphics(build_text_runs(frames, cfg), cfg) == []

    def test_a_single_brief_graphic_with_no_repeat_is_not_flagged(self):
        """Could just be sampling luck on a short super — not evidence of a
        mistake on its own."""
        frames = [frame(10.0), frame(11.5, "PRODUCT", "LAUNCH"), frame(13.0)]
        cfg = TextConfig()
        assert find_flashed_graphics(build_text_runs(frames, cfg), cfg) == []

    def test_two_different_graphics_are_not_a_flash(self):
        frames = [
            frame(10.0, "OPENING", "TITLE"),
            frame(13.0),
            frame(20.0, "CLOSING", "REMARKS"),
            frame(21.5, "CLOSING", "REMARKS"),
        ]
        cfg = TextConfig()
        assert find_flashed_graphics(build_text_runs(frames, cfg), cfg) == []

    def test_a_recurring_super_shown_properly_twice_is_not_flagged(self):
        """A brand tag that legitimately appears twice, held both times."""
        frames = [
            frame(10.0, "BURNINGHOUSE", "STUDIOS"),
            frame(11.5, "BURNINGHOUSE", "STUDIOS"),
            frame(13.0),
            frame(40.0, "BURNINGHOUSE", "STUDIOS"),
            frame(41.5, "BURNINGHOUSE", "STUDIOS"),
        ]
        cfg = TextConfig()
        assert find_flashed_graphics(build_text_runs(frames, cfg), cfg) == []

    def test_it_can_be_turned_off(self):
        cfg = TextConfig(detect_flashed_graphics=False)
        assert cfg.detect_flashed_graphics is False


class TestReportingTheFinding:
    """The first version built its findings inline in `detect`, referencing a
    list that was created later in the function. Because the loop only runs
    when something is actually found, every test passed and the code crashed on
    the first real detection — taking the whole text detector down with it, so
    spell-checking was skipped too. These tests exercise the reporting path,
    not just the decision to report.
    """

    def _findings(self, frames, cfg=None):
        from burninghouse_qc.detectors.text import (
            build_text_runs,
            flashed_graphic_findings,
        )

        cfg = cfg or TextConfig()
        return flashed_graphic_findings(frames, build_text_runs(frames, cfg), cfg)

    def test_a_finding_is_actually_built(self):
        frames = [
            frame(132.0, "PRODUCT", "LAUNCH", "2026"),
            frame(133.5),
            frame(138.0, "PRODUCT", "LAUNCH", "2026"),
            frame(139.5, "PRODUCT", "LAUNCH", "2026"),
        ]
        findings = self._findings(frames)

        assert len(findings) == 1
        finding = findings[0]
        assert finding.kind == "flashed_graphic"
        assert finding.start == 132.0

    def test_the_message_names_both_moments(self):
        """What the operator needs is where to look, twice."""
        frames = [
            frame(132.0, "PRODUCT", "LAUNCH"),
            frame(133.5),
            frame(138.0, "PRODUCT", "LAUNCH"),
            frame(139.5, "PRODUCT", "LAUNCH"),
        ]
        message = self._findings(frames)[0].message
        assert "00:02:12.00" in message
        assert "00:02:18.00" in message


def test_the_whole_finding_survives_serialisation():
    """The report and the JSON sidecar both go through to_dict."""
    from burninghouse_qc.detectors.text import build_text_runs, flashed_graphic_findings

    cfg = TextConfig()
    frames = [
        frame(132.0, "PRODUCT", "LAUNCH"),
        frame(133.5),
        frame(138.0, "PRODUCT", "LAUNCH"),
        frame(139.5, "PRODUCT", "LAUNCH"),
    ]
    finding = flashed_graphic_findings(frames, build_text_runs(frames, cfg), cfg)[0]
    data = finding.to_dict()
    assert data["kind"] == "flashed_graphic"
    assert data["severity"] == "review"
    assert data["detail"]["proper_appearance"] == "00:02:18.00"
