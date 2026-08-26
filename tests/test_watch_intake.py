"""File intake: which files we pick up, and when we decide a render is done."""

import pytest

from burninghouse_qc.config import WatcherConfig
from burninghouse_qc.stability import is_candidate, wait_until_stable


@pytest.mark.parametrize(
    "name,expected",
    [
        ("master.mov", True),
        ("master.MP4", True),
        ("master.mxf", False),          # not a house delivery format by default
        ("master.mov.tmp", False),      # still being written
        ("master.part", False),
        ("notes.txt", False),
        (".DS_Store", False),
        ("._master.mov", False),        # macOS resource fork
        ("_qc_master.mov", False),      # our own scratch naming
    ],
)
def test_is_candidate(tmp_path, name, expected):
    assert is_candidate(tmp_path / name, WatcherConfig()) is expected


def test_extra_formats_are_one_config_line_away(tmp_path):
    """mp4/mov are the house formats; anything FFmpeg reads can be added."""
    cfg = WatcherConfig(video_extensions=[".mov", ".mp4", ".mxf"])
    assert is_candidate(tmp_path / "master.mxf", cfg) is True


class FakeClock:
    """Drives wait_until_stable without real sleeping."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_growing_file_is_not_picked_up_until_it_stops(tmp_path):
    """A render that is still writing must never be QC'd mid-write."""
    cfg = WatcherConfig(poll_interval=1.0, stability_checks=3, stability_timeout=60)
    path = tmp_path / "render.mov"
    path.write_bytes(b"0" * 100)

    clock = FakeClock()
    writes = {"count": 0}
    real_sleep = clock.sleep

    def sleep_and_maybe_grow(seconds):
        real_sleep(seconds)
        # Grow for the first few polls, then stop, as a finishing render would.
        if writes["count"] < 4:
            writes["count"] += 1
            with path.open("ab") as fh:
                fh.write(b"0" * 100)

    assert wait_until_stable(path, cfg, sleep=sleep_and_maybe_grow, now=clock.now) is True
    assert writes["count"] == 4, "should have kept waiting while the file grew"


def test_stable_file_is_accepted(tmp_path):
    cfg = WatcherConfig(poll_interval=1.0, stability_checks=2, stability_timeout=60)
    path = tmp_path / "render.mov"
    path.write_bytes(b"0" * 100)
    clock = FakeClock()
    assert wait_until_stable(path, cfg, sleep=clock.sleep, now=clock.now) is True


def test_zero_byte_file_is_never_considered_stable(tmp_path):
    """A touched-but-empty placeholder must not be treated as a finished render."""
    cfg = WatcherConfig(poll_interval=1.0, stability_checks=2, stability_timeout=10)
    path = tmp_path / "render.mov"
    path.touch()
    clock = FakeClock()
    assert wait_until_stable(path, cfg, sleep=clock.sleep, now=clock.now) is False


def test_vanished_file_returns_false(tmp_path):
    cfg = WatcherConfig(poll_interval=1.0, stability_checks=2, stability_timeout=60)
    path = tmp_path / "render.mov"
    path.write_bytes(b"0" * 100)
    clock = FakeClock()

    def sleep_then_delete(seconds):
        clock.sleep(seconds)
        path.unlink(missing_ok=True)

    assert wait_until_stable(path, cfg, sleep=sleep_then_delete, now=clock.now) is False


def test_never_settling_file_times_out(tmp_path):
    cfg = WatcherConfig(poll_interval=1.0, stability_checks=3, stability_timeout=5)
    path = tmp_path / "render.mov"
    path.write_bytes(b"0")
    clock = FakeClock()

    def sleep_and_grow(seconds):
        clock.sleep(seconds)
        with path.open("ab") as fh:
            fh.write(b"0")

    assert wait_until_stable(path, cfg, sleep=sleep_and_grow, now=clock.now) is False
