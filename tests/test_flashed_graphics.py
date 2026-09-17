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
    TextRun,
    build_text_runs,
    find_flashed_graphics,
    flashed_graphic_findings,
    frame_signature,
    is_signature_token,
    refine_brief_runs,
    refine_run_bounds,
    refine_timestamps,
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


# -- what the real WestUrban case actually looked like -------------------

class TestSignatureTokens:
    """A URL is usually the most identifying thing on an end card.

    On the real deliverable the card read "For More Information on fall
    prevention visit worksafe.vic.gov.au". Three sampled frames showed only the
    URL, and because it is not alphabetic it was dropped — leaving those frames
    with an empty signature, which ends a run. The graphic was on screen for
    over four seconds and the detector measured 0.70s.
    """

    def test_a_domain_identifies_a_graphic(self):
        assert is_signature_token("worksafe.vic.gov.au")
        assert is_signature_token("vic.gov.au")

    def test_plain_words_still_count(self):
        assert is_signature_token("prevention")

    def test_ocr_noise_does_not(self):
        for junk in ("w7A4", "a3", "12.34", "=2B3", "——"):
            assert not is_signature_token(junk), junk

    def test_a_url_only_frame_does_not_end_a_run(self):
        frames = [
            frame(136.5, "prevention", "visit"),
            frame(138.0, "worksafe.vic.gov.au"),
            frame(139.5, "worksafe.vic.gov.au"),
        ]
        runs = build_text_runs(frames, TextConfig())
        # Still two runs — the text genuinely changes — but the URL frames form
        # a run of their own rather than vanishing and truncating the first.
        assert [run.frames for run in runs] == [1, 2]
        assert runs[1].span == 1.5


class TestBriefIsMeasuredInSeconds:
    def test_a_graphic_held_longer_than_the_span_is_not_brief(self):
        """Frame count depends on where the grid falls; seconds do not."""
        frames = [frame(10.0 + n * 0.5, "PRODUCT", "LAUNCH") for n in range(6)]
        frames += [frame(14.0), *[frame(20.0 + n * 1.5, "PRODUCT", "LAUNCH") for n in range(4)]]
        cfg = TextConfig(flash_max_span=2.0)
        # The first run is six frames but only 2.5s — the old frame-count rule
        # would never have called it brief, and the span rule correctly does not
        # either, because 2.5s is longer than flash_max_span.
        assert find_flashed_graphics(build_text_runs(frames, cfg), cfg) == []

    def test_a_repeat_of_equal_length_is_not_a_flash(self):
        """The regression that adding the span rule introduced.

        Two appearances of the same length are a repeat, not a flash: with both
        under flash_max_span, either could be read as the brief one.
        """
        frames = [
            frame(10.0, "BURNINGHOUSE"), frame(11.5, "BURNINGHOUSE"),
            frame(13.0),
            frame(40.0, "BURNINGHOUSE"), frame(41.5, "BURNINGHOUSE"),
        ]
        cfg = TextConfig()
        assert find_flashed_graphics(build_text_runs(frames, cfg), cfg) == []

    def test_the_finding_records_both_spans(self):
        frames = [
            frame(132.0, "PRODUCT", "LAUNCH", "2026"),
            frame(133.5),
            *[frame(138.0 + n * 1.5, "PRODUCT", "LAUNCH", "2026") for n in range(3)],
        ]
        cfg = TextConfig()
        runs = build_text_runs(frames, cfg)
        findings = flashed_graphic_findings(frames, runs, cfg)
        assert len(findings) == 1
        assert findings[0].detail["brief_span"] == 0.0
        assert findings[0].detail["proper_span"] == 3.0


