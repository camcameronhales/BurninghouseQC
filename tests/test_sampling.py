"""Frame sampling plan — the thoroughness/runtime trade-off from SPEC.md §6."""

import pytest

from burninghouse_qc.config import TextConfig
from burninghouse_qc.detectors.text import (
    effective_interval,
    ocr_scale,
    plan_timestamps,
)


def test_baseline_interval_covers_the_whole_clip():
    cfg = TextConfig(sample_interval=2.0, scene_followup_offsets=[])
    stamps = plan_timestamps(10.0, [], cfg)
    assert stamps == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_scene_changes_add_denser_sampling():
    """Supers land on cuts, so cuts get extra frames — that is the whole point."""
    cfg = TextConfig(sample_interval=5.0)
    sparse = plan_timestamps(20.0, [], cfg)
    dense = plan_timestamps(20.0, [7.0], cfg)
    assert len(dense) > len(sparse)
    assert any(7.0 < t < 8.5 for t in dense)


def test_frames_are_never_closer_than_the_minimum_gap():
    cfg = TextConfig(sample_interval=1.0, min_frame_gap=0.5)
    stamps = plan_timestamps(10.0, [1.0, 1.05, 1.1, 1.2], cfg)
    assert all(b - a >= 0.5 - 1e-6 for a, b in zip(stamps, stamps[1:]))


def test_max_frames_widens_the_interval_instead_of_truncating():
    """A long master must not get all its coverage in the first two minutes."""
    cfg = TextConfig(sample_interval=1.0, max_frames=20, scene_followup_offsets=[])
    stamps = plan_timestamps(600.0, [], cfg)
    assert len(stamps) <= 20
    assert stamps[-1] > 500, "coverage should reach the end of the programme"
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert max(gaps) - min(gaps) < 0.01, "the widened grid should stay evenly spaced"


def test_a_ten_minute_clip_keeps_the_requested_interval():
    """The house case (2-10 min) must fit the budget without widening."""
    cfg = TextConfig()
    assert effective_interval(600.0, cfg) == cfg.sample_interval
    assert len(plan_timestamps(600.0, [], cfg)) <= cfg.max_frames


def test_scene_followups_cannot_starve_the_baseline_grid():
    """A cut-heavy opening must not eat the whole frame budget."""
    cfg = TextConfig(sample_interval=2.0, max_frames=60)
    cuts = [i * 0.5 for i in range(200)]        # 100s of relentless cutting
    stamps = plan_timestamps(600.0, cuts, cfg)
    assert len(stamps) <= cfg.max_frames
    assert max(stamps) > 500, "sampling must still reach the end of the clip"


def test_timestamps_stay_inside_the_clip():
    cfg = TextConfig(sample_interval=1.0)
    stamps = plan_timestamps(5.0, [4.9], cfg)
    assert all(0.0 <= t < 5.0 for t in stamps)


@pytest.mark.parametrize("duration", [0.0, -1.0])
def test_zero_length_input_plans_nothing(duration):
    assert plan_timestamps(duration, [], TextConfig()) == []


@pytest.mark.parametrize(
    "height,expected",
    [(720, 2.0), (1080, 1440 / 1080), (1440, 1.0), (2160, 1.0)],
)
def test_ocr_scale_normalises_toward_the_target_height(height, expected):
    """720p is upscaled, 1080p less so, and large frames are never shrunk."""
    assert ocr_scale(height, TextConfig()) == pytest.approx(expected)


def test_neighbours_are_found_only_on_the_same_line():
    """Proper-noun detection depends on knowing which words share a line."""
    from burninghouse_qc.detectors.text import OcrWord, neighbours_on_line

    words = [
        OcrWord("Simon", 95.0, (0, 0, 10, 10), line=(1, 1, 1)),
        OcrWord("Gullery", 91.0, (0, 0, 10, 10), line=(1, 1, 1)),
        OcrWord("Director", 90.0, (0, 0, 10, 10), line=(1, 1, 2)),
    ]
    assert neighbours_on_line(words, 1) == ["Simon"]
    assert neighbours_on_line(words, 0) == ["Gullery"]
    assert neighbours_on_line(words, 2) == [], "a different line is not a neighbour"
