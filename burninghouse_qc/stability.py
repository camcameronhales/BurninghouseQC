"""Deciding when a render has actually finished writing.

'A file appeared' is not the same as 'a file is complete' — an NLE writes a
master over minutes. Two guards are used together: in-progress file extensions
are ignored outright, and every other candidate must report an identical size
and mtime across N consecutive polls before it is picked up.
"""

from __future__ import annotations

import enum
import time
from pathlib import Path

from .config import WatcherConfig


class Stability(enum.Enum):
    """Why the wait ended. These need telling apart in the log: a file that
    was never there is a different problem from one still being written."""

    STABLE = "stable"
    VANISHED = "vanished"
    TIMED_OUT = "timed_out"


def is_candidate(path: Path, cfg: WatcherConfig) -> bool:
    """True if this path looks like a finished-render candidate worth watching."""
    name = path.name
    if any(name.startswith(prefix) for prefix in cfg.ignore_prefixes):
        return False
    suffix = path.suffix.lower()
    if suffix in {ext.lower() for ext in cfg.ignore_extensions}:
        return False
    if suffix not in {ext.lower() for ext in cfg.video_extensions}:
        return False
    return True


def _sample(path: Path) -> tuple[int, float] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime


def wait_until_stable(
    path: Path,
    cfg: WatcherConfig,
    sleep=time.sleep,
    now=time.monotonic,
) -> Stability:
    """Block until `path` stops changing.

    `sleep`/`now` are injectable so the test suite can run this instantly.
    """
    deadline = now() + cfg.stability_timeout
    stable_count = 0
    last: tuple[int, float] | None = None

    while now() < deadline:
        current = _sample(path)
        if current is None:
            return Stability.VANISHED
        if current == last and current[0] > 0:
            stable_count += 1
            if stable_count >= cfg.stability_checks:
                return Stability.STABLE
        else:
            stable_count = 0
        last = current
        sleep(cfg.poll_interval)
    return Stability.TIMED_OUT