class TestOccupiedStretches:
    """The WestUrban end card, as the diagnostic actually found it.

    The card is one graphic that changes its own text: a line of copy until
    02:17.20, then the URL alone until past 02:21. Those share no words, so
    they are two runs, and each is under two seconds on the 1.5s grid — which
    made both look brief. A hallucinated "jail" off a busy frame at 02:15 was a
    third. Only the appearance at 02:12 is actually a flash.
    """

    def westurban_frames(self):
        return [
            frame(129.0), frame(130.5),
            frame(132.0, "fall", "prevention", "visit", "information"),   # the flash
            frame(133.5), frame(134.24),
            frame(135.0, "jail"), frame(135.72, "jail"),                  # OCR noise
            frame(136.5, "fall", "prevention", "visit", "information"),   # the card
            frame(137.2, "fall", "prevention", "visit", "information"),
            frame(138.0, "worksafe.vic.gov.au"),                          # …still up
            frame(139.5, "worksafe.vic.gov.au"),
            frame(141.0, "worksafe.vic.gov.au"),
        ]

    def test_the_card_is_one_occupied_stretch(self):
        runs = build_text_runs(self.westurban_frames(), TextConfig())
        # Still separate runs — the words genuinely differ — but every run from
        # 135.0 onward knows it sits in one unbroken six seconds of occupancy.
        assert [round(run.span, 2) for run in runs] == [0.0, 0.72, 0.7, 3.0]
        assert [round(run.presence_span, 2) for run in runs] == [0.0, 6.0, 6.0, 6.0]

    def test_only_the_flash_counts_as_brief(self):
        cfg = TextConfig()
        runs = build_text_runs(self.westurban_frames(), cfg)
        brief = [run for run in runs if run.presence_span <= cfg.flash_max_span]
        assert [run.start for run in brief] == [132.0]

    def test_the_flash_is_still_caught(self):
        cfg = TextConfig()
        flashes = find_flashed_graphics(build_text_runs(self.westurban_frames(), cfg), cfg)
        assert len(flashes) == 1
        assert flashes[0][0].start == 132.0
        assert flashes[0][1].start == 136.5

    def test_text_returning_inside_one_occupied_stretch_is_not_a_flash(self):
        """The case the occupied stretch actually decides.

        A card shows one line, swaps to another, then brings the first back and
        holds it — and the frame is never empty in between. By words alone that
        is "brief, then properly", and the pairing fires. But nothing was
        flashed on and pulled: the graphic area was occupied throughout, and
        what changed was the card's own content.
        """
        frames = [
            frame(10.0, "fall", "prevention"),
            frame(11.5, "worksafe.vic.gov.au"),
            frame(13.0, "worksafe.vic.gov.au"),
            frame(14.5, "fall", "prevention"),
            frame(16.0, "fall", "prevention"),
            frame(17.5, "fall", "prevention"),
        ]
        cfg = TextConfig()
        runs = build_text_runs(frames, cfg)
        assert runs[0].span == 0.0, "by its own words the first run looks brief"
        assert runs[0].presence_span == 7.5, "but the frame was never clear"
        assert find_flashed_graphics(runs, cfg) == []

    def test_the_same_shape_with_a_clear_frame_is_a_flash(self):
        """The contrast: one empty frame and it is a flash again.

        The graphic really did go away and come back, which is the defect.
        """
        frames = [
            frame(10.0, "fall", "prevention"),
            frame(11.5),
            frame(14.5, "fall", "prevention"),
            frame(16.0, "fall", "prevention"),
        ]
        cfg = TextConfig()
        flashes = find_flashed_graphics(build_text_runs(frames, cfg), cfg)
        assert [(f[0].start, f[1].start) for f in flashes] == [(10.0, 14.5)]


