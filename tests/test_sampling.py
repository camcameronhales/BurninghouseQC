"""Frame sampling plan — the thoroughness/runtime trade-off from SPEC.md §6."""

import pytest

from burninghouse_qc.config import TextConfig
from burninghouse_qc.detectors.text import plan_timestamps


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


def test_max_frames_thins_evenly_instead_of_truncating():
    """A 90-minute master must not get all its coverage in the first two minutes."""
    cfg = TextConfig(sample_interval=1.0, max_frames=20, scene_followup_offsets=[])
    stamps = plan_timestamps(600.0, [], cfg)
    assert len(stamps) == 20
    assert stamps[-1] > 500, "coverage should reach the end of the programme"


def test_timestamps_stay_inside_the_clip():
    cfg = TextConfig(sample_interval=1.0)
    stamps = plan_timestamps(5.0, [4.9], cfg)
    assert all(0.0 <= t < 5.0 for t in stamps)


@pytest.mark.parametrize("duration", [0.0, -1.0])
def test_zero_length_input_plans_nothing(duration):
    assert plan_timestamps(duration, [], TextConfig()) == []