class TestRefiningBriefAppearances:
    """The grid cannot resolve below its own interval.

    A graphic landing on one 1.5s sample is only bounded as "shorter than about
    three seconds" — 0.3s and 1.4s look identical. Flagging graphics *under two
    seconds* needs the edges measured, which means sampling again around the
    candidate rather than everywhere.
    """

    def test_it_reaches_a_grid_step_either_side(self):
        run = TextRun(frozenset({"launch"}), 132.0, 132.0, 1, 132.0, 132.0)
        stamps = refine_timestamps(run, duration=200.0, grid_interval=1.5, refine_interval=0.5)
        assert stamps[0] == 130.5 and stamps[-1] == 133.5

    def test_it_does_not_reach_past_the_file(self):
        run = TextRun(frozenset({"launch"}), 0.2, 0.2, 1, 0.2, 0.2)
        stamps = refine_timestamps(run, duration=1.0, grid_interval=1.5, refine_interval=0.5)
        assert stamps[0] == 0.0 and stamps[-1] <= 1.0

    def test_it_is_skipped_when_switched_off(self):
        run = TextRun(frozenset({"launch"}), 132.0, 132.0, 1, 132.0, 132.0)
        assert refine_timestamps(run, 200.0, 1.5, refine_interval=0.0) == []

    def test_the_edges_come_from_the_dense_frames(self):
        run = TextRun(frozenset({"product", "launch"}), 132.0, 132.0, 1, 132.0, 132.0)
        dense = [
            frame(130.5), frame(131.0),
            frame(131.5, "PRODUCT", "LAUNCH"),
            frame(132.0, "PRODUCT", "LAUNCH"),
            frame(132.5, "PRODUCT", "LAUNCH"),
            frame(133.0), frame(133.5),
        ]
        assert refine_run_bounds(run, dense, TextConfig()) == (131.5, 132.5)

    def test_a_graphic_the_dense_pass_cannot_see_keeps_its_coarse_bounds(self):
        """OCR can read a graphic at one moment and miss it at another. A
        measurement saying it was never there is worse than a vague one."""
        run = TextRun(frozenset({"product", "launch"}), 132.0, 132.0, 1, 132.0, 132.0)
        assert refine_run_bounds(run, [frame(131.5), frame(132.5)], TextConfig()) is None

    def test_refinement_does_not_cost_a_real_flash_its_finding(self):
        """The interaction that made this need care.

        Refining the brief run gives it a real span while the card it pairs
        with still carries the coarse grid's underestimate. Comparing the two
        run spans would make the flash look *longer* than the card and drop the
        finding; comparing occupancy keeps it.
        """
        frames = [
            frame(129.0),
            frame(132.0, "fall", "prevention"),
            frame(133.5),
            frame(136.5, "fall", "prevention"),
            frame(137.2, "fall", "prevention"),
            frame(138.0, "worksafe.vic.gov.au"),
            frame(139.5, "worksafe.vic.gov.au"),
        ]
        cfg = TextConfig()
        runs = build_text_runs(frames, cfg)
        brief = runs[0]
        # What the dense pass would have found: on screen for 0.9s, which is
        # longer than the 0.7s the *card* measures on the coarse grid.
        brief.start, brief.presence_start = 131.6, 131.6
        brief.end, brief.presence_end = 132.5, 132.5
        assert brief.span > runs[1].span, "the trap this guards against"

        flashes = find_flashed_graphics(runs, cfg)
        assert [(f[0].start, f[1].start) for f in flashes] == [(131.6, 136.5)]


class TestTheSecondPassItself:
    """`refine_brief_runs` orchestrates the re-sampling.

    Its parts are tested above; this covers what it does with them, because a
    detector whose pieces all work and whose wiring does not is exactly the
    failure SPEC §7 records.
    """

    def fake_video(self, monkeypatch, visible_from, visible_to):
        """Stand in for extract + OCR: the graphic is up between those times."""
        def extract(path, timestamps, workdir):
            return [SampledFrame(timestamp=t, path=Path(f"/x/t{t}.png")) for t in timestamps]

        def ocr(frame_path, cfg):
            moment = float(str(frame_path).split("/t")[-1][:-4])
            if visible_from <= moment <= visible_to:
                return [
                    OcrWord(text=w, confidence=92.0, box=(0, 0, 10, 10), line=(1, 1, 1))
                    for w in ("PRODUCT", "LAUNCH")
                ]
            return []

        monkeypatch.setattr("burninghouse_qc.detectors.text.extract_frames", extract)
        monkeypatch.setattr("burninghouse_qc.detectors.text.ocr_frame", ocr)

    def test_a_single_sample_becomes_a_measurement(self, monkeypatch, tmp_path):
        self.fake_video(monkeypatch, 131.75, 132.5)
        runs = build_text_runs(
            [frame(130.5), frame(132.0, "PRODUCT", "LAUNCH"), frame(133.5)], TextConfig()
        )
        assert runs[0].span == 0.0, "the grid knows only that it was there at 132.0"

        assert refine_brief_runs(Path("/x.mov"), 200.0, tmp_path, runs, TextConfig()) == 1
        # It finds a start the grid never saw: the graphic was already up at
        # 131.75, a quarter second before the sample that caught it.
        assert (runs[0].start, runs[0].end) == (131.75, 132.5)
        assert runs[0].span == 0.75
        assert runs[0].presence_span == runs[0].span, "it was alone in its stretch"

    def test_a_run_inside_a_longer_stretch_is_left_alone(self, monkeypatch, tmp_path):
        self.fake_video(monkeypatch, 0.0, 999.0)
        runs = build_text_runs(
            [
                frame(136.5, "PRODUCT", "LAUNCH"),
                frame(138.0, "worksafe.vic.gov.au"),
                frame(139.5, "worksafe.vic.gov.au"),
            ],
            TextConfig(),
        )
        assert refine_brief_runs(Path("/x.mov"), 200.0, tmp_path, runs, TextConfig()) == 0

    def test_it_stops_at_the_candidate_ceiling(self, monkeypatch, tmp_path):
        self.fake_video(monkeypatch, 0.0, 999.0)
        # Six separate one-frame appearances, each isolated by an empty frame.
        frames = []
        for n in range(6):
            frames += [frame(10.0 * n, "PRODUCT", "LAUNCH"), frame(10.0 * n + 1.5)]
        runs = build_text_runs(frames, TextConfig())
        assert len(runs) == 6, "six candidates, all of them brief"

        cfg = TextConfig(flash_refine_max=2)
        assert refine_brief_runs(Path("/x.mov"), 200.0, tmp_path, runs, cfg) == 2
        # The rest keep the coarse bounds rather than being silently skipped.
        assert [run.span for run in runs[2:]] == [0.0] * 4

    def test_switching_it_off_does_nothing(self, monkeypatch, tmp_path):
        self.fake_video(monkeypatch, 131.75, 132.5)
        runs = build_text_runs(
            [frame(130.5), frame(132.0, "PRODUCT", "LAUNCH"), frame(133.5)], TextConfig()
        )
        cfg = TextConfig(flash_refine_interval=0.0)
        assert refine_brief_runs(Path("/x.mov"), 200.0, tmp_path, runs, cfg) == 0
        assert runs[0].span == 0.0

    def test_a_candidate_the_dense_pass_misses_is_not_counted_as_refined(
        self, monkeypatch, tmp_path
    ):
        """OCR reading the graphic once and not again is not a measurement.

        The run keeps the coarse bounds, and the count says nothing was
        refined — the caller records that number, so claiming a refinement that
        did not happen would misreport the file's own coverage.
        """
        self.fake_video(monkeypatch, 900.0, 999.0)   # never visible in this window
        runs = build_text_runs(
            [frame(130.5), frame(132.0, "PRODUCT", "LAUNCH"), frame(133.5)], TextConfig()
        )
        assert refine_brief_runs(Path("/x.mov"), 200.0, tmp_path, runs, TextConfig()) == 0
        assert (runs[0].start, runs[0].end) == (132.0, 132.0)
